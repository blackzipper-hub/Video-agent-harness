from __future__ import annotations

import os
import json
from pathlib import Path

from app.capabilities.loader import build_registry, reload_registry
from app.chat.v2.skill_catalog import SkillCatalog
from app.orchestration.skills import SkillResolver
from app.orchestration.workflow_compiler import WorkflowRegistry


def configured_skill_roots() -> list[Path]:
    """Return the complete Cuti Skill source set used by the Video Runtime."""

    configured = os.getenv("VIDEO_SKILL_PATHS", "").strip()
    if configured:
        return [
            Path(item).expanduser().resolve()
            for item in configured.split(os.pathsep)
            if item.strip()
        ]
    service_root = Path(__file__).resolve().parents[2]
    return [
        *(service_root / "skills" / scope for scope in ("system", "builtin", "external")),
        service_root / "kit" / "skills" / "creative",
        service_root / "kit" / "skills" / "stages",
        service_root / "app" / "services" / "agent" / "video_edit" / "skills",
    ]


class VideoSkillRuntime:
    """Single catalog, capability registry, workflow registry, and resolver."""

    def __init__(self, roots: list[Path] | None = None) -> None:
        self.roots = roots or configured_skill_roots()
        for root in self.roots:
            root.mkdir(parents=True, exist_ok=True)
        self.catalog = SkillCatalog(self.roots)
        self.catalog.discover()
        self.capabilities = build_registry(self.catalog, include_platform=True)
        self.workflows = WorkflowRegistry()
        self.workflows.replace_from_catalog(self.catalog, self.capabilities)
        self.resolver = SkillResolver(
            self.catalog,
            self.capabilities,
            self.workflows.items(),
        )

    def reload(self) -> int:
        """Atomically refresh Skill metadata, executable capabilities, and workflows."""

        reload_registry(
            self.capabilities,
            self.catalog,
            include_platform=True,
        )
        self.workflows.replace_from_catalog(self.catalog, self.capabilities)
        return len(self.catalog.list_metadata())

    @property
    def external_root(self) -> Path:
        """Canonical writable root for user-installed Skill bundles."""
        configured = os.getenv("VIDEO_EXTERNAL_SKILL_PATH", "").strip()
        if configured:
            root = Path(configured).expanduser().resolve()
        else:
            root = next(
                (item for item in self.roots if item.name.lower() == "external"),
                Path(__file__).resolve().parents[2] / "skills" / "external",
            )
        root.mkdir(parents=True, exist_ok=True)
        return root

    def prompt_view(self) -> list[dict]:
        """Return the public catalog shape consumed by the migrated Cuti UI."""
        from .workflow_plans import SUPPORTED_WORKFLOW_MODES, UNAVAILABLE_WORKFLOW_MODES
        capabilities_by_skill = {
            item.skill_name: item
            for item in self.capabilities.list(include_disabled=True)
            if item.skill_name
        }
        result: list[dict] = []
        for metadata in self.catalog.list_metadata():
            view = metadata.prompt_view()
            capability = capabilities_by_skill.get(metadata.name)
            install_record: dict = {}
            record_path = metadata.path.parent / ".cuti-install.json"
            if record_path.is_file():
                try:
                    parsed = json.loads(record_path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        install_record = parsed
                except (OSError, json.JSONDecodeError):
                    pass
            view.update({
                "capability_id": capability.id if capability else None,
                "bundle_digest": (
                    install_record.get("payload_digest")
                    or (capability.bundle_digest if capability else None)
                ),
                "installed_at": install_record.get("installed_at"),
            })
            raw = dict(metadata.metadata or {})
            kind = str(raw.get("kind") or "helper")
            view["kind"] = kind
            if kind == "workflow":
                workflow = self.workflows.get(metadata.name)
                mode = workflow.mode if workflow is not None else ""
                view.update({
                    "available": mode in SUPPORTED_WORKFLOW_MODES,
                    "unavailable_reason": UNAVAILABLE_WORKFLOW_MODES.get(mode) or (
                        None if mode in SUPPORTED_WORKFLOW_MODES
                        else f"No installed compiler for workflow mode {mode}"
                    ),
                    "workflow_mode": mode,
                })
            else:
                view.update({"available": True, "unavailable_reason": None})
            result.append(view)
        return result


_default_runtime: VideoSkillRuntime | None = None


def default_video_skill_runtime() -> VideoSkillRuntime:
    global _default_runtime
    if _default_runtime is None:
        _default_runtime = VideoSkillRuntime()
    return _default_runtime
