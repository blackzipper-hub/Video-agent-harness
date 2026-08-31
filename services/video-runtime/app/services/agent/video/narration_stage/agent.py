from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from app.contracts.artifacts.narration import NarrationAgentDraft, NarrationArtifact
from app.services.agent.stage_runtime.generic_stage import load_artifact, make_write_json_tool, run_stage_deep_agent
from prompts.prompt_config import PromptName

async def generate_narration_plan_via_deep_agent(*, thread_id: str, run_id: str, input_paths: Dict[str, Any],
    detected_language: Optional[str] = None) -> Tuple[NarrationArtifact, List[Any]]:
    artifact_name = input_paths["artifact_name"]
    tool = make_write_json_tool(
        thread_id=thread_id, run_id=run_id, artifact_name=artifact_name,
        draft_model=NarrationAgentDraft, tool_name="write_narration_artifact",
        stamp={"artifact": "narration", "schema_version": 1, "thread_id": thread_id, "run_id": run_id},
    )
    msgs = await run_stage_deep_agent(
        stage="narration", agent_name="narration_stage",
        prompt_name=PromptName.VIDEO_NARRATION_GENERATION, tools=[tool],
        human_text=(f"Plan narration voice delivery. Read {input_paths['brief']}. "
                    f"Follow narration-director. Call write_narration_artifact. Do not synthesize audio."),
        detected_language=detected_language,
        draft_model=NarrationAgentDraft, artifact_name=artifact_name,
        thread_id=thread_id, run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, NarrationArtifact), msgs
