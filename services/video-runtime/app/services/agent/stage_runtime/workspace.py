"""Per-run local workspace: inputs/ + artifacts/ under STAGE_ARTIFACT_ROOT."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from .paths import run_workspace_dir

logger = logging.getLogger(__name__)


def ensure_run_workspace(thread_id: str, run_id: str) -> Path:
    root = run_workspace_dir(thread_id, run_id)
    (root / "inputs").mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    return root


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON atomically. Unique tmp name avoids collide/ENOENT races on replace."""
    import uuid as _uuid

    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.parent / f".{path.name}.{_uuid.uuid4().hex[:8]}.tmp"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def write_input_json(thread_id: str, run_id: str, name: str, payload: Dict[str, Any]) -> Path:
    root = ensure_run_workspace(thread_id, run_id)
    path = root / "inputs" / name
    atomic_write_json(path, payload)
    logger.info("stage workspace wrote input %s", path)
    return path


def write_artifact_json(thread_id: str, run_id: str, name: str, payload: Dict[str, Any]) -> Path:
    root = ensure_run_workspace(thread_id, run_id)
    path = root / "artifacts" / name
    atomic_write_json(path, payload)
    logger.info("stage workspace wrote artifact %s", path)
    return path


def artifact_path(thread_id: str, run_id: str, name: str) -> Path:
    return run_workspace_dir(thread_id, run_id) / "artifacts" / name


def artifact_exists(thread_id: str, run_id: str, name: str) -> bool:
    return artifact_path(thread_id, run_id, name).is_file()


def read_artifact_json(thread_id: str, run_id: str, name: str) -> Dict[str, Any]:
    path = artifact_path(thread_id, run_id, name)
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_relpath(thread_id: str, run_id: str, name: str) -> str:
    """Relative to agent service root (stored in additional_data.artifact_path)."""
    from .paths import AGENT_SERVICE_ROOT

    path = run_workspace_dir(thread_id, run_id) / "artifacts" / name
    try:
        return str(path.resolve().relative_to(AGENT_SERVICE_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)
