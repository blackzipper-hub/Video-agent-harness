"""Video I2V consistency VLM artifact."""
from __future__ import annotations

from typing import Literal, Optional

from app.schemas.video_llm import (
    PerCharacterFirstFrameResult,
    VideoConsistencyCheckResult,
)

# Ensure nested schema is resolved for deep-agent tool args_schema.
_ = PerCharacterFirstFrameResult


class VideoConsistencyAgentDraft(VideoConsistencyCheckResult):
    """Args for write_video_consistency_artifact (passed computed by Program)."""


class VideoConsistencyArtifact(VideoConsistencyAgentDraft):
    schema_version: int = 1
    artifact: Literal["video_consistency"] = "video_consistency"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None


VideoConsistencyAgentDraft.model_rebuild()
VideoConsistencyArtifact.model_rebuild()
