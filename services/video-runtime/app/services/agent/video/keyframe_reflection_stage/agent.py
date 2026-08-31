from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from app.contracts.artifacts.keyframe_reflection import KeyframeReflectionAgentDraft, KeyframeReflectionArtifact
from app.services.agent.stage_runtime.generic_stage import load_artifact, make_write_json_tool, run_stage_deep_agent
from prompts.prompt_config import PromptName

async def generate_keyframe_reflection_via_deep_agent(*, thread_id: str, run_id: str, input_paths: Dict[str, Any],
    detected_language: Optional[str] = None) -> Tuple[KeyframeReflectionArtifact, List[Any]]:
    expected = set(input_paths.get("expected_shot_numbers") or [])
    artifact_name = input_paths["artifact_name"]
    def extra(d: KeyframeReflectionAgentDraft) -> Optional[str]:
        got = {r.shot_number for r in d.results}
        if expected and got != expected:
            return f"shot_numbers {sorted(got)} != expected {sorted(expected)}"
        return None
    tool = make_write_json_tool(
        thread_id=thread_id, run_id=run_id, artifact_name=artifact_name,
        draft_model=KeyframeReflectionAgentDraft, tool_name="write_keyframe_reflection_artifact",
        extra_validate=extra,
        stamp={"artifact": "keyframe_reflection", "schema_version": 1, "thread_id": thread_id, "run_id": run_id},
    )
    msgs = await run_stage_deep_agent(
        stage="keyframe_reflection", agent_name="keyframe_reflection_stage",
        prompt_name=PromptName.VIDEO_SINGLE_KEYFRAME_REFLECTION_PROMPT, tools=[tool],
        human_text=(f"Run keyframe reflection. Read {input_paths['brief']}. "
                    f"Follow keyframe_reflection-director. Call write_keyframe_reflection_artifact. "
                    f"Do not regenerate images."),
        detected_language=detected_language,
        image_urls=input_paths.get("image_urls") or [],
        max_images=24,
        draft_model=KeyframeReflectionAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, KeyframeReflectionArtifact), msgs
