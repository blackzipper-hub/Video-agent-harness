from __future__ import annotations

from collections import defaultdict, deque

from .models import ArtifactStatus, StudioArtifact, StudioArtifactEdge


class InvalidationResult:
    def __init__(self, stale_ids: set[str], untouched_ids: set[str]) -> None:
        self.stale_ids = stale_ids
        self.untouched_ids = untouched_ids


def invalidate_downstream(
    artifacts: list[StudioArtifact],
    edges: list[StudioArtifactEdge],
    root_ids: set[str],
) -> InvalidationResult:
    """Mark only descendants of rejected/replaced assets as stale.

    The operation is intentionally graph-only: persistence creates new versions
    around this result, preserving previous selectable assets.
    """
    children: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        children[edge.source_version_id].add(edge.target_version_id)
    stale = set(root_ids)
    queue = deque(root_ids)
    while queue:
        current = queue.popleft()
        for child in children[current]:
            if child not in stale:
                stale.add(child)
                queue.append(child)
    known = {artifact.id for artifact in artifacts}
    return InvalidationResult(stale & known, known - stale)
