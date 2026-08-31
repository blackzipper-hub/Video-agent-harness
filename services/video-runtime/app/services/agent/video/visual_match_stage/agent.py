from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from app.contracts.artifacts.visual_match import VisualMatchAgentDraft, VisualMatchArtifact
from app.services.agent.stage_runtime.generic_stage import load_artifact, make_write_json_tool, run_stage_deep_agent
from prompts.prompt_config import PromptName

async def generate_visual_match_via_deep_agent(*, thread_id: str, run_id: str, input_paths: Dict[str, Any],
    detected_language: Optional[str] = None) -> Tuple[VisualMatchArtifact, List[Any]]:
    expected = set(input_paths.get("expected_scene_numbers") or [])
    artifact_name = input_paths["artifact_name"]
    def extra(d: VisualMatchAgentDraft) -> Optional[str]:
        got = {s.scene_number for s in d.scenes}
        if expected and got != expected:
            return f"scene_numbers {sorted(got)} != expected {sorted(expected)}"
        return None
    tool = make_write_json_tool(
        thread_id=thread_id, run_id=run_id, artifact_name=artifact_name,
        draft_model=VisualMatchAgentDraft, tool_name="write_visual_match_artifact",
        extra_validate=extra,
        stamp={"artifact": "visual_match", "schema_version": 1, "thread_id": thread_id, "run_id": run_id},
    )
    msgs = await run_stage_deep_agent(
        stage="visual_match", agent_name="visual_match_stage",
        prompt_name=PromptName.VIDEO_VISUAL_ELEMENTS_MATCHING, tools=[tool],
        human_text=(f"Run visual elements matching. Read {input_paths['brief']}. "
                    f"Follow visual_match-director. Call write_visual_match_artifact."),
        detected_language=detected_language,
        draft_model=VisualMatchAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, VisualMatchArtifact), msgs
