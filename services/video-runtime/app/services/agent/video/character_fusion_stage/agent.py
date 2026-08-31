from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from app.contracts.artifacts.character_fusion import CharacterFusionAgentDraft, CharacterFusionArtifact
from app.services.agent.stage_runtime.generic_stage import load_artifact, make_write_json_tool, run_stage_deep_agent
from prompts.prompt_config import PromptName

async def generate_fusion_plan_via_deep_agent(*, thread_id: str, run_id: str, input_paths: Dict[str, Any],
    detected_language: Optional[str] = None) -> Tuple[CharacterFusionArtifact, List[Any]]:
    artifact_name = input_paths["artifact_name"]
    tool = make_write_json_tool(
        thread_id=thread_id, run_id=run_id, artifact_name=artifact_name,
        draft_model=CharacterFusionAgentDraft, tool_name="write_character_fusion_artifact",
        stamp={"artifact": "fusion", "schema_version": 1, "thread_id": thread_id, "run_id": run_id},
    )
    msgs = await run_stage_deep_agent(
        stage="character_fusion", agent_name="character_fusion_stage",
        prompt_name=PromptName.VIDEO_CHARACTER_FUSION_IMAGE_GENERATION, tools=[tool],
        human_text=(f"Plan character fusion groups. Read {input_paths['brief']}. "
                    f"Follow character-fusion-director. Call write_character_fusion_artifact. Do not generate images."),
        detected_language=detected_language,
        draft_model=CharacterFusionAgentDraft, artifact_name=artifact_name,
        thread_id=thread_id, run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, CharacterFusionArtifact), msgs
