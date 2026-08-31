"""Narration voice plan artifact (TTS tools stay Program)."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class NarrationShotDraft(BaseModel):
    shot_number: int = Field(description="Shot this narration line covers")
    narration_text: str = Field(description="Off-screen TTS text for this shot")
    voice_hint: str = Field(
        default="",
        description="Tone/pace hint for TTS, e.g. warm urgent whisper",
    )
    speaker_gender: Optional[Literal["f", "m"]] = Field(
        default=None,
        description="TTS gender: f|m when narration_text is non-empty",
    )


class NarrationAgentDraft(BaseModel):
    """Args for write_narration_artifact."""

    shots: List[NarrationShotDraft] = Field(
        default_factory=list,
        description="Per-shot narration plan; omit empty shots or leave text empty",
    )


class NarrationArtifact(NarrationAgentDraft):
    schema_version: int = 1
    artifact: Literal["narration"] = "narration"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
