from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from app.contracts.artifacts.keyframe import KeyframeAgentDraft, KeyframeArtifact
from app.services.agent.stage_runtime.generic_stage import load_artifact, make_write_json_tool, run_stage_deep_agent
from prompts.prompt_config import PromptName

async def generate_keyframe_prompts_via_deep_agent(*, thread_id: str, run_id: str, input_paths: Dict[str, Any],
    detected_language: Optional[str] = None) -> Tuple[KeyframeArtifact, List[Any]]:
    expected = set(input_paths.get("expected_shot_numbers") or [])
    artifact_name = input_paths["artifact_name"]
    def extra(d: KeyframeAgentDraft) -> Optional[str]:
        got = {p.shot_number for p in d.prompts}
        if expected and not expected.issubset(got) and got != expected:
            # allow subset only if exact match preferred
            if got != expected:
                return f"shot_numbers {sorted(got)} != expected {sorted(expected)}"
        for p in d.prompts:
            if not (p.t2i_prompt or "").strip():
                return f"shot {p.shot_number} empty t2i_prompt"
        return None
    tool = make_write_json_tool(
        thread_id=thread_id, run_id=run_id, artifact_name=artifact_name,
        draft_model=KeyframeAgentDraft, tool_name="write_keyframe_artifact",
        extra_validate=extra,
        stamp={"artifact": "keyframes", "schema_version": 1, "thread_id": thread_id, "run_id": run_id},
    )
    msgs = await run_stage_deep_agent(
        stage="keyframe", agent_name="keyframe_stage",
        prompt_name=PromptName.VIDEO_KEYFRAME_GENERATION_PROMPT_BATCH_GENERATION, tools=[tool],
        human_text=(
            f"Read brief. Follow keyframe-director. Call write_keyframe_artifact. "
            f"Read {input_paths['brief']} with read_file limit=2000."
        ),
        detected_language=detected_language,
        image_urls=input_paths.get("image_urls") or [],
        max_images=24,
        draft_model=KeyframeAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, KeyframeArtifact), msgs
