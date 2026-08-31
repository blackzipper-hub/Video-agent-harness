from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreativeBible(BaseModel):
    project_id: str
    story: dict[str, Any] = Field(default_factory=dict)
    characters: list[dict[str, Any]] = Field(default_factory=list)
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    style: dict[str, Any] = Field(default_factory=dict)
    camera_rules: dict[str, Any] = Field(default_factory=dict)
    music_reference_artifact_id: str | None = None
