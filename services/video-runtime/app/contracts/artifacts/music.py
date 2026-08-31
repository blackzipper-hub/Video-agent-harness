"""Music intent / BGM prompt artifacts."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class MusicIntentAgentDraft(BaseModel):
    """Args for write_music_artifact (intent)."""

    intent: Literal["lyrics_provided", "auto_lyrics_song", "instrumental_bgm"] = Field(
        description="lyrics_provided | auto_lyrics_song | instrumental_bgm",
    )
    reasoning: str = Field(
        default="",
        description="Why this intent fits the piece (brief)",
    )


class MusicIntentArtifact(MusicIntentAgentDraft):
    schema_version: int = 1
    artifact: Literal["music_intent"] = "music_intent"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None


class MusicBgmAgentDraft(BaseModel):
    """Args for write_music_bgm_artifact."""

    suno_prompt: str = Field(description="Prompt text for Suno / BGM generator")
    title: str = Field(default="", description="Track title suggestion")
    tags: str = Field(default="", description="Comma-separated genre/mood tags")
    target_instrumental: bool = Field(
        default=True,
        description="True for instrumental BGM; False if vocals/lyrics desired",
    )


class MusicBgmArtifact(MusicBgmAgentDraft):
    schema_version: int = 1
    artifact: Literal["bgm"] = "bgm"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
