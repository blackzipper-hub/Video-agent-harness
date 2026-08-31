from __future__ import annotations

import os
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.chat.config import Settings
from app.chat.v2.skill_catalog import SkillCatalog


class V2AgentContext(BaseModel):
    user_id: str
    project_id: str
    run_id: str
    thread_id: str


class DeepAgentRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.graph = None
        self.checkpointer = None
        self._checkpointer_context: AbstractAsyncContextManager | None = None
        self.skill_files: dict = {}

    async def initialize(self, tools: list[Any]) -> None:
        from deepagents import (
            GeneralPurposeSubagentProfile, HarnessProfile, create_deep_agent,
            register_harness_profile,
        )
        from deepagents.backends import StateBackend
        from deepagents.backends.utils import create_file_data
        from deepagents.middleware import FilesystemPermission
        from app.llm.openai_failover import FailoverChatOpenAI
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        self.skill_files = self._load_skill_files(
            self.settings.deep_agent_skill_root_paths(), create_file_data
        )
        checkpoint_url = (
            self.settings.DEEP_AGENT_V2_CHECKPOINT_DATABASE_URL
            or self.settings.DEEP_AGENT_V2_DATABASE_URL
        )
        if checkpoint_url:
            self._checkpointer_context = AsyncPostgresSaver.from_conn_string(checkpoint_url)
            self.checkpointer = await self._checkpointer_context.__aenter__()
            await self.checkpointer.setup()
        else:
            self.checkpointer = MemorySaver()
        api_key = (
            self.settings.DEEP_AGENT_V2_OPENAI_API_KEY
            or os.getenv("OPENAI_API_KEY", "")
        ).strip()
        model = FailoverChatOpenAI(
            model=self.settings.DEEP_AGENT_V2_MODEL,
            api_key=api_key,
            fallback_api_key=self.settings.OPENAI_API_KEY_FALLBACK,
            base_url=self.settings.DEEP_AGENT_V2_OPENAI_BASE_URL,
            timeout=self.settings.DEEP_AGENT_V2_TIMEOUT_SECONDS,
            temperature=0,
            use_responses_api=self.settings.DEEP_AGENT_V2_MODEL.startswith("gpt-5"),
        )
        register_harness_profile(
            f"openai:{self.settings.DEEP_AGENT_V2_MODEL}",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(
                    enabled=self.settings.DEEP_AGENT_V2_SUBAGENTS_ENABLED
                )
            ),
        )
        self.graph = create_deep_agent(
            model=model, tools=tools, system_prompt=self._system_prompt(),
            skills=["/skills/"], backend=StateBackend(),
            permissions=[
                FilesystemPermission(
                    operations=["write"], paths=["/skills/**"], mode="deny",
                ),
                FilesystemPermission(
                    operations=["read"], paths=["/skills/**"], mode="allow",
                ),
            ],
            context_schema=V2AgentContext, checkpointer=self.checkpointer,
            name="cuti_deep_agent_v2",
        )

    async def close(self) -> None:
        if self._checkpointer_context:
            await self._checkpointer_context.__aexit__(None, None, None)

    def reload_skill_files(self) -> int:
        from deepagents.backends.utils import create_file_data

        files = self._load_skill_files(
            self.settings.deep_agent_skill_root_paths(),
            create_file_data,
        )
        self.skill_files = files
        return len(files)

    def invocation_input(self, message: str) -> dict:
        value: dict[str, Any] = {"messages": [{"role": "user", "content": message}]}
        if self.skill_files:
            value["files"] = self.skill_files
        return value

    def config(self, thread_id: str, callbacks: list[Any] | None = None) -> dict:
        value = {
            "configurable": {
                "thread_id": f"{self.settings.DEEP_AGENT_V2_CHECKPOINT_NAMESPACE}:{thread_id}",
                "checkpoint_ns": self.settings.DEEP_AGENT_V2_CHECKPOINT_NAMESPACE,
            },
            "recursion_limit": 9999,
        }
        if callbacks:
            value["callbacks"] = callbacks
        return value

    @staticmethod
    def _load_skill_files(roots: list[Path], create_file_data) -> dict:
        files = {}
        catalog = SkillCatalog(roots)
        for metadata in catalog.discover():
            if not metadata.enabled:
                continue
            path = metadata.path
            text = path.read_text(encoding="utf-8")
            files[f"/skills/{path.parent.name}/SKILL.md"] = create_file_data(text)
        return files

    def _system_prompt(self) -> str:
        limit = max(1, int(self.settings.DEEP_AGENT_V2_MAX_PARALLEL_GENERATION_TASKS))
        return f"""You are the Cuti Deep Agent V2 coordinator.
Skills are Codex-style guides. User-selectable **workflow** skills (metadata.kind=workflow)
define which pipeline to run. Non-workflow skills are helpers/bridges.

Workflow selection (mandatory before stage work):
1. Call list_skills. Prefer skills whose metadata.kind is "workflow":
   workflow-keyframe-pipeline, workflow-short-drama, workflow-direct-video,
   open-montage, seedance2, seedance-mv.
   For MV / song / beat-sync / 角色唱这首歌, prefer seedance-mv over seedance2.
2. If the user wrote `$name` / `/name` for a workflow, that workflow is confirmed —
   load it and follow its pipeline.
3. If no workflow is confirmed yet, pick the best match by description, then pause with
   waiting_for_input=true and interruption.category=workflow_confirm,
   interruption.skill_name=<chosen workflow>, requires_confirmation=true,
   explaining why that workflow fits and asking the user to confirm with
   `$<skill-name>` or 确认.
4. Until a workflow is activated on the run, you must NOT propose generation-stage
   tasks (outline/character/scene/shot/keyframe/shot.video/assemble/provider/atomics/…).
   Post-production on an existing selected video is workflow-free: media.transcribe,
   subtitle.compose, media.subtitle_burn, media.hyperframes_caption, media.extract_frame,
   media.concat, media.audio_trim, media.audio_analyze, and media.mix_audio may run
   without selecting a generation workflow. actions.suggest is also workflow-free.
5. multimodal-video-director is disabled — never load or follow it.
6. Kit creative directors under services/agent/kit are stage-internal only; they are
   NOT listed as user workflows and must not be proposed as `$kit/...` skills.

After a workflow is active, load its SKILL.md and schedule capabilities in that
workflow's order. Inject workflow_mode / shot_workflow_mode / activated_workflow on
every stage task (host also injects if missing). Parallelize only independent tasks
whose upstream artifacts already exist.

For every image/keyframe task that applies one or more non-workflow Skills, you MUST
populate PlannedTask.constraint_contract. Read the applicable Skill instructions and
classify them semantically; do not rely on a fixed list of style names or keywords:
- hard_constraints: only requirements stated as mandatory, exact, prohibited,
  measurable, invariant, or required output fields. Give each a stable constraint_id,
  source skill_id, exact enforceable text, required_terms that must survive into the
  vendor prompt, and required_fields for named structured values.
- guidance: preferences, examples, recommendations, and discretionary creative advice.
Use an empty hard_constraints list when the Skill genuinely has no mandatory rule,
but still provide the contract. The host rejects image stages whose applied Skills lack
this classification. The Stage Agent may write the creative prompt; the Harness will
deterministically merge and validate the frozen hard constraints before calling the
image provider.

If the user explicitly mentions `$skill-name` for a non-workflow helper, load it too.
Use run_skill_script for bundled scripts and skill_http_get for absolute http(s) URLs.
Never call skill_http_get with file:// or large_tool_results handles — use
get_project_snapshot instead.
If load_skill reports has_executable_contract=true, prefer propose_plan_patch with
THAT Skill's own capability id. Instruction-only Skills coordinate enabled capabilities;
they do not themselves create an artifact.
Platform atomics: atomic.text/image/music/video.generate. Prefer them under
workflow-direct-video or seedance2 as those workflows specify.
video.pipeline.generate remains paused. Prefer discrete stage capabilities under the
active workflow. Host bridges include api.provider.generate, api.ark_protocol.generate,
media.concat, media.extract_frame, media.audio_trim, media.audio_analyze,
media.mix_audio, media.transcribe, subtitle.compose, media.subtitle_burn,
media.hyperframes_caption, open_montage.tool.invoke.
When seedance2 runs scripts/seedance.py without a real ARK_API_KEY, the platform
redirects Ark HTTP to the protocol bridge — do not edit seedance2.
Provider error rule: category=provider_pending_timeout or retryable=true means the
remote job is still running — do not treat as hard failure or switch providers.
Hard concurrency rule: at most {limit} video-generation tasks may be active at once.
Never propose more than the remaining slots in one revision; batch long films.
Post-production media capabilities do not count against this limit.
After each task finishes, inspect the snapshot and revise the plan. Never mark a goal
satisfied merely because work was queued.
Generated files are durable artifacts rendered in the creation workspace. Never print
their raw URI, local file URL, playback URL, or download URL in chat. Say that the
result is ready and visible in the right-hand creation workspace instead.
For non-workflow Skill-required pauses use interruption.category=skill_required with
exact skill_name, resource, and quoted skill_policy. Failures use category=failure or
blocked. Ordinary commentary never pauses a run.
Submit executable work only through propose_plan_patch. Never invent artifacts,
disabled capabilities, completion, or downstream contracts."""
