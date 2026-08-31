"""Machine-readable workflow registration sourced from SKILL.md frontmatter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.capabilities.models import CapabilityRegistry


@dataclass(frozen=True)
class WorkflowSpec:
    skill_name: str
    title: str
    mode: str
    parameters: dict[str, Any] = field(default_factory=dict)
    pipeline: tuple[str, ...] = ()
    requires_keyframe: bool = True
    allowed_capabilities: frozenset[str] | None = None
    entrypoints: tuple[str, ...] = ()
    skill_dependencies: tuple[str, ...] = ()


class WorkflowRegistry:
    """Validated workflows discovered from installed Skill metadata."""

    def __init__(self) -> None:
        self._items: dict[str, WorkflowSpec] = {}

    def replace_from_catalog(
        self,
        catalog: Any,
        capabilities: CapabilityRegistry | None = None,
    ) -> dict[str, WorkflowSpec]:
        discovered: dict[str, WorkflowSpec] = {}
        metadata_items = catalog.list_metadata()
        available_skills = {item.name for item in metadata_items if item.enabled}
        for metadata in metadata_items:
            if not metadata.enabled:
                continue
            raw = dict(metadata.metadata or {})
            if raw.get("kind") != "workflow":
                continue
            spec = self._parse(metadata.name, metadata.description, raw)
            self._validate_capabilities(spec, capabilities)
            missing_skills = set(spec.skill_dependencies) - available_skills
            if missing_skills:
                raise ValueError(
                    f"workflow Skill {spec.skill_name} requires missing Skills: "
                    f"{sorted(missing_skills)}"
                )
            discovered[spec.skill_name] = spec
        self._items.clear()
        self._items.update(discovered)
        return self._items

    def get(self, name: str) -> WorkflowSpec | None:
        return self._items.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._items)

    def items(self) -> dict[str, WorkflowSpec]:
        return self._items

    @staticmethod
    def _parse(name: str, description: str, metadata: dict[str, Any]) -> WorkflowSpec:
        raw = metadata.get("workflow")
        if not isinstance(raw, dict):
            raise ValueError(
                f"workflow Skill {name} must declare metadata.workflow in SKILL.md"
            )
        mode = str(raw.get("mode") or "").strip()
        if not mode:
            raise ValueError(f"workflow Skill {name} must declare workflow.mode")
        title = str(raw.get("title") or description or name).strip()
        parameters = raw.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise ValueError(f"workflow Skill {name} workflow.parameters must be an object")
        pipeline = WorkflowRegistry._string_tuple(name, "pipeline", raw.get("pipeline") or [])
        allowed_raw = raw.get("allowed_capabilities")
        allowed = None
        if allowed_raw is not None:
            allowed = frozenset(
                WorkflowRegistry._string_tuple(name, "allowed_capabilities", allowed_raw)
            )
        entrypoints = WorkflowRegistry._string_tuple(
            name, "entrypoints", raw.get("entrypoints") or []
        )
        dependencies = raw.get("dependencies") or {}
        if not isinstance(dependencies, dict):
            raise ValueError(f"workflow Skill {name} workflow.dependencies must be an object")
        skill_dependencies = WorkflowRegistry._string_tuple(
            name, "dependencies.skills", dependencies.get("skills") or []
        )
        merged_parameters = dict(parameters)
        merged_parameters.setdefault("workflow_mode", mode)
        return WorkflowSpec(
            skill_name=name,
            title=title,
            mode=mode,
            parameters=merged_parameters,
            pipeline=pipeline,
            requires_keyframe=bool(raw.get("requires_keyframe", True)),
            allowed_capabilities=allowed,
            entrypoints=entrypoints,
            skill_dependencies=skill_dependencies,
        )

    @staticmethod
    def _string_tuple(name: str, field_name: str, value: Any) -> tuple[str, ...]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError(
                f"workflow Skill {name} workflow.{field_name} must be a string list"
            )
        return tuple(item.strip() for item in value)

    @staticmethod
    def _validate_capabilities(
        spec: WorkflowSpec,
        capabilities: CapabilityRegistry | None,
    ) -> None:
        if capabilities is None:
            if spec.allowed_capabilities is not None:
                missing_permissions = set(spec.pipeline) - spec.allowed_capabilities
                if missing_permissions:
                    raise ValueError(
                        f"workflow Skill {spec.skill_name} pipeline capabilities are not "
                        f"allowed: {sorted(missing_permissions)}"
                    )
            return
        if spec.allowed_capabilities is not None:
            missing_permissions = set(spec.pipeline) - spec.allowed_capabilities
            if missing_permissions:
                raise ValueError(
                    f"workflow Skill {spec.skill_name} pipeline capabilities are not "
                    f"allowed: {sorted(missing_permissions)}"
                )
        declared: Iterable[str] = (
            *spec.pipeline,
            *(spec.allowed_capabilities or ()),
        )
        for capability_id in declared:
            try:
                capabilities.get(capability_id, require_enabled=False)
            except LookupError as exc:
                raise ValueError(
                    f"workflow Skill {spec.skill_name} references unknown capability "
                    f"{capability_id}"
                ) from exc
