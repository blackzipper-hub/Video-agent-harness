"""Visual elements matching artifact."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class MatchedItemDraft(BaseModel):
    character_index: int = Field(description="Index into cast / visual element list")
    character_type: str = Field(default="character", description="character | prop | …")
    confidence: float = Field(
        default=0.8,
        description="Match confidence 0–1",
    )
    match_reason: str = Field(default="", description="Why this element belongs in the scene")


class SceneMatchDraft(BaseModel):
    scene_number: int = Field(description="Scene being matched")
    matched_characters: List[MatchedItemDraft] = Field(
        default_factory=list,
        description="Visual elements present in this scene",
    )


class VisualMatchAgentDraft(BaseModel):
    """Args for write_visual_match_artifact."""

    scenes: List[SceneMatchDraft] = Field(
        min_length=1,
        description="Per-scene visual element matches",
    )


class VisualMatchArtifact(VisualMatchAgentDraft):
    schema_version: int = 1
    artifact: Literal["visual_match"] = "visual_match"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
