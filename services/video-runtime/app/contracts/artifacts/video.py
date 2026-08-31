"""Video I2V/T2V prompt artifact (prompt side only).

Seedance/OM craft lives in prompt text (opener + timecodes + says + bans);
schema keeps machine fields for batch validation.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class VideoPromptDraft(BaseModel):
    shot_number: int = Field(description="Must match storyboard/scene shot_number")
    i2v_prompt: str = Field(
        description="Primary motion prompt (I2V or Seedance-style: opener, timecodes, says, bans)",
    )
    t2v_prompt: Optional[str] = Field(
        default=None,
        description="Optional T2V/Seedance dual prompt when pipeline supports it",
    )
    negative_prompt: str = Field(
        default="",
        description="Explicit bans: readable text, watermark, wrong identity, etc.",
    )


class VideoAgentDraft(BaseModel):
    """Args for write_video_prompts_artifact."""

    prompts: List[VideoPromptDraft] = Field(
        min_length=1,
        description="One prompt object per expected shot_number in this batch",
    )
    style: Optional[str] = Field(
        default=None,
        description="Optional batch style tag echoed from style_guide",
    )


class VideoArtifact(VideoAgentDraft):
    schema_version: int = 1
    artifact: Literal["video_prompts"] = "video_prompts"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
