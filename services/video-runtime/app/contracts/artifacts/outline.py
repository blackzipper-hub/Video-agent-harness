"""Outline artifact (disk) + agent draft shape aligned with StoryOutlineForLLMMode."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.contracts.llm.outline import (
    EnhancementCue,
    OutlineChapterLLM,
    OutlineLLMOutput,
    OutlineStructureLLM,
)


class OutlineChapterArtifact(BaseModel):
    id: str = Field(description="Stable chapter id, e.g. ch_0 / chapter_1")
    order: int = Field(description="0-based chapter order")
    title: str = Field(description="Chapter title (short, punchy)")
    description: str = Field(
        description="What happens in this chapter: conflict → turn → landing; not mood-only",
    )
    duration: Optional[float] = Field(
        default=None,
        description="Seconds; required for video_driven (sum == total_duration). Audio_driven may omit.",
    )
    audio_segment_indices: Optional[List[int]] = Field(
        default=None,
        description="Audio-driven only: which audio segment indices this chapter covers",
    )
    enhancement_cues: List[EnhancementCue] = Field(
        default_factory=list,
        description="Optional overlay/broll/diagram hints for this chapter",
    )

    @field_validator("order", mode="before")
    @classmethod
    def _coerce_order(cls, v: object) -> int:
        return int(v) if v is not None else 0


class OutlineArtifact(BaseModel):
    """Canonical on-disk outline contract (after Program stamps ids)."""

    schema_version: int = 1
    artifact: Literal["outline"] = "outline"
    story_outline_uuid: Optional[str] = None
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    mode: Literal["video_driven", "audio_driven"] = "video_driven"
    title: str
    theme: str
    description: str
    key_message: str
    total_duration: int
    style_guide: str
    user_message: Optional[str] = ""
    chapters: List[OutlineChapterArtifact] = Field(min_length=1)

    def to_llm_mode(self) -> OutlineLLMOutput:
        """Bridge to existing normalize / convert helpers."""
        return OutlineLLMOutput(
            title=self.title,
            theme=self.theme,
            description=self.description,
            key_message=self.key_message,
            total_duration=int(self.total_duration),
            style_guide=self.style_guide,
            user_message=self.user_message or "",
            structure=OutlineStructureLLM(
                chapters=[
                    OutlineChapterLLM(
                        id=ch.id,
                        order=ch.order,
                        title=ch.title,
                        description=ch.description,
                        duration=ch.duration,
                        audio_segment_indices=ch.audio_segment_indices,
                        enhancement_cues=ch.enhancement_cues or None,
                    )
                    for ch in self.chapters
                ]
            ),
        )


class OutlineAgentDraft(BaseModel):
    """Args for write_outline_artifact."""

    title: str = Field(description="Working title of the piece")
    theme: str = Field(description="Core theme / logline theme word")
    description: str = Field(description="1–3 sentence synopsis of the whole piece")
    key_message: str = Field(description="Takeaway the audience should feel/remember")
    total_duration: int = Field(description="Total seconds; chapter durations must sum to this (video_driven)")
    style_guide: str = Field(
        description="Visual/tone bible for downstream (lighting, palette, pacing words)",
    )
    user_message: Optional[str] = Field(
        default="",
        description="Optional echo/notes from user request",
    )
    chapters: List[OutlineChapterArtifact] = Field(
        min_length=1,
        description="Ordered chapters; honor max/recommended chapter counts from analysis_brief",
    )
