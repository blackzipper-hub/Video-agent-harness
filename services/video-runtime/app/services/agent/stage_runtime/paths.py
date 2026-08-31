"""Paths for stage runtime.

kit/     = agent knowledge (skills + exported JSON schemas)
data/…   = per-run workspaces (not part of kit)
Backend root = AGENT_SERVICE_ROOT so both /kit/... and /data/... are visible.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

# services/agent/
AGENT_SERVICE_ROOT = Path(__file__).resolve().parents[4]
KIT_ROOT = AGENT_SERVICE_ROOT / "kit"
SKILLS_ROOT = KIT_ROOT / "skills"
SCHEMAS_ROOT = KIT_ROOT / "schemas"


def virtual_skills_stage(stage: str) -> str:
    """Virtual path for create_deep_agent skills= (backend root = AGENT_SERVICE_ROOT)."""
    return f"/kit/skills/stages/{stage}"


def virtual_skills_creative() -> str:
    """Shared cinematic creative skills (storytelling / short-form / …)."""
    return "/kit/skills/creative"


def is_short_drama_category(content_category: Optional[str]) -> bool:
    """True when content_category is Short Drama (delivery/routing only)."""
    return (content_category or "").strip().lower() == "short drama"


def is_story_narrative_category(content_category: Optional[str]) -> bool:
    """Story/narrative craft path: Default + Short Drama (+ empty → Default).

    Product Launch and Lip-Sync MV keep their own TTS/music/lipsync routing and
    are excluded from Explainer-skip / chapter-hint-skip / dense-dialogue craft.
    """
    raw = (content_category or "").strip().lower()
    if raw in {"product launch", "lip-sync mv"}:
        return False
    return True


def narrative_skills_paths(
    stage: str,
    *,
    content_category: Optional[str] = None,
) -> List[str]:
    """Stage skills + optional creative craft for narrative stages.

    Story narrative (Default / Short Drama): stage directors only — skip Explainer
    creative pack. Product Launch / Lip-Sync MV still mount creative when useful.
    """
    paths = [virtual_skills_stage(stage)]
    if not is_story_narrative_category(content_category):
        paths.append(virtual_skills_creative())
    return paths


# Back-compat alias
VIRTUAL_SKILLS_OUTLINE = virtual_skills_stage("outline")


def get_stage_artifact_root() -> Path:
    from app.config import settings

    root = Path(settings.STAGE_ARTIFACT_ROOT)
    if not root.is_absolute():
        root = AGENT_SERVICE_ROOT / root
    return root


def run_workspace_dir(thread_id: str, run_id: str) -> Path:
    tid = thread_id or "_no_thread"
    rid = run_id or "_no_run"
    return get_stage_artifact_root() / tid / rid


def virtual_run_prefix(thread_id: str, run_id: str) -> str:
    from app.config import settings

    rel = Path(settings.STAGE_ARTIFACT_ROOT)
    if rel.is_absolute():
        return f"/runs/{thread_id or '_no_thread'}/{run_id or '_no_run'}"
    return f"/{rel.as_posix()}/{(thread_id or '_no_thread')}/{(run_id or '_no_run')}"
