from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from app.contracts.artifacts.first_frame import FirstFrameAgentDraft, FirstFrameArtifact
from app.services.agent.stage_runtime.generic_stage import load_artifact, make_write_json_tool, run_stage_deep_agent
from prompts.prompt_config import PromptName

async def generate_first_frame_via_deep_agent(*, thread_id: str, run_id: str, input_paths: Dict[str, Any],
    detected_language: Optional[str] = None) -> Tuple[FirstFrameArtifact, List[Any]]:
    expected = set(input_paths.get("expected_shot_numbers") or [])
    artifact_name = input_paths["artifact_name"]
    def extra(d: FirstFrameAgentDraft) -> Optional[str]:
        got = {s.shot_number for s in d.shots}
        if expected and got != expected:
            return f"shot_numbers {sorted(got)} != expected {sorted(expected)}"
        return None
    tool = make_write_json_tool(
        thread_id=thread_id, run_id=run_id, artifact_name=artifact_name,
        draft_model=FirstFrameAgentDraft, tool_name="write_first_frame_artifact",
        extra_validate=extra,
        stamp={"artifact": "first_frame", "schema_version": 1, "thread_id": thread_id, "run_id": run_id},
    )
    msgs = await run_stage_deep_agent(
        stage="first_frame", agent_name="first_frame_stage",
        prompt_name=PromptName.VIDEO_STORYBOARD_FIRST_FRAME_REVISION, tools=[tool],
        human_text=(f"Run first-frame revision. Read {input_paths['brief']} with read_file limit=2000. "
                    f"Follow first-frame-director. For every expected shot_number call "
                    f"write_first_frame_artifact (has_violation true/false). Do not stop after only reading."),
        detected_language=detected_language,
        draft_model=FirstFrameAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, FirstFrameArtifact), msgs
