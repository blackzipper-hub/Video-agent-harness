"""Workflows and Skills that must not appear in Studio or director catalogs.

Compilers and SKILL.md files may still exist for tests and old project data.
New runs cannot select these ids.
"""

from __future__ import annotations

# Dest outline/character/keyframe pipelines and other dead user-facing Skills.
RETIRED_PUBLIC_SKILLS = frozenset({
    "workflow-keyframe-pipeline",
    "workflow-short-drama",
    "workflow-direct-video",
    "open-montage",
    "ink-press-product-workflow",
    "video-shotcraft",
})

# Plugin workflow aliases that duplicate $mv or wrap the dest keyframe path.
RETIRED_PUBLIC_PLUGIN_WORKFLOWS = frozenset({
    "cuti.seedance-story",
    "cuti.music-video",
    "cuti.lipsync-music-video",
})

RETIRED_PUBLIC_WORKFLOWS = RETIRED_PUBLIC_SKILLS | RETIRED_PUBLIC_PLUGIN_WORKFLOWS

RETIRED_UNAVAILABLE_REASON = "retired: no longer selectable in this runtime"


def is_retired_public_skill(name: str) -> bool:
    return name in RETIRED_PUBLIC_SKILLS


def is_retired_public_workflow(name: str) -> bool:
    return name in RETIRED_PUBLIC_WORKFLOWS
