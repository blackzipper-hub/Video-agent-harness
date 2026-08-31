from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.contracts.artifacts.image_consistency import (
    ImageConsistencyAgentDraft,
    ImageConsistencyArtifact,
)
from app.services.agent.stage_runtime.generic_stage import (
    load_artifact,
    make_write_json_tool,
    run_stage_deep_agent,
)
from prompts.prompt_config import PromptName


async def check_image_consistency_via_deep_agent(
    *,
    thread_id: str,
    run_id: str,
    input_paths: Dict[str, Any],
) -> Tuple[ImageConsistencyArtifact, List[Any]]:
    artifact_name = input_paths["artifact_name"]
    tool = make_write_json_tool(
        thread_id=thread_id,
        run_id=run_id,
        artifact_name=artifact_name,
        draft_model=ImageConsistencyAgentDraft,
        tool_name="write_image_consistency_artifact",
        stamp={
            "artifact": "image_consistency",
            "schema_version": 1,
            "thread_id": thread_id,
            "run_id": run_id,
        },
    )
    msgs = await run_stage_deep_agent(
        stage="image_consistency",
        agent_name="image_consistency_stage",
        prompt_name=PromptName.IMAGE_CHARACTER_CONSISTENCY_CHECK,
        tools=[tool],
        human_text=(
            f"Run I2I character consistency. Read {input_paths['brief']} with read_file limit=2000. "
            f"Follow image-consistency-director. Call write_image_consistency_artifact."
        ),
        image_urls=input_paths.get("image_urls") or [],
        max_images=24,
        draft_model=ImageConsistencyAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, ImageConsistencyArtifact), msgs
