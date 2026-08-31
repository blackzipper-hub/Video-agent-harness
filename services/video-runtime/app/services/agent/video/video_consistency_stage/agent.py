from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.contracts.artifacts.video_consistency import (
    VideoConsistencyAgentDraft,
    VideoConsistencyArtifact,
)
from app.services.agent.stage_runtime.generic_stage import (
    load_artifact,
    make_write_json_tool,
    run_stage_deep_agent,
)
from prompts.prompt_config import PromptName


async def check_video_consistency_via_deep_agent(
    *,
    thread_id: str,
    run_id: str,
    input_paths: Dict[str, Any],
    media_parts: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[VideoConsistencyArtifact, List[Any]]:
    artifact_name = input_paths["artifact_name"]
    tool = make_write_json_tool(
        thread_id=thread_id,
        run_id=run_id,
        artifact_name=artifact_name,
        draft_model=VideoConsistencyAgentDraft,
        tool_name="write_video_consistency_artifact",
        stamp={
            "artifact": "video_consistency",
            "schema_version": 1,
            "thread_id": thread_id,
            "run_id": run_id,
        },
    )
    msgs = await run_stage_deep_agent(
        stage="video_consistency",
        agent_name="video_consistency_stage",
        prompt_name=PromptName.VIDEO_CONSISTENCY_CHECK,
        tools=[tool],
        human_text=(
            f"Run I2V consistency check. Read {input_paths['brief']} with read_file limit=2000. "
            f"Follow video-consistency-director. Call write_video_consistency_artifact. "
            f"Do not set passed (Program computes)."
        ),
        image_urls=input_paths.get("image_urls") or [],
        media_parts=media_parts or [],
        max_images=24,
        draft_model=VideoConsistencyAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, VideoConsistencyArtifact), msgs
