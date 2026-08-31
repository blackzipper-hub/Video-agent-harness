from app.domain.artifacts.invalidation import invalidate_downstream
from app.domain.artifacts.models import StudioArtifact, StudioArtifactEdge


def impact_preview(artifacts: list[StudioArtifact], edges: list[StudioArtifactEdge], asset_id: str) -> dict:
    result = invalidate_downstream(artifacts, edges, {asset_id})
    return {"stale_artifact_ids": sorted(result.stale_ids), "untouched_artifact_ids": sorted(result.untouched_ids)}
