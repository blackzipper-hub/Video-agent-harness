"""Paths for stage runtime.

kit/     = agent knowledge (skills)
data/…   = per-run workspaces (not part of kit)
Backend root = AGENT_SERVICE_ROOT so both /kit/... and /data/... are visible.
"""
from __future__ import annotations

from pathlib import Path

# services/agent/
AGENT_SERVICE_ROOT = Path(__file__).resolve().parents[4]
KIT_ROOT = AGENT_SERVICE_ROOT / "kit"
SKILLS_ROOT = KIT_ROOT / "skills"


def virtual_skills_stage(stage: str) -> str:
    """Virtual path for create_deep_agent skills= (backend root = AGENT_SERVICE_ROOT)."""
    return f"/kit/skills/stages/{stage}"


def virtual_skills_builtin(domain: str) -> str:
    """Virtual path for a platform builtin Skill domain.

    Lets a stage agent mount the same package the coordinator loads, so a
    methodology shared by both does not get forked into a kit copy. The backend
    root is AGENT_SERVICE_ROOT, so ``skills/`` is visible alongside ``kit/``.
    """
    return f"/skills/builtin/{domain}"


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
