"""Platform limits for concurrent video-generation tasks (not Skill-owned)."""
from __future__ import annotations

from .capabilities import CapabilityManifest, CapabilityRegistry
from .models import RunSnapshot, Task, TaskStatus

# Video output that is post-process / assembly, not generation.
NON_GENERATION_VIDEO_IDS = frozenset({
    "video.assemble",
    "media.concat",
    "video.edit",
})

ACTIVE_GENERATION_STATUSES = frozenset({
    TaskStatus.PROPOSED,
    TaskStatus.BLOCKED,
    TaskStatus.READY,
    TaskStatus.RUNNING,
    TaskStatus.WAITING_EXTERNAL,
})

IN_FLIGHT_GENERATION_STATUSES = frozenset({
    TaskStatus.RUNNING,
    TaskStatus.WAITING_EXTERNAL,
})


def is_video_generation_capability(capability: CapabilityManifest) -> bool:
    if capability.output_type != "video":
        return False
    return capability.id not in NON_GENERATION_VIDEO_IDS


def capability_is_video_generation(
    capabilities: CapabilityRegistry,
    capability_id: str,
) -> bool:
    try:
        capability = capabilities.get(
            capabilities.canonical_id(capability_id),
            require_enabled=False,
        )
    except (LookupError, ValueError):
        return False
    return is_video_generation_capability(capability)


def is_video_generation_task(
    capabilities: CapabilityRegistry,
    task: Task,
) -> bool:
    return capability_is_video_generation(capabilities, task.capability_id)


def count_generation_tasks(
    tasks: list[Task],
    capabilities: CapabilityRegistry,
    *,
    statuses: frozenset[TaskStatus],
) -> int:
    return sum(
        1
        for task in tasks
        if task.status in statuses and is_video_generation_task(capabilities, task)
    )


def count_active_generation_tasks(
    snapshot: RunSnapshot,
    capabilities: CapabilityRegistry,
) -> int:
    return count_generation_tasks(
        snapshot.tasks,
        capabilities,
        statuses=ACTIVE_GENERATION_STATUSES,
    )


def count_in_flight_generation_tasks(
    tasks: list[Task],
    capabilities: CapabilityRegistry,
) -> int:
    return count_generation_tasks(
        tasks,
        capabilities,
        statuses=IN_FLIGHT_GENERATION_STATUSES,
    )
