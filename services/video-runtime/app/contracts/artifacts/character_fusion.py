"""Character fusion plan artifact (image tools stay Program)."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class FusionGroupDraft(BaseModel):
    fusion_key: str = Field(description="Stable key for this fusion group")
    character_ids: List[str] = Field(
        min_length=1,
        description="Character ids to fuse into one reference plate",
    )
    reason: str = Field(default="", description="Why these ids must share a fused plate")


class CharacterFusionAgentDraft(BaseModel):
    """Args for write_character_fusion_artifact."""

    main_fusions: List[FusionGroupDraft] = Field(
        default_factory=list,
        description="Primary identity fusion groups",
    )
    multiview_fusions: List[FusionGroupDraft] = Field(
        default_factory=list,
        description="Multi-view / angle fusion groups if needed",
    )


class CharacterFusionArtifact(CharacterFusionAgentDraft):
    schema_version: int = 1
    artifact: Literal["fusion"] = "fusion"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
