"""Prior input for outline stage (exported from DB / analysis)."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AnalysisBriefArtifact(BaseModel):
    """Machine facts for outline-director. No mustache — agent reads this JSON."""

    schema_version: int = 1
    artifact: Literal["analysis_brief"] = "analysis_brief"
    mode: Literal["video_driven", "audio_driven"] = "video_driven"
    content_category: str = Field(default="default", description="default | short_drama | product_launch | …")
    user_input: str = ""
    target_duration_seconds: float = Field(description="Chapters must sum to this (video) or match audio length")
    main_character: str = ""
    purpose: str = ""
    key_elements: List[str] = Field(default_factory=list)
    video_type: str = ""
    target_audience: str = ""
    style_preferences: List[str] = Field(default_factory=list)
    style_guidance_for_visual: Optional[str] = None
    # Video-driven planning numbers (Program-computed; agent must obey)
    max_chapters: Optional[int] = None
    recommended_chapters: Optional[int] = None
    min_chapter_duration: Optional[int] = None
    max_chapter_duration: Optional[int] = None
    min_video_duration: Optional[int] = None
    reference_image_urls: List[str] = Field(default_factory=list)
    # Opaque extras for forward compat
    extra: Dict[str, Any] = Field(default_factory=dict)


class AudioContextArtifact(BaseModel):
    """Audio-driven extras; companion file next to analysis_brief."""

    schema_version: int = 1
    artifact: Literal["audio_context"] = "audio_context"
    audio_duration: float = 0.0
    total_segments: int = 0
    music_line_global: str = ""
    sections_info: str = ""
    segments_info: str = ""
    section_count: Optional[int] = None
