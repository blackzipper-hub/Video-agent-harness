"""First-frame revision artifact."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class FirstFrameItemDraft(BaseModel):
    shot_number: int = Field(description="Shot under review")
    has_violation: bool = Field(
        default=False,
        description="True if first frame / storyboard fields need revision",
    )
    violation_type: Optional[str] = Field(
        default=None,
        description="Short violation tag when has_violation, e.g. readable_ui / identity_drift",
    )
    revised_scene_description: Optional[str] = Field(
        default=None, description="Replacement scene_description when revising"
    )
    revised_camera_movement: Optional[str] = Field(
        default=None, description="Replacement camera_movement when revising"
    )
    revised_shot_type: Optional[str] = Field(
        default=None, description="Replacement shot_type when revising"
    )
    revised_subject_angle: Optional[str] = Field(
        default=None, description="Replacement subject_angle when revising"
    )
    revised_subject_pose: Optional[str] = Field(
        default=None, description="Replacement subject_pose when revising"
    )
    revised_generation_mode: Optional[str] = Field(
        default=None, description="Replacement generation_mode when revising"
    )


class FirstFrameAgentDraft(BaseModel):
    """Args for write_first_frame_artifact."""

    shots: List[FirstFrameItemDraft] = Field(
        min_length=1,
        description="Per-shot first-frame review / revision rows",
    )


class FirstFrameArtifact(FirstFrameAgentDraft):
    schema_version: int = 1
    artifact: Literal["first_frame"] = "first_frame"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
