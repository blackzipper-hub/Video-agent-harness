from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"
    SUPERSEDED = "superseded"


class StudioArtifact(BaseModel):
    id: str
    artifact_id: str
    project_id: str
    type: str
    status: ArtifactStatus = ArtifactStatus.READY
    metadata: dict[str, Any] = Field(default_factory=dict)


class StudioArtifactEdge(BaseModel):
    source_version_id: str
    target_version_id: str
    relation: str = "derived_from"
