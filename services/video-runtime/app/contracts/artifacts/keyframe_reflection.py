"""Keyframe reflection VLM artifact."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ReflectionItemDraft(BaseModel):
    shot_number: int = Field(description="Shot whose keyframe was reviewed")
    needs_regeneration: bool = Field(
        default=False,
        description="True if keyframe must be regenerated",
    )
    issues: List[str] = Field(
        default_factory=list,
        description="Concrete issues found (identity, composition, text, etc.)",
    )
    analysis_summary: str = Field(
        default="",
        description="Short VLM summary of the frame vs storyboard intent",
    )
    improved_description: Optional[str] = Field(
        default=None,
        description="Improved scene/prompt description when regenerating",
    )
    improvement_points: List[str] = Field(
        default_factory=list,
        description="Bullet fixes to apply on regen",
    )


class KeyframeReflectionAgentDraft(BaseModel):
    """Args for write_keyframe_reflection_artifact."""

    results: List[ReflectionItemDraft] = Field(
        min_length=1,
        description="Per-shot reflection results",
    )


class KeyframeReflectionArtifact(KeyframeReflectionAgentDraft):
    schema_version: int = 1
    artifact: Literal["keyframe_reflection"] = "keyframe_reflection"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
