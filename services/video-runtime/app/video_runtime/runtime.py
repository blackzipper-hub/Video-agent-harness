from __future__ import annotations

import asyncio
import json
import os
import time
from copy import deepcopy
from typing import Any, Protocol

from .engine import IncrementalBuildEngine
from .models import (
    ArtifactDependency,
    Build,
    BuildPlanRevision,
    BuildStep,
    CheckpointResolution,
    ChangeRequest,
    ExportRecord,
    MediaArtifactVersion,
    PlanCheckpoint,
    Project,
    ProjectIntent,
    ProjectSessionBinding,
    ProjectSkillLock,
    ProjectVersion,
    RebuildPlan,
    RebuildPlanItem,
    ValidationResult,
    VideoSpec,
    VideoSpecRevision,
    now,
)
from .initial_build import topological_steps
from .repository import InMemoryVideoProjectRepository
from .plugins import PluginContext, VideoPluginRegistry
from .capabilities import RuntimeCapabilityRegistry
from .execution import CapabilityExecutionGateway
from .security import CapabilityGrant, CapabilityGrantSigner
from .skills import VideoSkillRuntime, default_video_skill_runtime
from app.orchestration.skills import SkillResolutionRequest
from app.domain.skills.service import make_skill_lock


_CHECKPOINT_METADATA_KEYS = {
    "content",
    "duration",
    "duration_seconds",
    "lyrics",
    "transcript",
    "beats",
    "segments",
    "words",
    "width",
    "height",
    "plan_step_id",
}


def _bounded_checkpoint_value(value: Any, *, limit: int = 16_000) -> Any:
    """Keep planner inputs useful while preventing an Artifact from flooding a prompt."""
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if len(encoded) <= limit:
        return value
    return {
        "truncated": True,
        "preview": encoded[:limit],
        "original_characters": len(encoded),
    }


def _checkpoint_artifact_summary(
    artifact: MediaArtifactVersion,
    *,
    issues: list[Any] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "id": artifact.id,
        "artifact_id": artifact.artifact_id,
        "type": artifact.type,
        "title": artifact.title,
        "summary": artifact.summary,
        "uri": artifact.uri,
        "metadata": {
            key: _bounded_checkpoint_value(value)
            for key, value in artifact.metadata.items()
            if key in _CHECKPOINT_METADATA_KEYS
        },
    }
    if issues is not None:
        summary["issues"] = issues
    return summary


def _merge_spec_patch(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge object sections recursively; arrays and scalar values replace atomically."""
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_spec_patch(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


class BuildStepExecutor(Protocol):
    async def rebuild_artifact(
        self,
        *,
        build: Build,
        source: MediaArtifactVersion,
        completed_replacements: dict[str, MediaArtifactVersion],
        idempotency_key: str,
    ) -> MediaArtifactVersion: ...

    async def execute_plan_step(
        self,
        *,
        build: Build,
        step: RebuildPlanItem,
        completed_artifacts: dict[str, MediaArtifactVersion],
        idempotency_key: str,
        report_remote_operation: Any | None = None,
    ) -> MediaArtifactVersion: ...


class PluginBuildStepExecutor:
    """Resolves artifact rebuild metadata to one authorized plugin capability."""

    def __init__(
        self,
        capabilities: RuntimeCapabilityRegistry,
        signer: CapabilityGrantSigner,
    ) -> None:
        self.capabilities = capabilities
        self.signer = signer

    async def rebuild_artifact(
        self,
        *,
        build: Build,
        source: MediaArtifactVersion,
        completed_replacements: dict[str, MediaArtifactVersion],
        idempotency_key: str,
    ) -> MediaArtifactVersion:
        capability = str(source.metadata.get("rebuild_capability") or "").strip()
        if not capability:
            raise ValueError(f"artifact has no rebuild_capability: {source.id}")
        entry = self.capabilities.describe(capability)
        loaded = self.capabilities.gateway.plugins.get(entry.plugin_id)
        permissions = loaded.manifest.permissions
        grant = CapabilityGrant(
            project_id=build.project_id,
            session_id=build.session_id or "system-recovery",
            user_id=build.user_id or "system-recovery",
            plugin_id=entry.plugin_id,
            capability=capability,
            allowed_capabilities=[capability],
            allowed_domains=list(permissions.network_domains),
            max_cost_usd=permissions.max_cost_usd,
            timeout_seconds=int(source.metadata.get("timeout_seconds") or 3600),
            max_concurrency=int(source.metadata.get("max_concurrency") or 1),
            max_retries=int(source.metadata.get("max_retries") or 0),
            idempotency_key=idempotency_key,
            audit_id=f"build:{build.id}:{source.id}",
            nonce=f"{build.id}:{source.id}",
            expires_at=int(time.time()) + 7200,
        )
        result = await self.capabilities.execute(
            grant_token=self.signer.issue(grant),
            project_id=grant.project_id,
            session_id=grant.session_id,
            user_id=grant.user_id,
            capability=capability,
            payload={
                "build": build.model_dump(mode="json"),
                "source": source.model_dump(mode="json"),
                "completed_replacements": {
                    key: value.model_dump(mode="json")
                    for key, value in completed_replacements.items()
                },
            },
            cancellation_id=build.id,
        )
        if isinstance(result, MediaArtifactVersion):
            return result
        return MediaArtifactVersion.model_validate(result)

    async def execute_plan_step(
        self,
        *,
        build: Build,
        step: RebuildPlanItem,
        completed_artifacts: dict[str, MediaArtifactVersion],
        idempotency_key: str,
        report_remote_operation: Any | None = None,
    ) -> MediaArtifactVersion:
        if not step.capability:
            raise ValueError(f"build step has no capability: {step.step_id}")
        entry = self.capabilities.describe(step.capability)
        loaded = self.capabilities.gateway.plugins.get(entry.plugin_id)
        permissions = loaded.manifest.permissions
        grant = CapabilityGrant(
            project_id=build.project_id,
            session_id=build.session_id or "system-recovery",
            user_id=build.user_id or "system-recovery",
            plugin_id=entry.plugin_id,
            capability=step.capability,
            allowed_capabilities=[step.capability],
            allowed_domains=list(permissions.network_domains),
            max_cost_usd=permissions.max_cost_usd,
            timeout_seconds=int(step.parameters.get("timeout_seconds") or 3600),
            max_concurrency=int(step.parameters.get("max_concurrency") or 1),
            max_retries=int(step.parameters.get("max_retries") or 1),
            idempotency_key=idempotency_key,
            audit_id=f"build:{build.id}:{step.step_id}",
            nonce=f"{build.id}:{step.step_id}",
            expires_at=int(time.time()) + 7200,
        )
        result = await self.capabilities.execute(
            grant_token=self.signer.issue(grant),
            project_id=grant.project_id,
            session_id=grant.session_id,
            user_id=grant.user_id,
            capability=step.capability,
            payload={
                "build": build.model_dump(mode="json"),
                "step": step.model_dump(mode="json"),
                "completed_artifacts": {
                    key: value.model_dump(mode="json")
                    for key, value in completed_artifacts.items()
                },
            },
            cancellation_id=build.id,
            report_remote_operation=report_remote_operation,
        )
        if isinstance(result, MediaArtifactVersion):
            return result
        return MediaArtifactVersion.model_validate(result)


class VideoBuildRuntime:
    """Project facade that keeps LLM planning outside deterministic build state."""

    def __init__(
        self,
        repository: Any | None = None,
        engine: IncrementalBuildEngine | None = None,
        plugins: VideoPluginRegistry | None = None,
        skill_runtime: VideoSkillRuntime | None = None,
        staged_planning_enabled: bool | None = None,
        max_parallel_generation_tasks: int | None = None,
    ) -> None:
        self.repo = repository or InMemoryVideoProjectRepository()
        self.engine = engine or IncrementalBuildEngine()
        self.plugins = plugins or VideoPluginRegistry()
        self.skills = skill_runtime or default_video_skill_runtime()
        self.staged_planning_enabled = (
            staged_planning_enabled
            if staged_planning_enabled is not None
            else os.getenv("VIDEO_STAGED_PLANNING_ENABLED", "false").strip().lower()
            in {"1", "true", "yes"}
        )
        # Keep the established Cuti deployment limit as the global safety cap;
        # a Workflow may declare a stricter limit but may not exceed this one.
        configured_parallelism = (
            max_parallel_generation_tasks
            if max_parallel_generation_tasks is not None
            else int(os.getenv("DEEP_AGENT_V2_MAX_PARALLEL_GENERATION_TASKS", "2"))
        )
        self.max_parallel_generation_tasks = max(1, configured_parallelism)
        self._default_executor: BuildStepExecutor | None = None
        self._grant_signer: CapabilityGrantSigner | None = None
        self._build_tasks: dict[str, asyncio.Task] = {}

    async def create_project(
        self, *, user_id: str, title: str, idempotency_key: str | None = None,
    ) -> tuple[Project, ProjectVersion]:
        project = Project(user_id=user_id, title=title)
        if idempotency_key:
            return await self.repo.create_project_idempotently(project, idempotency_key)
        return await self.repo.create_project(project)

    async def bind_session(self, *, project_id: str, session_id: str, user_id: str) -> ProjectSessionBinding:
        return await self.repo.bind_session(ProjectSessionBinding(
            project_id=project_id, session_id=session_id, user_id=user_id,
        ))

    async def list_project_skill_locks(self, project_id: str) -> list[ProjectSkillLock]:
        return await self.repo.list_project_skill_locks(project_id)

    async def set_project_skill_enabled(
        self,
        *,
        project_id: str,
        skill_id: str,
        enabled: bool,
    ) -> ProjectSkillLock:
        if not self.skills.catalog.has(skill_id):
            raise LookupError(f"unknown Skill: {skill_id}")
        metadata = self.skills.catalog.load(skill_id).metadata
        if enabled and not metadata.enabled:
            raise ValueError(f"Skill is disabled: {skill_id}")
        raw = dict(metadata.metadata or {})
        if enabled and raw.get("kind") == "workflow":
            for existing in await self.repo.list_project_skill_locks(project_id):
                if not existing.enabled or existing.skill_id == skill_id:
                    continue
                if not self.skills.catalog.has(existing.skill_id):
                    continue
                existing_metadata = self.skills.catalog.load(existing.skill_id).metadata
                if (existing_metadata.metadata or {}).get("kind") == "workflow":
                    await self.repo.upsert_project_skill_lock(
                        existing.model_copy(update={"enabled": False}),
                    )
        lock = ProjectSkillLock.model_validate(
            make_skill_lock(project_id, metadata).model_dump() | {"enabled": enabled},
        )
        return await self.repo.upsert_project_skill_lock(lock)

    async def reload_skills(self) -> int:
        from .skill_workflows import reload_workflow_skills

        await reload_workflow_skills(self.plugins, self.skills)
        self.refresh_plugin_execution()
        return len(self.skills.catalog.list_metadata())

    async def _apply_project_skill_locks(
        self,
        project_id: str,
        video_spec: VideoSpec | ProjectIntent,
    ) -> VideoSpec | ProjectIntent:
        enabled = [
            item for item in await self.repo.list_project_skill_locks(project_id)
            if item.enabled
        ]
        workflow_id = video_spec.workflow_id
        activated = list(video_spec.activated_skill_ids)
        for lock in enabled:
            if not self.skills.catalog.has(lock.skill_id):
                raise LookupError(f"project Skill is no longer installed: {lock.skill_id}")
            metadata = self.skills.catalog.load(lock.skill_id).metadata
            kind = (metadata.metadata or {}).get("kind")
            if kind == "workflow":
                # An explicit non-default workflow is a one-build override.
                if workflow_id == "cuti.seedance-story":
                    workflow_id = lock.skill_id
            elif lock.skill_id not in activated:
                activated.append(lock.skill_id)
        return video_spec.model_copy(update={
            "workflow_id": workflow_id,
            "activated_skill_ids": activated,
        })

    async def preview_change(
        self,
        *,
        project_id: str,
        description: str,
        target_artifact_version_ids: list[str],
        idempotency_key: str | None = None,
    ) -> RebuildPlan:
        if idempotency_key:
            existing_id = await self.repo.get_operation_result(
                project_id, "preview", idempotency_key,
            )
            if existing_id is not None:
                return await self.repo.get_plan(existing_id)
        project = await self.repo.get_project(project_id)
        change = ChangeRequest(
            project_id=project_id,
            base_project_version_id=project.current_version_id,
            description=description,
            target_artifact_version_ids=target_artifact_version_ids,
        )
        context = PluginContext(project_id=project_id, values={"change_request": change})
        for loaded in self.plugins.loaded:
            await loaded.implementation.before_plan(context)
        plan = self.engine.preview(
            change=change,
            artifacts=await self.repo.current_artifacts(project_id),
            dependencies=await self.repo.current_dependencies(project_id),
        )
        for loaded in self.plugins.loaded:
            plan = await loaded.implementation.after_plan(context, plan)
        await self._resolve_plan_skills(plan)
        return await self.repo.save_change_and_plan(change, plan, idempotency_key)

    async def preview_edits(
        self,
        *,
        project_id: str,
        base_project_version_id: str,
        edits: list[dict[str, Any]],
        description: str = "",
        idempotency_key: str | None = None,
    ) -> RebuildPlan:
        project = await self.repo.get_project(project_id)
        if project.current_version_id != base_project_version_id:
            from .repository import ProjectVersionConflict
            raise ProjectVersionConflict(base_project_version_id, project.current_version_id)
        artifacts = await self.repo.current_artifacts(project_id)
        spec_artifact = next((item for item in artifacts if item.type == "video_spec"), None)
        if spec_artifact is None or not isinstance(spec_artifact.metadata.get("content"), dict):
            raise LookupError("project has no editable VideoSpec")
        spec_data = deepcopy(spec_artifact.metadata["content"])
        targets: set[str] = set()
        manual_steps: set[str] = set()
        template_roots: set[str] = set()
        by_logical = {
            str(item.metadata.get("plan_step_id") or ""): item for item in artifacts
        }

        def target_step(step_id: str) -> None:
            artifact = by_logical.get(step_id)
            if artifact is not None:
                targets.add(artifact.id)

        for edit in edits:
            kind = str(edit.get("type") or edit.get("kind") or "")
            target_id = str(edit.get("id") or edit.get("targetId") or edit.get("target_id") or "")
            patch = edit.get("patch") if isinstance(edit.get("patch"), dict) else {}
            if kind == "patch_character":
                character = next((item for item in spec_data.get("characters", []) if item.get("id") == target_id), None)
                if character is None:
                    raise LookupError(f"character not found: {target_id}")
                character.update(patch)
                manual_steps.add("characters")
                target_step(f"character-{target_id}-reference")
                # Compatibility for projects created before per-character references.
                if f"character-{target_id}-reference" not in by_logical:
                    target_step("character-reference")
            elif kind == "patch_shot":
                shot = next((item for item in spec_data.get("shots", []) if item.get("id") == target_id), None)
                if shot is None:
                    raise LookupError(f"shot not found: {target_id}")
                shot.update(patch)
                manual_steps.add("storyboard")
                target_step(f"shot-{target_id}-keyframe")
            elif kind == "replace_music":
                spec_data.setdefault("audio", {})["bgm_prompt"] = str(
                    patch.get("bgm_prompt") or edit.get("prompt") or ""
                )
                target_step("bgm")
                template_roots.add("bgm")
            elif kind == "patch_timeline":
                shot_patches = patch.get("shots")
                if shot_patches is not None and not isinstance(shot_patches, list):
                    raise ValueError("patch_timeline.patch.shots must be a list")
                shots_by_id = {
                    str(item.get("id") or ""): item
                    for item in spec_data.get("shots", [])
                }
                for shot_patch in shot_patches or []:
                    if not isinstance(shot_patch, dict):
                        raise ValueError("timeline shot patches must be objects")
                    shot_id = str(shot_patch.get("id") or "")
                    shot = shots_by_id.get(shot_id)
                    if shot is None:
                        raise LookupError(f"shot not found: {shot_id}")
                    previous_duration = shot.get("duration_seconds")
                    previous_narration = shot.get("narration")
                    for field in ("order", "duration_seconds", "transition", "narration"):
                        if field in shot_patch:
                            shot[field] = shot_patch[field]
                    if shot.get("duration_seconds") != previous_duration:
                        target_step(f"shot-{shot_id}-video")
                        template_roots.add(f"shot-{shot_id}-video")
                    if shot.get("narration") != previous_narration:
                        target_step("narration")
                        target_step("subtitles")
                        template_roots.add("narration")
                        template_roots.add("subtitles")
                if shot_patches:
                    spec_data["shots"] = sorted(
                        shots_by_id.values(), key=lambda item: int(item.get("order") or 0),
                    )
                    spec_data["target_duration_seconds"] = sum(
                        float(item.get("duration_seconds") or 0)
                        for item in spec_data["shots"]
                    )
                    manual_steps.add("storyboard")
                target_step("timeline")
                target_step("assembled-video")
                template_roots.update({"timeline", "assembled-video"})
            elif kind == "generate_lipsync":
                target_step(f"shot-{target_id}-video" if target_id else "final-video")
            elif kind == "regenerate_artifact":
                version_id = str(edit.get("artifactVersionId") or edit.get("artifact_version_id") or "")
                if version_id:
                    targets.add(version_id)
                elif target_id:
                    target_step(target_id)
            elif kind == "patch_scene":
                target_step("storyboard")
            else:
                raise ValueError(f"unsupported edit type: {kind}")

        proposed_spec = await self._apply_project_skill_locks(
            project_id, VideoSpec.model_validate(spec_data),
        )
        change = ChangeRequest(
            project_id=project_id,
            base_project_version_id=base_project_version_id,
            description=description or "; ".join(str(item.get("type") or item.get("kind")) for item in edits),
            target_artifact_version_ids=sorted(targets),
            edits=edits,
            proposed_video_spec=proposed_spec,
        )
        dependencies = await self.repo.current_dependencies(project_id)
        plan = self.engine.preview(
            change=change,
            artifacts=artifacts,
            dependencies=dependencies,
        )
        plan.video_spec = proposed_spec
        plan.workflow_id = proposed_spec.workflow_id
        source_by_id = {item.id: item for item in artifacts}
        template_plan = await self._compile_workflow_plan(
            PluginContext(project_id=project_id, values={
                "video_spec": proposed_spec,
                "base_project_version_id": base_project_version_id,
            }),
            proposed_spec,
        )
        template_by_step = {item.step_id: item for item in template_plan.items}
        if template_roots and template_by_step:
            affected_steps = set(template_roots)
            changed = True
            while changed:
                changed = False
                for template in template_by_step.values():
                    if template.step_id in affected_steps:
                        continue
                    if any(dependency in affected_steps for dependency in template.depends_on):
                        affected_steps.add(template.step_id)
                        changed = True
            item_by_step = {item.step_id: item for item in plan.items if item.step_id}
            for step_id in affected_steps:
                template = template_by_step.get(step_id)
                if template is None:
                    continue
                source = by_logical.get(step_id)
                if source is not None:
                    item = next(
                        (value for value in plan.items if value.artifact_version_id == source.id),
                        None,
                    )
                    if item is None:
                        item = RebuildPlanItem(
                            step_id=step_id,
                            artifact_version_id=source.id,
                            output_artifact_id=source.artifact_id,
                            output_artifact_type=source.type,
                            action="rebuild",
                        )
                        plan.items.append(item)
                    if template.action == "validate":
                        item.action = "validate"
                    elif item.action in {"reuse", "validate"}:
                        item.action = "rebuild"
                    continue
                if step_id in item_by_step:
                    continue
                created = template.model_copy(deep=True)
                created.action = "validate" if template.action == "validate" else "create"
                plan.items.append(created)
                item_by_step[step_id] = created
        capability_costs = {
            "atomic.image.generate": 0.08,
            "atomic.video.generate": 0.65,
            "atomic.music.generate": 0.10,
            "suno.generate": 0.10,
            "api.provider.generate": 0.65,
            "media.tts": 0.05,
            "media.concat": 0.02,
            "media.mix_audio": 0.01,
            "media.subtitle.burn": 0.02,
            "media.lipsync": 0.35,
            "runtime.artifact.persist": 0.0,
        }
        legacy_capabilities = {
            "video_spec": "runtime.artifact.persist",
            "script": "runtime.artifact.persist",
            "characters": "runtime.artifact.persist",
            "storyboard": "runtime.artifact.persist",
            "character_reference": "atomic.image.generate",
            "keyframe": "atomic.image.generate",
            "video_clip": "atomic.video.generate",
            "continuity_frame": "media.extract_frame",
            "audio_narration": "media.tts",
            "audio_bgm": "atomic.music.generate",
            "timeline": "media.timeline.compose",
            "video_assembled": "media.concat",
            "video_mixed": "media.mix_audio",
            "subtitle": "media.subtitle.compose",
            "final_video": "media.subtitle.burn",
        }
        regeneration_overrides = {
            str(edit.get("artifactVersionId") or edit.get("artifact_version_id")): dict(edit.get("patch") or {})
            for edit in edits
            if str(edit.get("type") or edit.get("kind")) == "regenerate_artifact"
        }
        lipsync_shots = {
            str(edit.get("id") or edit.get("targetId") or "")
            for edit in edits
            if str(edit.get("type") or edit.get("kind")) == "generate_lipsync"
        }
        narration_artifact = by_logical.get("narration")
        for item in plan.items:
            source = source_by_id.get(item.artifact_version_id)
            if source is None:
                continue
            step_id = str(source.metadata.get("plan_step_id") or "")
            template = template_by_step.get(step_id)
            item.output_artifact_id = source.artifact_id
            item.output_artifact_type = source.type
            item.capability = str(
                source.metadata.get("rebuild_capability")
                or (template.capability if template else "")
                or legacy_capabilities.get(source.type, "")
            )
            # Preserve provider-specific values that are absent from the
            # workflow template, but let the newly compiled VideoSpec win for
            # ordering, duration, prompts, and dependency references.
            item.parameters = dict(source.metadata.get("generation_parameters") or {})
            if template is not None:
                item.parameters.update(template.parameters)
            item.parameters.update(regeneration_overrides.get(source.id, {}))
            item.estimated_cost = float(
                source.metadata.get("estimated_cost")
                or capability_costs.get(item.capability, 0.0)
            )
            item.step_id = str(source.metadata.get("plan_step_id") or item.step_id)
            if item.action != "rebuild":
                continue
            if step_id == "characters":
                item.parameters["content"] = [value.model_dump(mode="json") for value in proposed_spec.characters]
            elif step_id == "character-reference" or (step_id.startswith("character-") and step_id.endswith("-reference")):
                character_id = str(item.parameters.get("character_id") or "")
                selected = [value for value in proposed_spec.characters if not character_id or value.id == character_id]
                descriptions = "; ".join(
                    f"{value.name}: {value.appearance}; clothing: {value.clothing}"
                    for value in selected
                )
                item.parameters["prompt"] = f"Consistent production character reference sheet. {descriptions}"
            elif step_id == "storyboard":
                item.parameters["content"] = [value.model_dump(mode="json") for value in proposed_spec.shots]
            elif step_id.startswith("shot-"):
                shot_id = str(item.parameters.get("shot_id") or "")
                shot = next((value for value in proposed_spec.shots if value.id == shot_id), None)
                if shot is not None:
                    item.parameters["prompt"] = shot.visual_prompt
                    if source.type == "video_clip":
                        item.parameters["duration"] = shot.duration_seconds
            elif step_id == "bgm":
                item.parameters["prompt"] = proposed_spec.audio.bgm_prompt
            if source.type == "video_clip" and str(item.parameters.get("shot_id") or "") in lipsync_shots:
                if narration_artifact is None or not narration_artifact.uri or not source.uri:
                    raise ValueError("lipsync requires an existing narration and video clip")
                item.capability = "media.lipsync"
                item.estimated_cost = capability_costs["media.lipsync"]
                item.parameters = {
                    "video_url": source.uri,
                    "audio_url": narration_artifact.uri,
                    "model": "sync/lipsync-2-pro",
                }
        for step_id in sorted(manual_steps):
            source = by_logical.get(step_id)
            if source is None:
                continue
            plan.items = [item for item in plan.items if item.artifact_version_id != source.id]
            parameters = dict(source.metadata.get("generation_parameters") or {})
            if step_id == "characters":
                parameters["content"] = [value.model_dump(mode="json") for value in proposed_spec.characters]
            elif step_id == "storyboard":
                parameters["content"] = [value.model_dump(mode="json") for value in proposed_spec.shots]
            plan.items.append(RebuildPlanItem(
                step_id=step_id,
                artifact_version_id=source.id,
                output_artifact_id=source.artifact_id,
                output_artifact_type=source.type,
                action="rebuild",
                capability=str(source.metadata.get("rebuild_capability") or "runtime.artifact.persist"),
                parameters=parameters,
                order=-1,
                reason="Commit edited definition",
            ))
        # Persist the new spec atomically without invalidating every downstream artifact.
        plan.items = [item for item in plan.items if item.artifact_version_id != spec_artifact.id]
        plan.items.append(RebuildPlanItem(
            step_id="spec",
            artifact_version_id=spec_artifact.id,
            output_artifact_id=spec_artifact.artifact_id,
            output_artifact_type="video_spec",
            action="rebuild",
            capability="runtime.artifact.persist",
            parameters={"title": proposed_spec.title, "content": proposed_spec.model_dump(mode="json")},
            order=-1,
            reason="Commit edited VideoSpec",
        ))
        # Keep the executable plan self-describing. Artifact edges remain the
        # source of truth, while stable step ids let providers and the UI show
        # the same dependency order without rediscovering the graph.
        step_by_version = {
            item.artifact_version_id: item.step_id
            for item in plan.items
            if item.artifact_version_id and item.step_id
        }
        item_by_version = {
            item.artifact_version_id: item
            for item in plan.items
            if item.artifact_version_id
        }
        for edge in dependencies:
            target = item_by_version.get(edge.target_version_id)
            source_step = step_by_version.get(edge.source_version_id)
            if target is not None and source_step and source_step not in target.depends_on:
                target.depends_on.append(source_step)
        topological_steps(plan.items)
        plan.estimated_cost = round(sum(
            item.estimated_cost
            for item in plan.items
            if item.action in {"create", "rebuild"}
        ), 6)
        await self._resolve_plan_skills(plan)
        return await self.repo.save_change_and_plan(change, plan, idempotency_key)

    async def apply_rebuild(
        self,
        *,
        project_id: str,
        plan_id: str,
        base_project_version_id: str,
        idempotency_key: str,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> Build:
        build = await self.repo.submit_build(
            project_id=project_id,
            plan_id=plan_id,
            base_version_id=base_project_version_id,
            idempotency_key=idempotency_key,
            session_id=session_id,
            user_id=user_id,
        )
        self._schedule_build(build)
        return build

    async def plan_project(
        self,
        *,
        project_id: str,
        base_project_version_id: str,
        video_spec: VideoSpec | None = None,
        project_intent: ProjectIntent | None = None,
        idempotency_key: str,
    ) -> RebuildPlan:
        if (video_spec is None) == (project_intent is None):
            raise ValueError("provide exactly one of video_spec or project_intent")
        project = await self.repo.get_project(project_id)
        if project.current_version_id != base_project_version_id:
            from .repository import ProjectVersionConflict
            raise ProjectVersionConflict(base_project_version_id, project.current_version_id)
        supplied: VideoSpec | ProjectIntent = video_spec or project_intent  # type: ignore[assignment]
        supplied = await self._apply_project_skill_locks(project_id, supplied)
        if isinstance(supplied, VideoSpec):
            video_spec = supplied
            project_intent = None
        else:
            project_intent = supplied
            video_spec = None
        current_artifacts = await self.repo.current_artifacts(project_id)
        source_by_logical_id = {item.artifact_id: item for item in current_artifacts}
        referenced_source_ids = set(supplied.source_asset_ids)
        if video_spec is not None:
            referenced_source_ids.update(
                asset_id for shot in video_spec.shots for asset_id in shot.reference_asset_ids
            )
        missing_source_ids = sorted(referenced_source_ids - set(source_by_logical_id))
        if missing_source_ids:
            raise LookupError(
                "project plan references source assets that do not exist: "
                + ", ".join(missing_source_ids)
            )
        context = PluginContext(project_id=project_id, values={
            "video_spec": video_spec,
            "project_intent": project_intent,
            "base_project_version_id": base_project_version_id,
            "source_artifacts": {
                key: value for key, value in source_by_logical_id.items()
                if key in referenced_source_ids
            },
        })
        for loaded in self.plugins.loaded:
            await loaded.implementation.before_plan(context)
        if self.staged_planning_enabled:
            workflow_id = supplied.workflow_id
            plugin = self._workflow_plugin(workflow_id)
            planning_mode = plugin.implementation.planning_mode(workflow_id)
            if planning_mode not in {"full", "staged", "agentic"}:
                raise ValueError(f"unsupported workflow planning mode: {planning_mode}")
            if planning_mode == "full":
                if video_spec is None:
                    raise ValueError(
                        f"full-planning workflow {workflow_id} requires a complete VideoSpec"
                    )
                plan = await plugin.implementation.compile_build_plan(context, video_spec)
                plan.schema_version = 2
                plan.current_revision = 1
                plan.current_phase = "full"
                plan.next_checkpoint = None
            else:
                if project_intent is None:
                    assert video_spec is not None
                    project_intent = ProjectIntent(
                        title=video_spec.title,
                        brief=str(
                            video_spec.workflow_parameters.get("brief")
                            or "; ".join(shot.beat for shot in video_spec.shots)
                        ),
                        language=video_spec.language,
                        target_duration_seconds=video_spec.target_duration_seconds,
                        aspect_ratio=video_spec.aspect_ratio,
                        resolution=video_spec.resolution,
                        workflow_id=video_spec.workflow_id,
                        style_id=video_spec.style_id,
                        activated_skill_ids=list(video_spec.activated_skill_ids),
                        source_asset_ids=list(video_spec.source_asset_ids),
                        workflow_parameters=dict(video_spec.workflow_parameters),
                        providers=video_spec.providers,
                        automation=video_spec.automation,
                        constraints={"initial_video_spec": video_spec.model_dump(mode="json")},
                    )
                    context.values["project_intent"] = project_intent
                compile_initial = getattr(plugin.implementation, "compile_initial", None)
                if not callable(compile_initial):
                    raise LookupError(
                        f"workflow plugin has no staged compiler: {project_intent.workflow_id}"
                    )
                plan = await compile_initial(context, project_intent)
            if project_intent is None and video_spec is not None:
                # Full planning does not need a ProjectIntent, but preserving the
                # user's known constraints makes the plan self-describing.
                assert video_spec is not None
                project_intent = ProjectIntent(
                    title=video_spec.title,
                    brief=str(
                        video_spec.workflow_parameters.get("brief")
                        or "; ".join(shot.beat for shot in video_spec.shots)
                    ),
                    language=video_spec.language,
                    target_duration_seconds=video_spec.target_duration_seconds,
                    aspect_ratio=video_spec.aspect_ratio,
                    resolution=video_spec.resolution,
                    workflow_id=video_spec.workflow_id,
                    style_id=video_spec.style_id,
                    activated_skill_ids=list(video_spec.activated_skill_ids),
                    source_asset_ids=list(video_spec.source_asset_ids),
                    workflow_parameters=dict(video_spec.workflow_parameters),
                    providers=video_spec.providers,
                    automation=video_spec.automation,
                    constraints={"initial_video_spec": video_spec.model_dump(mode="json")},
                )
            source_versions = [source_by_logical_id[item].id for item in referenced_source_ids]
            revision_content = (
                video_spec.model_dump(mode="json")
                if video_spec is not None
                else project_intent.model_dump(mode="json")
            )
            existing_spec_revisions = await self.repo.list_video_spec_revisions(project_id)
            latest_spec_revision = (
                existing_spec_revisions[-1] if existing_spec_revisions else None
            )
            spec_revision = VideoSpecRevision(
                project_id=project_id,
                revision=(latest_spec_revision.revision + 1 if latest_spec_revision else 1),
                parent_revision_id=(latest_spec_revision.id if latest_spec_revision else None),
                content=revision_content,
                resolved_sections=(
                    ["title", "format", "workflow", "style", "providers", "automation"]
                    if video_spec is None
                    else [
                        "title", "format", "workflow", "style", "characters", "shots",
                        "audio", "providers", "automation", "timeline",
                    ]
                ),
                unresolved_sections=(
                    ["characters", "shots", "audio", "timeline"]
                    if video_spec is None else []
                ),
                source_artifact_version_ids=source_versions,
                created_by="agent",
                complete=video_spec is not None,
            )
            plan.video_spec = video_spec
            plan.video_spec_revision_id = spec_revision.id
            plan.project_intent = project_intent
            plan_revision = BuildPlanRevision(
                plan_id=plan.id,
                project_id=project_id,
                revision=1,
                base_revision=0,
                video_spec_revision_id=spec_revision.id,
                added_step_ids=[item.step_id for item in plan.items],
                reason="Initial staged phase",
            )
        else:
            if video_spec is None:
                raise ValueError(
                    "ProjectIntent requires VIDEO_STAGED_PLANNING_ENABLED=true"
                )
            plan = await self._compile_workflow_plan(context, video_spec)
        for loaded in self.plugins.loaded:
            plan = await loaded.implementation.after_plan(context, plan)
        topological_steps(plan.items)
        await self._resolve_plan_skills(plan)
        if plan.schema_version == 2:
            saved = await self.repo.save_staged_plan(
                plan, spec_revision, plan_revision, idempotency_key,
            )
        else:
            saved = await self.repo.save_plan(plan, idempotency_key)
        if self.skills.catalog.has(saved.workflow_id):
            metadata = self.skills.catalog.load(saved.workflow_id).metadata
            if (metadata.metadata or {}).get("kind") == "workflow":
                await self.set_project_skill_enabled(
                    project_id=project_id,
                    skill_id=saved.workflow_id,
                    enabled=True,
                )
        return saved

    def _workflow_plugin(self, workflow_id: str):
        from .skill_workflows import SKILL_WORKFLOW_PLUGIN_ID

        matches = [
            loaded for loaded in self.plugins.loaded
            if workflow_id in loaded.manifest.contributions.workflows
        ]
        if not matches:
            raise LookupError(f"workflow plugin is not installed: {workflow_id}")
        return next(
            (item for item in matches if item.manifest.id == SKILL_WORKFLOW_PLUGIN_ID),
            matches[0],
        )

    async def _compile_workflow_plan(
        self,
        context: PluginContext,
        video_spec: VideoSpec,
    ) -> RebuildPlan:
        return await self._workflow_plugin(video_spec.workflow_id).implementation.compile_build_plan(
            context, video_spec,
        )

    async def _resolve_plan_skills(self, plan: RebuildPlan) -> None:
        activated = list(plan.video_spec.activated_skill_ids) if plan.video_spec else []
        project_locks = await self.repo.list_project_skill_locks(plan.project_id)
        for item in plan.items:
            try:
                capability = self.skills.capabilities.get(
                    item.capability,
                    require_enabled=False,
                )
                capability_skill_id = capability.skill_name
            except LookupError:
                capability_skill_id = None
            context = self.skills.resolver.resolve(SkillResolutionRequest(
                workflow_skill_id=(
                    plan.workflow_id
                    if self.skills.workflows.get(plan.workflow_id) is not None
                    else None
                ),
                activated_skill_ids=activated,
                project_skill_locks=project_locks,
                declared_skill_ids=list(item.skill_ids),
                capability_id=item.capability,
                output_type=item.output_artifact_type,
                capability_skill_id=capability_skill_id,
                constraint_contract=item.constraint_contract,
                require_constraint_contract=False,
            ))
            item.skill_context = context
            item.resolved_skills = list(context.applied_skills) if context else []

    async def start_build(
        self,
        *,
        project_id: str,
        plan_id: str,
        base_project_version_id: str,
        idempotency_key: str,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> Build:
        build = await self.repo.submit_build(
            project_id=project_id,
            plan_id=plan_id,
            base_version_id=base_project_version_id,
            idempotency_key=idempotency_key,
            session_id=session_id,
            user_id=user_id,
        )
        self._schedule_build(build)
        return build

    async def inspect_checkpoint(
        self, *, project_id: str, build_id: str, checkpoint_id: str,
        session_id: str | None = None, user_id: str | None = None,
    ) -> PlanCheckpoint:
        checkpoint = await self.repo.get_checkpoint(project_id, build_id, checkpoint_id)
        if session_id is not None and checkpoint.session_id != session_id:
            raise PermissionError("checkpoint does not belong to this DeepSeek Session")
        if user_id is not None and checkpoint.user_id != user_id:
            raise PermissionError("checkpoint does not belong to this user")
        return checkpoint

    async def resolve_checkpoint(
        self,
        *,
        project_id: str,
        build_id: str,
        checkpoint_id: str,
        resolution: CheckpointResolution,
        session_id: str,
        user_id: str,
    ) -> RebuildPlan:
        existing = await self.repo.get_operation_result(
            project_id, f"checkpoint:{checkpoint_id}", resolution.idempotency_key,
        )
        if existing is not None:
            return await self.repo.get_plan(existing)
        checkpoint = await self.inspect_checkpoint(
            project_id=project_id,
            build_id=build_id,
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            user_id=user_id,
        )
        if checkpoint.base_plan_revision != resolution.base_plan_revision:
            from .repository import PlanRevisionConflict
            raise PlanRevisionConflict(
                resolution.base_plan_revision, checkpoint.base_plan_revision,
            )
        if checkpoint.base_spec_revision != resolution.base_spec_revision:
            from .repository import VideoSpecRevisionConflict
            raise VideoSpecRevisionConflict(
                resolution.base_spec_revision, checkpoint.base_spec_revision,
            )
        plan = await self.repo.get_plan(checkpoint.plan_id)
        if plan.schema_version != 2:
            raise ValueError("legacy build plans cannot resolve semantic checkpoints")
        current_spec_revision = await self.repo.get_video_spec_revision(
            plan.video_spec_revision_id,
        )
        if resolution.video_spec is not None:
            submitted_spec = resolution.video_spec
        else:
            intent = plan.project_intent
            intent_base = ({
                "title": intent.title,
                "language": intent.language,
                "target_duration_seconds": intent.target_duration_seconds,
                "aspect_ratio": intent.aspect_ratio,
                "resolution": intent.resolution,
                "workflow_id": intent.workflow_id,
                "style_id": intent.style_id,
                "activated_skill_ids": list(intent.activated_skill_ids),
                "source_asset_ids": list(intent.source_asset_ids),
                "workflow_parameters": dict(intent.workflow_parameters),
                "providers": intent.providers.model_dump(mode="json"),
                "automation": intent.automation.model_dump(mode="json"),
            } if intent is not None else {})
            current_base = {
                key: value for key, value in current_spec_revision.content.items()
                if key in VideoSpec.model_fields
            }
            submitted_spec = VideoSpec.model_validate(_merge_spec_patch(
                {**intent_base, **current_base},
                resolution.video_spec_patch or {},
            ))
        supplied_spec = await self._apply_project_skill_locks(
            project_id, submitted_spec,
        )
        if not isinstance(supplied_spec, VideoSpec):
            raise TypeError("checkpoint resolution requires a complete VideoSpec")
        if supplied_spec.workflow_id != plan.workflow_id:
            raise ValueError("checkpoint resolution cannot switch workflows")
        if (
            plan.project_intent is not None
            and supplied_spec.providers != plan.project_intent.providers
        ):
            raise ValueError("checkpoint resolution cannot switch providers")
        resolution = resolution.model_copy(update={"video_spec": supplied_spec})
        source_artifacts = {
            item.artifact_id: item
            for item in await self.repo.current_artifacts(project_id)
        }
        referenced_source_ids = {
            *supplied_spec.source_asset_ids,
            *(
                asset_id
                for shot in supplied_spec.shots
                for asset_id in shot.reference_asset_ids
            ),
        }
        missing = sorted(referenced_source_ids - set(source_artifacts))
        if missing:
            raise LookupError(
                "VideoSpec references project source assets that do not exist: "
                + ", ".join(missing)
            )
        spec_revision = VideoSpecRevision(
            project_id=project_id,
            revision=current_spec_revision.revision + 1,
            parent_revision_id=current_spec_revision.id,
            content=supplied_spec.model_dump(mode="json"),
            resolved_sections=[
                "title", "format", "workflow", "style", "characters", "shots",
                "audio", "providers", "automation", "timeline",
            ],
            unresolved_sections=[],
            source_artifact_version_ids=[
                source_artifacts[item].id for item in sorted(referenced_source_ids)
            ],
            checkpoint_id=checkpoint.id,
            created_by="agent",
            complete=True,
        )
        context = PluginContext(project_id=project_id, build_id=build_id, values={
            "video_spec": supplied_spec,
            "base_project_version_id": plan.base_project_version_id,
            "source_artifacts": {
                key: value for key, value in source_artifacts.items()
                if key in referenced_source_ids
            },
            "checkpoint": checkpoint,
            "phase_inputs": dict(resolution.phase_inputs),
            "video_spec_revision_id": spec_revision.id,
        })
        plugin = self._workflow_plugin(plan.workflow_id)
        compile_phase = getattr(plugin.implementation, "compile_phase", None)
        if not callable(compile_phase):
            raise LookupError(f"workflow plugin has no staged phase compiler: {plan.workflow_id}")
        updated_plan = await compile_phase(context, checkpoint, resolution, plan)
        if updated_plan.workflow_id != plan.workflow_id:
            raise ValueError("workflow compiler changed the locked workflow")
        previous_ids = {item.step_id for item in plan.items}
        added_ids = [item.step_id for item in updated_plan.items if item.step_id not in previous_ids]
        if not added_ids:
            raise ValueError("checkpoint resolution did not add a build phase")
        await self._resolve_plan_skills(updated_plan)
        topological_steps(updated_plan.items)
        plan_revision = BuildPlanRevision(
            plan_id=plan.id,
            project_id=project_id,
            revision=updated_plan.current_revision,
            base_revision=plan.current_revision,
            checkpoint_id=checkpoint.id,
            video_spec_revision_id=spec_revision.id,
            added_step_ids=added_ids,
            reason=resolution.reason or f"Resolved checkpoint {checkpoint.id}",
        )
        saved = await self.repo.resolve_checkpoint(
            checkpoint=checkpoint,
            updated_plan=updated_plan,
            spec_revision=spec_revision,
            plan_revision=plan_revision,
            idempotency_key=resolution.idempotency_key,
        )
        build = await self.repo.get_build(project_id, build_id)
        self._schedule_build(build)
        return saved

    async def retry_checkpoint(
        self, *, project_id: str, build_id: str, checkpoint_id: str,
    ) -> PlanCheckpoint:
        return await self.repo.retry_checkpoint(project_id, build_id, checkpoint_id)

    def configure_plugin_execution(self, signer: CapabilityGrantSigner) -> int:
        """Register loaded plugin handlers and make them the default Build executor."""
        self._grant_signer = signer
        capabilities = RuntimeCapabilityRegistry(
            CapabilityExecutionGateway(signer, self.plugins),
        )
        count = 0
        for loaded in self.plugins.loaded:
            provider = getattr(loaded.implementation, "capability_handlers", None)
            if provider is None:
                continue
            handlers = provider()
            for capability, operation in handlers.items():
                capabilities.register(
                    plugin_id=loaded.manifest.id,
                    capability=capability,
                    operation=operation,
                )
                count += 1
        if count:
            self._default_executor = PluginBuildStepExecutor(capabilities, signer)
        return count

    def refresh_plugin_execution(self) -> int:
        if self._grant_signer is None:
            return 0
        return self.configure_plugin_execution(self._grant_signer)

    async def recover_active_builds(self) -> int:
        builds = [
            build
            for project in await self._all_projects_for_recovery()
            for build in await self.repo.active_builds(project.id)
            if build.status != "waiting_agent"
        ]
        for build in builds:
            self._schedule_build(build)
        return len(builds)

    async def _all_projects_for_recovery(self):
        list_all = getattr(self.repo, "list_all_projects", None)
        if list_all is not None:
            return await list_all()
        projects = getattr(self.repo, "projects", {})
        return list(projects.values())

    def _schedule_build(self, build: Build) -> None:
        if self._default_executor is None or build.status not in {
            "queued", "running", "waiting_external",
        } or build.id in self._build_tasks:
            return
        task = asyncio.create_task(self.execute_build(
            project_id=build.project_id,
            build_id=build.id,
            executor=self._default_executor,
        ))
        self._build_tasks[build.id] = task
        task.add_done_callback(
            lambda finished, build_id=build.id: self._settle_build_task(build_id, finished),
        )

    def _settle_build_task(self, build_id: str, task: asyncio.Task) -> None:
        self._build_tasks.pop(build_id, None)
        if not task.cancelled():
            task.exception()

    async def cancel_build(self, *, project_id: str, build_id: str) -> Build:
        build = await self.repo.cancel_build(project_id, build_id)
        task = self._build_tasks.get(build_id)
        if task is not None:
            task.cancel()
        return build

    async def retry_failed_build(self, *, project_id: str, build_id: str) -> Build:
        build = await self.repo.requeue_failed_build(project_id, build_id)
        self._schedule_build(build)
        return build

    async def close(self) -> None:
        tasks = list(self._build_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def complete_build(
        self,
        *,
        build_id: str,
        replacements: dict[str, MediaArtifactVersion],
        validation_results: list[ValidationResult],
        created_artifacts: dict[str, MediaArtifactVersion] | None = None,
    ) -> tuple[Build, ProjectVersion]:
        build, version = await self.repo.commit_build(
            build_id=build_id,
            replacements=replacements,
            validation_results=validation_results,
            created_artifacts=created_artifacts,
        )
        context = PluginContext(project_id=build.project_id, build_id=build.id)
        for artifact in [*replacements.values(), *(created_artifacts or {}).values()]:
            for loaded in self.plugins.loaded:
                await loaded.implementation.on_artifact_committed(context, artifact)
        return build, version

    async def execute_build(
        self,
        *,
        project_id: str,
        build_id: str,
        executor: BuildStepExecutor,
    ) -> tuple[Build, ProjectVersion | None]:
        """Execute one persisted plan and publish only its atomic version commit."""
        build = await self.repo.get_build(project_id, build_id)
        if build.status == "completed" and build.project_version_id:
            return build, await self.repo.get_project_version(build.project_version_id)
        if build.status in {"failed", "cancelled"}:
            raise ValueError(f"cannot execute {build.status} build")
        try:
            plan = await self.repo.get_plan(build.plan_id)
            if plan.kind == "initial":
                execute_plan_step = getattr(executor, "execute_plan_step", None)
                if execute_plan_step is None:
                    raise TypeError("initial builds require a plan step executor")
                return await self._execute_initial_build(build=build, plan=plan, executor=executor)
            source_by_id = {
                item.id: item for item in await self.repo.current_artifacts(project_id)
            }
            persisted_steps = {
                item.plan_step_id: item
                for item in await self.repo.list_build_steps(project_id, build_id)
            }
            build.status = "running"
            build.message = "Executing rebuild plan"
            await self.repo.update_build(build)
            replacements: dict[str, MediaArtifactVersion] = {}
            created_artifacts: dict[str, MediaArtifactVersion] = {}
            ordered = topological_steps(plan.items)
            executable_items = [
                item for item in ordered if item.action in {"create", "rebuild"}
            ]
            completed_by_step = {
                str(item.metadata.get("plan_step_id") or item.id): item
                for item in source_by_id.values()
            }
            for item in executable_items:
                state = persisted_steps[item.step_id]
                if state.status == "completed" and state.result_artifact_version_id:
                    artifact = await self.repo.get_artifact(
                        project_id, state.result_artifact_version_id,
                    )
                    completed_by_step[item.step_id] = artifact
                    if item.action == "create":
                        created_artifacts[item.step_id] = artifact
                    else:
                        replacements[item.artifact_version_id] = artifact
            for item in plan.items:
                if item.action == "reuse":
                    state = persisted_steps[item.step_id]
                    if state.status != "completed":
                        state.status = "completed"
                        state.completed_at = now()
                        await self.repo.update_build_step(state)
            for index, item in enumerate(executable_items):
                current = await self.repo.get_build(project_id, build_id)
                if current.status == "cancelled":
                    raise ValueError("build was cancelled")
                source = source_by_id.get(item.artifact_version_id)
                if item.action == "rebuild" and source is None:
                    raise LookupError(
                        f"planned artifact version not found: {item.artifact_version_id}",
                    )
                state = persisted_steps[item.step_id]
                if state.status == "completed" and (
                    item.step_id in created_artifacts
                    or item.artifact_version_id in replacements
                ):
                    continue
                state.status = "running"
                state.attempt += 1
                state.started_at = state.started_at or now()
                state.error = None
                await self.repo.update_build_step(state)
                completed_for_provider: dict[str, MediaArtifactVersion] = dict(source_by_id)
                for existing in source_by_id.values():
                    completed_for_provider[str(existing.metadata.get("plan_step_id") or existing.id)] = existing
                for old_id, rebuilt in replacements.items():
                    completed_for_provider[old_id] = rebuilt
                    old = source_by_id.get(old_id)
                    if old is not None:
                        completed_for_provider[str(old.metadata.get("plan_step_id") or old_id)] = rebuilt
                completed_for_provider.update(completed_by_step)
                try:
                    if item.action == "create":
                        execute_plan_step = getattr(executor, "execute_plan_step", None)
                        if execute_plan_step is None:
                            raise TypeError("incremental create steps require a plan step executor")

                        async def report_remote(operation_id: str, provider: str) -> None:
                            state.remote_operation_id = operation_id
                            state.remote_provider = provider
                            state.status = "waiting_external"
                            await self.repo.update_build_step(state)
                            build.status = "waiting_external"
                            build.message = f"Waiting for {provider} operation for {item.step_id}"
                            await self.repo.update_build(build)

                        effective_step = item
                        if state.remote_operation_id:
                            effective_step = item.model_copy(deep=True)
                            effective_step.parameters["remote_operation_id"] = state.remote_operation_id
                        replacement = await execute_plan_step(
                            build=build,
                            step=effective_step,
                            completed_artifacts={
                                dependency: completed_by_step[dependency]
                                for dependency in item.depends_on
                                if dependency in completed_by_step
                            },
                            idempotency_key=(
                                item.idempotency_key
                                or f"{build.idempotency_key}:{item.step_id}"
                            ),
                            report_remote_operation=report_remote,
                        )
                        build.status = "running"
                    else:
                        assert source is not None
                        effective_source = source.model_copy(deep=True)
                        effective_source.metadata["rebuild_parameters"] = dict(item.parameters)
                        if item.capability:
                            effective_source.metadata["rebuild_capability"] = item.capability
                        effective_source.metadata["resolved_skills"] = [
                            value.model_dump(mode="json") for value in item.resolved_skills
                        ]
                        effective_source.metadata["skill_context"] = (
                            item.skill_context.model_dump(mode="json")
                            if item.skill_context is not None
                            else None
                        )
                        replacement = await executor.rebuild_artifact(
                            build=build,
                            source=effective_source,
                            completed_replacements=completed_for_provider,
                            idempotency_key=f"{build.idempotency_key}:{source.id}",
                        )
                except BaseException as exc:
                    state.status = "failed"
                    state.error = str(exc)
                    await self.repo.update_build_step(state)
                    raise
                replacement.project_id = project_id
                replacement.artifact_id = (
                    item.output_artifact_id
                    if item.action == "create"
                    else source.artifact_id  # type: ignore[union-attr]
                )
                replacement.type = (
                    item.output_artifact_type
                    or (source.type if source is not None else replacement.type)
                )
                replacement.status = "draft"
                replacement.metadata = {
                    **replacement.metadata,
                    "build_id": build.id,
                    "plan_step_id": item.step_id,
                    "capability": item.capability,
                    "rebuild_capability": item.capability,
                    "generation_parameters": dict(item.parameters),
                    "estimated_cost": item.estimated_cost,
                    "resolved_skills": [
                        value.model_dump(mode="json") for value in item.resolved_skills
                    ],
                    "skill_context": (
                        item.skill_context.model_dump(mode="json")
                        if item.skill_context is not None
                        else None
                    ),
                }
                if replacement.type == "video_spec" and isinstance(
                    item.parameters.get("content"), dict,
                ):
                    replacement.metadata["content"] = deepcopy(item.parameters["content"])
                replacement = await self.repo.stage_artifact(replacement)
                completed_by_step[item.step_id] = replacement
                if item.action == "create":
                    created_artifacts[item.step_id] = replacement
                else:
                    assert source is not None
                    replacements[source.id] = replacement
                state.status = "completed"
                state.result_artifact_version_id = replacement.id
                state.completed_at = now()
                await self.repo.update_build_step(state)
                build.actual_cost = round(sum(
                    candidate.estimated_cost
                    for candidate in executable_items
                    if persisted_steps[candidate.step_id].status == "completed"
                ), 6)
                build.progress = (index + 1) / max(1, len(executable_items) + 1)
                build.message = f"Completed {index + 1} of {len(executable_items)} media steps"
                await self.repo.update_build(build)

            validations: list[ValidationResult] = []
            validation_targets = [
                source_by_id[item.artifact_version_id]
                for item in plan.items
                if item.action == "validate" and item.artifact_version_id in source_by_id
            ]
            validation_targets.extend([*replacements.values(), *created_artifacts.values()])
            for artifact in validation_targets:
                results = await self.validate_artifact(build_id=build.id, artifact=artifact)
                if artifact.id in plan.ids_for("validate") and not results:
                    raise ValueError(
                        f"artifact requires validation but no validator handled it: {artifact.id}",
                    )
                if any(not result.passed for result in results):
                    raise ValueError(f"artifact validation failed: {artifact.id}")
                validations.extend(results)
            for item in plan.items:
                if item.action == "validate":
                    state = persisted_steps[item.step_id]
                    state.status = "completed"
                    state.completed_at = now()
                    await self.repo.update_build_step(state)
            return await self.complete_build(
                build_id=build.id,
                replacements=replacements,
                validation_results=validations,
                created_artifacts=created_artifacts,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            current = await self.repo.get_build(project_id, build_id)
            if current.status != "cancelled":
                current.status = "failed"
                current.message = "Build failed; the previous project version remains active"
                current.error = str(exc)
                await self.repo.update_build(current)
            raise

    async def _execute_initial_create_step(
        self,
        *,
        build: Build,
        planned: RebuildPlanItem,
        state: BuildStep,
        completed: dict[str, MediaArtifactVersion],
        executor: BuildStepExecutor,
        retry_limit: int,
    ) -> MediaArtifactVersion:
        """Execute and durably stage one create step.

        The method is deliberately unaware of scheduling.  The initial-build
        scheduler can therefore run a declared independent group concurrently
        while retaining the same retry, remote-job resume and idempotency
        behavior used by a sequential step.
        """
        last_error: BaseException | None = None
        # Reconciliation is not a new paid generation attempt. Even when the
        # local attempt counter reached its retry limit before a process crash,
        # a durable remote operation must still be polled and collected.
        resume_existing_remote = bool(state.remote_operation_id)
        attempts = (
            range(1)
            if resume_existing_remote
            else range(state.attempt, retry_limit + 1)
        )
        for _attempt in attempts:
            state.status = "running"
            if not resume_existing_remote:
                state.attempt += 1
            state.started_at = state.started_at or now()
            state.error = None
            await self.repo.update_build_step(state)
            try:
                async def report_remote(operation_id: str, provider: str) -> None:
                    state.remote_operation_id = operation_id
                    state.remote_provider = provider
                    state.status = "waiting_external"
                    await self.repo.update_build_step(state)
                    build.status = "waiting_external"
                    build.message = f"Waiting for {provider} operation for {planned.step_id}"
                    await self.repo.update_build(build)

                effective_step = planned
                if state.remote_operation_id:
                    # A provider submission survived a Runtime restart. Feed the
                    # durable operation id back into the provider bridge so it
                    # reconciles/polls the existing job instead of charging for a
                    # duplicate submission.
                    effective_step = planned.model_copy(deep=True)
                    effective_step.parameters["remote_operation_id"] = (
                        state.remote_operation_id
                    )

                artifact = await executor.execute_plan_step(
                    build=build,
                    step=effective_step,
                    completed_artifacts={
                        dependency: completed[dependency]
                        for dependency in planned.depends_on
                        if dependency in completed
                    },
                    idempotency_key=(
                        planned.idempotency_key
                        or f"{build.idempotency_key}:{planned.step_id}"
                    ),
                    report_remote_operation=report_remote,
                )
                artifact.project_id = build.project_id
                artifact.artifact_id = planned.output_artifact_id or artifact.artifact_id
                artifact.type = planned.output_artifact_type or artifact.type
                artifact.status = "draft"
                artifact.metadata = {
                    **artifact.metadata,
                    "build_id": build.id,
                    "plan_step_id": planned.step_id,
                    "capability": planned.capability,
                    "rebuild_capability": planned.capability,
                    "generation_parameters": dict(planned.parameters),
                    "estimated_cost": planned.estimated_cost,
                    "resolved_skills": [
                        value.model_dump(mode="json")
                        for value in planned.resolved_skills
                    ],
                    "skill_context": (
                        planned.skill_context.model_dump(mode="json")
                        if planned.skill_context is not None
                        else None
                    ),
                }
                if artifact.type == "video_spec" and isinstance(
                    planned.parameters.get("content"), dict,
                ):
                    artifact.metadata["content"] = deepcopy(
                        planned.parameters["content"],
                    )
                artifact = await self.repo.stage_artifact(artifact)
                completed[planned.step_id] = artifact
                state.status = "completed"
                state.result_artifact_version_id = artifact.id
                state.remote_operation_id = str(
                    artifact.provenance.get("remote_operation_id") or ""
                ) or None
                state.completed_at = now()
                await self.repo.update_build_step(state)
                return artifact
            except BaseException as exc:
                last_error = exc
                state.status = "failed"
                state.error = str(exc) or repr(exc)
                await self.repo.update_build_step(state)
        if last_error is None:
            raise RuntimeError(
                f"retry limit exhausted for build step {planned.step_id}"
            )
        raise last_error

    async def _execute_initial_build(
        self,
        *,
        build: Build,
        plan: RebuildPlan,
        executor: BuildStepExecutor,
    ) -> tuple[Build, ProjectVersion | None]:
        ordered = topological_steps(plan.items)
        persisted_steps = {
            item.plan_step_id: item
            for item in await self.repo.list_build_steps(build.project_id, build.id)
        }
        completed: dict[str, MediaArtifactVersion] = {}
        for item in persisted_steps.values():
            if item.status == "completed" and item.result_artifact_version_id:
                completed[item.plan_step_id] = await self.repo.get_artifact(
                    build.project_id, item.result_artifact_version_id,
                )
        for planned in ordered:
            if planned.action == "reuse" and planned.artifact_version_id:
                completed[planned.step_id] = await self.repo.get_artifact(
                    build.project_id, planned.artifact_version_id,
                )

        build.status = "running"
        build.message = "Executing initial video build"
        await self.repo.update_build(build)
        validations: list[ValidationResult] = []
        retry_limit = plan.video_spec.automation.max_artifact_retries if plan.video_spec else 1
        processed_parallel_groups: set[str] = set()
        for index, planned in enumerate(ordered):
            current = await self.repo.get_build(build.project_id, build.id)
            if current.status == "cancelled":
                raise ValueError("build was cancelled")
            state = persisted_steps[planned.step_id]
            if state.status == "completed":
                continue
            if planned.action == "validate":
                state.status = "running"
                state.started_at = state.started_at or now()
                await self.repo.update_build_step(state)
                targets = [completed[item] for item in planned.depends_on if item in completed]
                step_results: list[ValidationResult] = []
                for artifact in targets:
                    step_results.extend(await self.validate_artifact(
                        build_id=build.id, artifact=artifact,
                    ))
                validations.extend(step_results)
                if not step_results:
                    state.status = "failed"
                    state.error = "No validator handled the required artifacts"
                    await self.repo.update_build_step(state)
                    raise ValueError("initial build requires at least one validator result")
                failed_results = [item for item in step_results if not item.passed]
                if failed_results:
                    semantic_failure = any(
                        any(token in item.validator_id.lower() for token in (
                            "continuity", "semantic", "identity", "product",
                        ))
                        for item in failed_results
                    )
                    prior_checkpoints = await self.repo.list_build_checkpoints(
                        build.project_id, build.id,
                    )
                    repaired_before = any(
                        item.phase == "semantic_validation" for item in prior_checkpoints
                    )
                    if plan.schema_version != 2 or not semantic_failure or repaired_before:
                        state.status = "failed"
                        state.error = "; ".join(
                            issue
                            for result in failed_results
                            for issue in result.issues
                        ) or "Artifact validation failed"
                        await self.repo.update_build_step(state)
                        raise ValueError("initial build validation failed")
                    if not plan.video_spec_revision_id:
                        raise ValueError("staged build plan has no VideoSpec revision")
                    spec_revision = await self.repo.get_video_spec_revision(
                        plan.video_spec_revision_id,
                    )
                    session_id, user_id = build.session_id, build.user_id
                    if not session_id or not user_id:
                        project = await self.repo.get_project(build.project_id)
                        binding = await self.repo.latest_session_binding(
                            build.project_id, project.user_id,
                        )
                        session_id, user_id = binding.session_id, binding.user_id
                    state.status = "completed"
                    state.completed_at = now()
                    state.error = "Semantic validation requested one repair pass"
                    await self.repo.update_build_step(state)
                    issues_by_artifact: dict[str, list[str]] = {}
                    for result in failed_results:
                        issues_by_artifact.setdefault(
                            result.artifact_version_id, [],
                        ).extend(result.issues)
                    repair = PlanCheckpoint(
                        project_id=build.project_id,
                        build_id=build.id,
                        plan_id=plan.id,
                        workflow_id=plan.workflow_id,
                        session_id=session_id,
                        user_id=user_id,
                        phase="semantic_validation",
                        next_phase="semantic_repair",
                        required_artifact_types=[item.type for item in targets],
                        artifact_version_ids=[item.id for item in targets],
                        artifact_summaries=[_checkpoint_artifact_summary(
                            item,
                            issues=issues_by_artifact.get(item.id, []),
                        ) for item in targets],
                        resolved_sections=list(spec_revision.resolved_sections),
                        unresolved_sections=["semantic_repair"],
                        planner_instruction=(
                            "Revise only prompts or creative fields implicated by the validation "
                            "issues. This is the single allowed semantic repair pass."
                        ),
                        planning_mode="staged",
                        base_plan_revision=plan.current_revision,
                        base_spec_revision=spec_revision.revision,
                        semantic_repair_attempts=1,
                    )
                    await self.repo.create_checkpoint(repair)
                    return await self.repo.get_build(build.project_id, build.id), None
                state.status = "completed"
                state.completed_at = now()
                await self.repo.update_build_step(state)
            elif planned.action == "reuse":
                if planned.artifact_version_id:
                    completed[planned.step_id] = await self.repo.get_artifact(
                        build.project_id, planned.artifact_version_id,
                    )
                state.status = "completed"
                state.completed_at = now()
                await self.repo.update_build_step(state)
            else:
                group_name = planned.execution_group
                if group_name and group_name not in processed_parallel_groups:
                    grouped = [
                        candidate for candidate in ordered
                        if candidate.execution_group == group_name
                    ]
                    group_ids = {candidate.step_id for candidate in grouped}
                    invalid_dependencies = sorted({
                        dependency
                        for candidate in grouped
                        for dependency in candidate.depends_on
                        if dependency in group_ids
                    })
                    if invalid_dependencies:
                        raise ValueError(
                            f"parallel execution group {group_name!r} contains internal "
                            "dependencies: " + ", ".join(invalid_dependencies)
                        )
                    missing_dependencies = sorted({
                        dependency
                        for candidate in grouped
                        for dependency in candidate.depends_on
                        if (
                            dependency not in persisted_steps
                            or persisted_steps[dependency].status != "completed"
                        )
                    })
                    if missing_dependencies:
                        raise ValueError(
                            f"parallel execution group {group_name!r} started before its "
                            "dependencies completed: " + ", ".join(missing_dependencies)
                        )
                    limits = {
                        candidate.max_parallelism
                        for candidate in grouped
                        if candidate.max_parallelism is not None
                    }
                    if len(limits) > 1:
                        raise ValueError(
                            f"parallel execution group {group_name!r} has inconsistent limits"
                        )
                    limit = min(
                        next(iter(limits), len(grouped)),
                        self.max_parallel_generation_tasks,
                    )
                    semaphore = asyncio.Semaphore(max(1, limit))

                    async def run_group_step(
                        candidate: RebuildPlanItem,
                    ) -> MediaArtifactVersion | None:
                        candidate_state = persisted_steps[candidate.step_id]
                        if candidate_state.status == "completed":
                            return completed.get(candidate.step_id)
                        async with semaphore:
                            return await self._execute_initial_create_step(
                                build=build,
                                planned=candidate,
                                state=candidate_state,
                                completed=completed,
                                executor=executor,
                                retry_limit=retry_limit,
                            )

                    results = await asyncio.gather(
                        *(run_group_step(candidate) for candidate in grouped),
                        return_exceptions=True,
                    )
                    processed_parallel_groups.add(group_name)
                    failures = [result for result in results if isinstance(result, BaseException)]
                    if failures:
                        raise failures[0]
                elif not group_name:
                    await self._execute_initial_create_step(
                        build=build,
                        planned=planned,
                        state=state,
                        completed=completed,
                        executor=executor,
                        retry_limit=retry_limit,
                    )
                build.status = "running"
                build.actual_cost = round(
                    sum(
                        candidate.estimated_cost
                        for candidate in ordered
                        if persisted_steps[candidate.step_id].status == "completed"
                    ),
                    6,
                )

            build.progress = (index + 1) / max(1, len(ordered) + 1)
            build.message = f"Completed {index + 1} of {len(ordered)} build steps"
            await self.repo.update_build(build)

        if plan.schema_version == 2 and plan.next_checkpoint is not None:
            if not plan.video_spec_revision_id:
                raise ValueError("staged build plan has no VideoSpec revision")
            spec_revision = await self.repo.get_video_spec_revision(
                plan.video_spec_revision_id,
            )
            session_id = build.session_id
            user_id = build.user_id
            if not session_id or not user_id:
                project = await self.repo.get_project(build.project_id)
                binding = await self.repo.latest_session_binding(
                    build.project_id, project.user_id,
                )
                session_id = binding.session_id
                user_id = binding.user_id
            definition = plan.next_checkpoint
            required = set(definition.required_artifact_types)
            aliases = {
                "source_image": {"source_image", "image", "character_reference"},
                "source_audio": {"source_audio", "audio", "audio_bgm", "audio_cut"},
            }
            relevant = [
                artifact for artifact in completed.values()
                if not required
                or artifact.type in required
                or any(
                    expected in required and artifact.type in accepted
                    for expected, accepted in aliases.items()
                )
            ]
            missing_types = [
                artifact_type for artifact_type in required
                if not any(
                    artifact.type == artifact_type
                    or artifact.type in aliases.get(artifact_type, set())
                    for artifact in completed.values()
                )
            ]
            if missing_types:
                raise ValueError(
                    "checkpoint required artifacts are missing: " + ", ".join(missing_types)
                )
            checkpoint = PlanCheckpoint(
                project_id=build.project_id,
                build_id=build.id,
                plan_id=plan.id,
                workflow_id=plan.workflow_id,
                session_id=session_id,
                user_id=user_id,
                phase=definition.phase,
                next_phase=definition.next_phase,
                required_artifact_types=list(definition.required_artifact_types),
                artifact_version_ids=[artifact.id for artifact in relevant],
                artifact_summaries=[
                    _checkpoint_artifact_summary(artifact) for artifact in relevant
                ],
                resolved_sections=list(spec_revision.resolved_sections),
                unresolved_sections=list(dict.fromkeys([
                    *spec_revision.unresolved_sections,
                    *definition.resolves,
                ])),
                planner_instruction=definition.planner_instruction,
                planning_mode=definition.planning_mode,
                base_plan_revision=plan.current_revision,
                base_spec_revision=spec_revision.revision,
                max_delivery_attempts=definition.max_planning_attempts,
            )
            await self.repo.create_checkpoint(checkpoint)
            waiting = await self.repo.get_build(build.project_id, build.id)
            return waiting, None

        dependencies: list[ArtifactDependency] = []
        for planned in ordered:
            target = completed.get(planned.step_id)
            if target is None:
                continue
            for dependency_step in planned.depends_on:
                source = completed.get(dependency_step)
                if source is not None:
                    dependencies.append(ArtifactDependency(
                        project_id=build.project_id,
                        source_version_id=source.id,
                        target_version_id=target.id,
                        relation="build_step_dependency",
                    ))
        if plan.schema_version == 2 and plan.video_spec is None:
            raise ValueError("final staged build requires a complete VideoSpec")
        result = await self.repo.commit_initial_build(
            build_id=build.id,
            artifacts=list(completed.values()),
            dependencies=dependencies,
            validation_results=validations,
        )
        context = PluginContext(project_id=build.project_id, build_id=build.id)
        for artifact in completed.values():
            for loaded in self.plugins.loaded:
                await loaded.implementation.on_artifact_committed(context, artifact)
        return result

    async def add_artifact(
        self, artifact: MediaArtifactVersion, dependencies: list[ArtifactDependency] | None = None,
    ) -> MediaArtifactVersion:
        stored = await self.repo.add_artifact(artifact, dependencies)
        context = PluginContext(project_id=stored.project_id)
        for loaded in self.plugins.loaded:
            await loaded.implementation.on_artifact_committed(context, stored)
        return stored

    async def validate_artifact(
        self, *, build_id: str, artifact: MediaArtifactVersion,
    ) -> list[ValidationResult]:
        results: list[ValidationResult] = []
        context = PluginContext(project_id=artifact.project_id, build_id=build_id)
        for loaded in self.plugins.loaded:
            if loaded.manifest.contributions.validators:
                results.extend(await loaded.implementation.validate_artifact(context, artifact))
        return results

    async def select_artifact(
        self, *, project_id: str, version_id: str, base_project_version_id: str, idempotency_key: str,
    ) -> tuple[MediaArtifactVersion, ProjectVersion]:
        return await self.repo.select_artifact(
            project_id=project_id,
            version_id=version_id,
            base_version_id=base_project_version_id,
            idempotency_key=idempotency_key,
        )

    async def export_project(
        self, *, project_id: str, base_project_version_id: str, idempotency_key: str, format: str,
    ) -> ExportRecord:
        return await self.repo.create_export(
            project_id=project_id,
            base_version_id=base_project_version_id,
            idempotency_key=idempotency_key,
            format=format,
        )

    async def restore_project_version(
        self, *, project_id: str, restore_version_id: str,
        base_project_version_id: str, idempotency_key: str,
    ) -> ProjectVersion:
        return await self.repo.restore_project_version(
            project_id=project_id,
            restore_version_id=restore_version_id,
            base_version_id=base_project_version_id,
            idempotency_key=idempotency_key,
        )
