"""Keyframe T2I prompt artifact (prompt side only)."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class KeyframePromptDraft(BaseModel):
    shot_number: int = Field(description="Shot this keyframe belongs to")
    t2i_prompt: str = Field(
        description="Full T2I prompt for the still/keyframe (composition, lighting, identity)",
    )
    frame_index: int = Field(
        default=0,
        description="0 = first/key frame; higher if multi-frame per shot",
    )


class KeyframeAgentDraft(BaseModel):
    """Args for write_keyframe_artifact."""

    prompts: List[KeyframePromptDraft] = Field(
        min_length=1,
        description="T2I prompts for expected shots in this batch",
    )


class KeyframeArtifact(KeyframeAgentDraft):
    schema_version: int = 1
    artifact: Literal["keyframes"] = "keyframes"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
