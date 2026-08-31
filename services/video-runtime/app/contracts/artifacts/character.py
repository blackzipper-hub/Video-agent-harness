"""Character profiles artifact (disk) — image URLs filled later by Program."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class CharacterDraft(BaseModel):
    id: str = Field(description="Stable id used by scenes/script, e.g. char_hero")
    type: str = Field(default="character", description="Usually 'character'; rarely prop/creature")
    name: str = Field(description="Display name as spoken/shown in story")
    description: str = Field(
        description="Who they are in the story + casting gist (age range, vibe)",
    )
    personality: str = Field(default="", description="Traits that drive dialogue/choices")
    appearance: str = Field(
        default="",
        description="T2I-usable look: face, hair, outfit, era — concrete, not vague pretty",
    )
    role: str = Field(default="", description="Story function: protagonist / rival / mentor…")
    style: str = Field(default="", description="Art/render style hint aligned with style_guide")
    body_type: str = Field(default="", description="Build/silhouette for consistency across shots")


class CharactersArtifact(BaseModel):
    schema_version: int = 1
    artifact: Literal["characters"] = "characters"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    characters: List[CharacterDraft] = Field(min_length=1)
    user_message: str = ""


class CharactersAgentDraft(BaseModel):
    """Args for write_characters_artifact."""

    characters: List[CharacterDraft] = Field(
        min_length=1,
        description="Full cast needed for this piece (hero + key supporting)",
    )
    user_message: str = Field(
        default="",
        description="Optional notes for image gen / casting constraints",
    )
