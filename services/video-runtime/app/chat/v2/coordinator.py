from __future__ import annotations

import re
from typing import Protocol

from fastapi.encoders import jsonable_encoder

from app.agents.planner import PlannerAgent
from app.chat.config import get_settings
from app.chat.services.agent.prompt_shield import (
    OutputLineBuffer,
    generate_shield_refusal_reply,
    redact_raw_uuids,
    validate_output_line,
)

from .capabilities import CapabilityRegistry
from .agent_trace import trace_agent_event
from .deep_agent_runtime import DeepAgentRuntime, V2AgentContext
from .skill_catalog import SkillCatalog
from .language import active_language_skill, output_language_instruction
from .tools import CoordinatorServices
from .workflows import active_workflow
from .token_usage import V2TokenUsageCallback, llm_attempt_audit


_ARTIFACT_MEDIA_URL_RE = re.compile(
    r"https?://[^\s<>()\[\]]+/files/(?:media|videos?|images?|audio)/[^\s<>()\[\]]+",
    re.IGNORECASE,
)


def hide_artifact_media_urls(text: str) -> str:
    """Keep internal artifact locations out of chat; the UI renders the artifact."""
    if not text:
        return text
    replaced = _ARTIFACT_MEDIA_URL_RE.sub("（产物已显示在右侧创作区）", text)
    # Avoid awkward labels such as "播放/下载地址：（产物……）" after replacement.
    replaced = re.sub(
        r"(?:播放\s*/?\s*下载|下载|播放|访问|文件)\s*(?:地址|链接|URL)?\s*[：:]\s*"
        r"(?=（产物已显示在右侧创作区）)",
        "",
        replaced,
        flags=re.IGNORECASE,
    )
    return replaced


class Coordinator(Protocol):
    async def run(self, run_id: str, observation: str) -> None: ...
    async def close(self) -> None: ...


class CoordinatorHost(CoordinatorServices, Protocol):
    async def emit_agent_event(self, run_id: str, event_type: str, payload: dict) -> None: ...
    async def record_agent_response(self, run_id: str, response: str) -> None: ...


class DeepAgentCoordinator:
    def __init__(
        self, runtime: DeepAgentRuntime, host: CoordinatorHost,
        capabilities: CapabilityRegistry,
        skills: SkillCatalog | None = None,
        script_runner=None,
    ):
        self.runtime = runtime
        self.host = host
        self.capabilities = capabilities
        self.skills = skills
        self.script_runner = script_runner
        self.planner = PlannerAgent(
            runtime, capabilities, skills, script_runner,
        )

    async def initialize(self) -> None:
        await self.planner.initialize(self.host)

    async def close(self) -> None:
        await self.planner.close()

    async def run(self, run_id: str, observation: str) -> None:
        settings = get_settings()
        snapshot = await self.host.project_snapshot(run_id)
        run = snapshot["run"]
        context = V2AgentContext(
            user_id=run["user_id"], project_id=run["project_id"],
            run_id=run_id, thread_id=run["thread_id"],
        )
        before_revision = run["current_revision"]
        parts: list[str] = []
        output_buffer = OutputLineBuffer()
        blocked_reason = ""
        explicit_skill_context = self._explicit_skill_context(snapshot)
        language_context = output_language_instruction(
            run.get("output_language"),
            user_request=run.get("objective", ""),
            language_skill=active_language_skill(run.get("activated_skills", [])),
        )
        trace_agent_event(
            settings,
            "coordination.started",
            run_id=run_id,
            model=settings.DEEP_AGENT_V2_MODEL,
            revision=before_revision,
            observation=observation,
            explicit_skills=self._explicit_skill_names(snapshot),
            task_count=len(snapshot.get("tasks", [])),
            artifact_count=len(snapshot.get("artifacts", [])),
        )
        invocation_message = "\n\n".join(
            part for part in (observation, language_context, explicit_skill_context)
            if part
        )
        usage_callback = V2TokenUsageCallback(
            self.host.repo, run_id=run_id, scope="coordinator",
        )
        with llm_attempt_audit(usage_callback.audit_attempt):
            event_stream = self.planner.stream(
                invocation_message, thread_id=run["thread_id"], context=context,
                callbacks=[usage_callback],
            )
            async for event in event_stream:
                name = event.get("event", "")
                if name == "on_chat_model_stream" and self._is_root_model_event(event):
                    text = self._content_text(
                        getattr(event.get("data", {}).get("chunk"), "content", "")
                    )
                    if text:
                        for line in output_buffer.push(text):
                            line = hide_artifact_media_urls(line)
                            ok, reason = validate_output_line(line)
                            if get_settings().SHIELD_OUTPUT_ENABLED and not ok:
                                if reason == "raw_uuid":
                                    line = redact_raw_uuids(line)
                                    parts.append(line)
                                    await self.host.emit_agent_event(
                                        run_id, "agent.message.delta", {"content": line},
                                    )
                                    continue
                                blocked_reason = reason
                                break
                            parts.append(line)
                            await self.host.emit_agent_event(
                                run_id, "agent.message.delta", {"content": line},
                            )
                        if blocked_reason:
                            break
                elif name == "on_tool_start":
                    tool_input = jsonable_encoder(event.get("data", {}).get("input"))
                    trace_agent_event(
                        settings,
                        "tool.started",
                        run_id=run_id,
                        tool=event.get("name"),
                        input=tool_input,
                        namespace=event.get("metadata", {}).get(
                            "langgraph_checkpoint_ns", ""
                        ),
                    )
                    await self.host.emit_agent_event(run_id, "agent.tool.started", {
                        "tool": event.get("name"),
                        "input": tool_input,
                    })
                elif name == "on_tool_end":
                    trace_agent_event(
                        settings,
                        "tool.completed",
                        run_id=run_id,
                        tool=event.get("name"),
                        output=jsonable_encoder(event.get("data", {}).get("output")),
                        namespace=event.get("metadata", {}).get(
                            "langgraph_checkpoint_ns", ""
                        ),
                    )
                    await self.host.emit_agent_event(
                        run_id, "agent.tool.completed", {"tool": event.get("name")},
                    )
        if not blocked_reason:
            for line in output_buffer.flush():
                line = hide_artifact_media_urls(line)
                ok, reason = validate_output_line(line)
                if get_settings().SHIELD_OUTPUT_ENABLED and not ok:
                    if reason == "raw_uuid":
                        line = redact_raw_uuids(line)
                        parts.append(line)
                        await self.host.emit_agent_event(
                            run_id, "agent.message.delta", {"content": line},
                        )
                        continue
                    blocked_reason = reason
                    break
                parts.append(line)
                await self.host.emit_agent_event(
                    run_id, "agent.message.delta", {"content": line},
                )
        if blocked_reason:
            response = await generate_shield_refusal_reply(
                user_snippet=observation,
                shield_reason=blocked_reason,
                layer="output",
            )
            await self.host.emit_agent_event(
                run_id, "agent.message.delta", {"content": response},
            )
        else:
            response = "".join(parts).strip()
        after = await self.host.project_snapshot(run_id)
        trace_agent_event(
            settings,
            "coordination.response",
            run_id=run_id,
            response=response,
            blocked_reason=blocked_reason or None,
            revision_before=before_revision,
            revision_after=after["run"]["current_revision"],
            run_status=after["run"]["status"],
        )
        if response and after["run"]["current_revision"] == before_revision:
            await self.host.record_agent_response(run_id, response)

    @staticmethod
    def _content_text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                item.get("text", "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        return ""

    @staticmethod
    def _is_root_model_event(event: dict) -> bool:
        namespace = str(
            event.get("metadata", {}).get("langgraph_checkpoint_ns", "")
        )
        return "|" not in namespace

    def _explicit_skill_context(self, snapshot: dict) -> str:
        if self.skills is None:
            return ""
        latest = next(
            (
                item.get("content", "")
                for item in reversed(snapshot.get("messages", []))
                if item.get("role") == "user"
            ),
            "",
        )
        if not latest:
            return ""
        requested = {
            match.group(1)
            for match in re.finditer(
                r"(?<![\w-])[$/]([a-z0-9][a-z0-9-]{0,63})(?![\w-])",
                latest.lower(),
            )
        }
        # Project locks are durable explicit activation. They survive a page
        # reload and prevent a globally installed Skill from silently changing
        # a project's selected creative method.
        requested.update(
            str(item.get("skill_id") or "")
            for item in snapshot.get("run", {}).get("skill_locks", [])
            if isinstance(item, dict) and item.get("enabled")
        )
        # A workflow confirmed through the approval UI may not be repeated in
        # the latest user message. Always inject the durable activated workflow
        # and any helper Skills declared by its frontmatter dependencies.
        activated = [
            str(name)
            for name in snapshot.get("run", {}).get("activated_skills", [])
            if name
        ]
        requested.update(activated)
        for name in list(requested):
            workflow = active_workflow([name])
            if workflow is not None:
                requested.update(workflow.skill_dependencies)
        requested.discard("")
        blocks = []
        for name in sorted(requested):
            if not self.skills.has(name):
                continue
            skill = self.skills.load(name)
            if not skill.metadata.enabled:
                continue
            blocks.append(
                f"Explicitly activated Skill `{name}` (user-selected or project-locked):\n\n"
                f"{skill.instructions}\n\n"
                f"Bundled resources: {self.skills.list_resources(name)}"
            )
        if not blocks:
            return ""
        return (
            "The user explicitly invoked the following Skills. Treat their instructions "
            "as the active workflow for this goal:\n\n"
            + "\n\n---\n\n".join(blocks)
        )

    @staticmethod
    def _explicit_skill_names(snapshot: dict) -> list[str]:
        latest = next(
            (
                item.get("content", "")
                for item in reversed(snapshot.get("messages", []))
                if item.get("role") == "user"
            ),
            "",
        )
        return sorted({
            match.group(1)
            for match in re.finditer(
                r"(?<![\w-])[$/]([a-z0-9][a-z0-9-]{0,63})(?![\w-])",
                latest.lower(),
            )
        })
