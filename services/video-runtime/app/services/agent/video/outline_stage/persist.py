"""Persist outline artifact JSON + DB additional_data.artifact_path."""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.contracts.artifacts.outline import OutlineArtifact
from app.services.agent.stage_runtime.workspace import artifact_relpath, write_artifact_json


def stamp_and_write_outline_artifact(
    *,
    artifact: OutlineArtifact,
    story_outline_uuid: str,
    thread_id: str,
    run_id: str,
    mode: str,
) -> str:
    """Rewrite final outline.json with uuid; return relative artifact_path."""
    artifact.story_outline_uuid = story_outline_uuid
    artifact.thread_id = thread_id
    artifact.run_id = run_id
    artifact.mode = mode  # type: ignore[assignment]
    write_artifact_json(thread_id, run_id, "outline.json", artifact.model_dump(mode="json"))
    return artifact_relpath(thread_id, run_id, "outline.json")


def outline_additional_data(artifact_path: str) -> Dict[str, Any]:
    return {
        "artifact_path": artifact_path,
        "artifact_schema": "outline@1",
    }
