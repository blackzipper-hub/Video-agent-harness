"""I2I character consistency VLM artifact."""
from __future__ import annotations

from typing import Literal, Optional

from app.schemas.video_llm import (
    CharacterConsistencyResult,
    PerCharacterConsistencyResult,
)

# Nested schema must resolve before deep-agent tool args_schema / model_validate.
_ = PerCharacterConsistencyResult
CharacterConsistencyResult.model_rebuild()


class ImageConsistencyAgentDraft(CharacterConsistencyResult):
    """Args for write_image_consistency_artifact."""


class ImageConsistencyArtifact(ImageConsistencyAgentDraft):
    schema_version: int = 1
    artifact: Literal["image_consistency"] = "image_consistency"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None


ImageConsistencyAgentDraft.model_rebuild()
ImageConsistencyArtifact.model_rebuild()
