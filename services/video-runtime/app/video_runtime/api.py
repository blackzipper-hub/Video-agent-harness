from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .repository import ProjectVersionConflict
from .runtime import VideoBuildRuntime
from .identity import IdentityResolver, ServiceOrLocalIdentityResolver
from .models import VideoSpec
from .plugins import PluginDependencyError, VideoPluginManifest
from .plugins.registry import configured_plugin_roots


router = APIRouter(prefix="/api/video", tags=["video-runtime"])
runtime = VideoBuildRuntime()
identity_resolver: IdentityResolver = ServiceOrLocalIdentityResolver()


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiBody(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)


class CreateProjectBody(ApiBody):
    title: str = Field(min_length=1, max_length=200)
    session_id: str | None = None


class PreviewBody(ApiBody):
    change: str = Field(min_length=1)
    target_artifact_version_ids: list[str] = Field(default_factory=list)


class EditPreviewBody(ApiBody):
    base_project_version_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)
    description: str = ""
    edits: list[dict] = Field(min_length=1)


class RebuildBody(ApiBody):
    plan_id: str
    base_project_version_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)


class PlanBody(ApiBody):
    base_project_version_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)
    video_spec: VideoSpec


class BuildBody(ApiBody):
    plan_id: str
    base_project_version_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)


class SelectBody(ApiBody):
    base_project_version_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)


class ExportBody(ApiBody):
    base_project_version_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)
    format: Literal["mp4", "mov", "webm", "project"]


class RestoreVersionBody(ApiBody):
    base_project_version_id: str
    idempotency_key: str = Field(min_length=1, max_length=200)


def get_runtime() -> VideoBuildRuntime:
    return runtime


def set_runtime(value: VideoBuildRuntime) -> None:
    global runtime
    runtime = value


def set_identity_resolver(value: IdentityResolver) -> None:
    global identity_resolver
    identity_resolver = value


def _plugin_manifest_path(plugin_id: str) -> Path:
    for root in configured_plugin_roots():
        candidate = (root / plugin_id.replace(".", "-") / "video-plugin.yaml").resolve()
        if candidate.is_file() and candidate.is_relative_to(root):
            return candidate
        for manifest in root.glob("*/video-plugin.yaml"):
            if manifest.parent.resolve().is_relative_to(root):
                try:
                    import yaml
                    if yaml.safe_load(manifest.read_text(encoding="utf-8")).get("id") == plugin_id:
                        return manifest.resolve()
                except (OSError, AttributeError):
                    continue
    raise LookupError(f"plugin manifest not found: {plugin_id}")


async def _identity(
    request: Request,
) -> tuple[str, str | None]:
    return await identity_resolver.resolve(request)


async def _owned_project(build_runtime: VideoBuildRuntime, project_id: str, user_id: str):
    try:
        project = await build_runtime.repo.get_project(project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if project.user_id != user_id:
        raise HTTPException(status_code=403, detail="project does not belong to caller")
    return project


async def _owned_and_bound(
    build_runtime: VideoBuildRuntime,
    project_id: str,
    identity: tuple[str, str | None],
):
    user_id, session_id = identity
    project = await _owned_project(build_runtime, project_id, user_id)
    if session_id:
        await build_runtime.bind_session(
            project_id=project_id, session_id=session_id, user_id=user_id,
        )
    return project


async def _snapshot(build_runtime: VideoBuildRuntime, project_id: str, user_id: str) -> dict:
    project = await _owned_project(build_runtime, project_id, user_id)
    artifacts = await build_runtime.repo.current_artifacts(project_id)
    edges = await build_runtime.repo.current_dependencies(project_id)
    builds = await build_runtime.repo.active_builds(project_id)
    return {
        "projectId": project.id,
        "title": project.title,
        "status": project.status,
        "currentVersionId": project.current_version_id,
        "artifactCount": len(artifacts),
        "activeBuildIds": [item.id for item in builds],
        "summary": f"{len(artifacts)} artifacts, {len(edges)} active dependencies",
        "artifacts": [item.model_dump(mode="json") for item in artifacts],
        "artifactEdges": [item.model_dump(mode="json") for item in edges],
        "activeBuilds": [item.model_dump(mode="json") for item in builds],
    }


def _logical_artifact_id(project_id: str, artifact) -> str:
    raw = artifact.artifact_id
    prefix = f"{project_id}:"
    step = raw[len(prefix):] if raw.startswith(prefix) else str(
        artifact.metadata.get("plan_step_id") or raw
    )
    if step == "character-reference":
        return "character:shared:reference"
    if step.startswith("character-") and step.endswith("-reference"):
        return f"character:{step[len('character-'):-len('-reference')]}:reference"
    if step == "characters":
        return "character:shared:definition"
    if step.startswith("shot-"):
        tail = step[5:]
        for suffix, label in (("-keyframe", "keyframe:0"), ("-video", "clip"), ("-tail", "tail")):
            if tail.endswith(suffix):
                return f"shot:{tail[:-len(suffix)]}:{label}"
    return {
        "spec": "project:video-spec",
        "script": "project:script",
        "storyboard": "project:storyboard",
        "narration": "audio:narration",
        "bgm": "audio:bgm",
        "timeline": "timeline:main",
        "subtitles": "subtitle:main",
        "final-video": "video:final",
        # Legacy Cuti builds named the pre-subtitle/final assembly step this way.
        # Project it onto the stable workspace identity so migrated projects do
        # not expose a second final-video concept to the editor.
        "assembled-video": "video:final",
    }.get(step, step)


def _artifact_payload(project_id: str, artifact, selected_ids: set[str]) -> dict:
    value = artifact.model_dump(mode="json")
    value["logicalId"] = _logical_artifact_id(project_id, artifact)
    value["isSelected"] = artifact.id in selected_ids
    return value


async def _workspace(build_runtime: VideoBuildRuntime, project_id: str, user_id: str) -> dict:
    project = await _owned_project(build_runtime, project_id, user_id)
    current_version = await build_runtime.repo.get_project_version(project.current_version_id)
    artifacts = await build_runtime.repo.list_artifact_versions(project_id)
    selected_ids = set(current_version.selections.values())
    versions = await build_runtime.repo.list_project_versions(project_id)
    builds = await build_runtime.repo.list_builds(project_id)
    build_payloads = []
    for build in builds[:10]:
        steps = await build_runtime.repo.list_build_steps(project_id, build.id)
        validations = await build_runtime.repo.list_validation_results(project_id, build.id)
        build_payloads.append({
            **_build_payload(build),
            "steps": [item.model_dump(mode="json") for item in steps],
            "validations": [item.model_dump(mode="json") for item in validations],
        })
    spec = None
    for artifact in reversed(artifacts):
        if artifact.id not in selected_ids or artifact.type != "video_spec":
            continue
        content = artifact.metadata.get("content")
        if isinstance(content, dict):
            spec = content
            break
    grouped: dict[str, list[dict]] = {}
    for artifact in artifacts:
        payload = _artifact_payload(project_id, artifact, selected_ids)
        grouped.setdefault(payload["logicalId"], []).append(payload)
    return {
        "project": project.model_dump(mode="json"),
        "currentProjectVersion": current_version.model_dump(mode="json"),
        "videoSpec": spec,
        "artifacts": [_artifact_payload(project_id, item, selected_ids) for item in artifacts],
        "artifactGroups": grouped,
        "artifactEdges": [
            item.model_dump(mode="json")
            for item in await build_runtime.repo.current_dependencies(project_id)
        ],
        "builds": build_payloads,
        "projectVersions": [item.model_dump(mode="json") for item in reversed(versions)],
    }


@router.post("/projects")
async def create_project(
    body: CreateProjectBody,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    user_id, header_session_id = identity
    project, _version = await build_runtime.create_project(
        user_id=user_id, title=body.title, idempotency_key=idempotency_key,
    )
    session_id = body.session_id or header_session_id
    if session_id:
        await build_runtime.bind_session(project_id=project.id, session_id=session_id, user_id=user_id)
    return {"data": await _snapshot(build_runtime, project.id, user_id)}


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    return {"data": await _snapshot(build_runtime, project_id, identity[0])}


@router.get("/projects/{project_id}/workspace")
async def get_project_workspace(
    project_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    return {"data": await _workspace(build_runtime, project_id, identity[0])}


@router.get("/plugins")
async def list_plugins(
    _identity_value: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    import yaml
    enabled = {item.id: item for item in build_runtime.plugins.manifests}
    available = dict(enabled)
    for root in configured_plugin_roots():
        for path in root.glob("*/video-plugin.yaml"):
            try:
                candidate = VideoPluginManifest.model_validate(
                    yaml.safe_load(path.read_text(encoding="utf-8")),
                )
            except Exception:
                continue
            available[candidate.id] = candidate
    return {"data": [
        {**manifest.model_dump(mode="json"), "enabled": plugin_id in enabled}
        for plugin_id, manifest in sorted(available.items())
    ]}


def _workflow_views(build_runtime: VideoBuildRuntime) -> list[dict]:
    workflows: list[dict] = []
    for loaded in build_runtime.plugins.loaded:
        describe = getattr(loaded.implementation, "describe_workflows", None)
        if callable(describe):
            workflows.extend(describe())
            continue
        workflows.extend({
            "id": workflow_id,
            "title": workflow_id,
            "mode": "plugin",
            "parameters": {},
            "pipeline": [],
            "requiresKeyframe": None,
            "entrypoints": [],
            "skillDependencies": list(loaded.manifest.skills),
            "source": "plugin",
            "pluginId": loaded.manifest.id,
            "available": True,
            "unavailableReason": None,
            "requiredCapabilities": [],
            "missingCapabilities": [],
            "userSelectable": True,
            "executionKind": "plugin",
        } for workflow_id in loaded.manifest.contributions.workflows)
    for item in workflows:
        if build_runtime.skills.catalog.has(item["id"]):
            metadata = build_runtime.skills.catalog.load(item["id"]).metadata
            item["description"] = metadata.description
        else:
            item.setdefault("description", item["title"])
    return sorted(workflows, key=lambda item: item["id"])


def _skill_resources(
    build_runtime: VideoBuildRuntime,
    skill_id: str,
) -> tuple[list[str], list[dict]]:
    resources = build_runtime.skills.catalog.list_resources(skill_id)
    instructions = build_runtime.skills.catalog.load(skill_id).instructions
    resource_set = set(resources)
    # A large Skill bundle can contain fonts, screenshots, tests, and hundreds
    # of unrelated documents.  Load only files linked directly by SKILL.md;
    # return the complete resource inventory separately for audit/discovery.
    linked: list[str] = []
    for target in re.findall(r"\]\(([^)]+)\)", instructions):
        parsed = urlsplit(target.strip().strip("<>"))
        if parsed.scheme or parsed.netloc:
            continue
        path = unquote(parsed.path).removeprefix("./")
        if path in resource_set and path not in linked:
            linked.append(path)
    readable: list[dict] = []
    remaining_bytes = 64 * 1024
    for path in linked:
        try:
            content = build_runtime.skills.catalog.read_resource(
                skill_id, path, max_bytes=32 * 1024,
            )
        except ValueError:
            # Images, archives, fonts, and other binary assets remain visible
            # in the manifest but must never be injected into the LLM context
            # as if they were UTF-8 instructions.
            continue
        encoded_size = len(content.encode("utf-8"))
        if encoded_size > remaining_bytes:
            continue
        readable.append({"path": path, "content": content})
        remaining_bytes -= encoded_size
    return resources, readable


@router.get("/workflows")
async def list_workflows(
    _identity_value: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    return {"data": _workflow_views(build_runtime)}


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    _identity_value: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    view = next((item for item in _workflow_views(build_runtime) if item["id"] == workflow_id), None)
    if view is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    instructions = ""
    resources: list[str] = []
    if build_runtime.skills.catalog.has(workflow_id):
        loaded = build_runtime.skills.catalog.load(workflow_id)
        instructions = loaded.instructions
        resources, resource_contents = _skill_resources(build_runtime, workflow_id)
    else:
        resource_contents = []
    return {"data": {
        **view,
        "instructions": instructions,
        "resources": resources,
        "resourceContents": resource_contents,
    }}


@router.get("/skills/{skill_id}")
async def get_skill(
    skill_id: str,
    _identity_value: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    if not build_runtime.skills.catalog.has(skill_id):
        raise HTTPException(status_code=404, detail="skill not found")
    loaded = build_runtime.skills.catalog.load(skill_id)
    raw = dict(loaded.metadata.metadata or {})
    resources, resource_contents = _skill_resources(build_runtime, skill_id)
    return {"data": {
        "id": skill_id,
        "description": loaded.metadata.description,
        "kind": str(raw.get("kind") or "helper"),
        "instructions": loaded.instructions,
        "resources": resources,
        "resourceContents": resource_contents,
    }}


@router.post("/plugins/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    _identity_value: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    try:
        manifest = await build_runtime.plugins.load(_plugin_manifest_path(plugin_id))
        build_runtime.refresh_plugin_execution()
        return {"data": {**manifest.model_dump(mode="json"), "enabled": True}}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, PermissionError, PluginDependencyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/plugins/{plugin_id}/install")
async def install_plugin(
    plugin_id: str,
    identity_value: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    return await enable_plugin(plugin_id, identity_value, build_runtime)


@router.post("/plugins/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    _identity_value: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    try:
        await build_runtime.plugins.unload(plugin_id)
        build_runtime.refresh_plugin_execution()
        return {"data": {"id": plugin_id, "enabled": False}}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PluginDependencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/plugins/{plugin_id}")
async def uninstall_plugin(
    plugin_id: str,
    identity_value: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    return await disable_plugin(plugin_id, identity_value, build_runtime)


@router.post("/plugins/{plugin_id}/upgrade")
async def upgrade_plugin(
    plugin_id: str,
    _identity_value: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    try:
        manifest = await build_runtime.plugins.upgrade(_plugin_manifest_path(plugin_id))
        build_runtime.refresh_plugin_execution()
        return {"data": {**manifest.model_dump(mode="json"), "enabled": True}}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, PermissionError, PluginDependencyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/changes/preview")
async def preview_change(
    project_id: str,
    body: PreviewBody,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        plan = await build_runtime.preview_change(
            project_id=project_id,
            description=body.change,
            target_artifact_version_ids=body.target_artifact_version_ids,
            idempotency_key=idempotency_key,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"data": {
        "planId": plan.id,
        "projectId": plan.project_id,
        "baseProjectVersionId": plan.base_project_version_id,
        "staleArtifactIds": plan.ids_for("rebuild"),
        "validationArtifactIds": plan.ids_for("validate"),
        "reusedArtifactIds": plan.ids_for("reuse"),
        "rebuildOrder": [
            item.artifact_version_id
            for item in sorted(
                (item for item in plan.items if item.action == "rebuild"),
                key=lambda item: item.order if item.order is not None else -1,
            )
        ],
        "estimatedCost": plan.estimated_cost,
    }}


@router.post("/projects/{project_id}/edits/resolve")
async def resolve_edits(
    project_id: str,
    body: EditPreviewBody,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    """Validate structured edits. Natural-language resolution remains the Agent's job."""
    await _owned_and_bound(build_runtime, project_id, identity)
    return {"data": {"description": body.description, "edits": body.edits}}


@router.post("/projects/{project_id}/edits/preview")
async def preview_edits(
    project_id: str,
    body: EditPreviewBody,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        plan = await build_runtime.preview_edits(
            project_id=project_id,
            base_project_version_id=body.base_project_version_id,
            edits=body.edits,
            description=body.description,
            idempotency_key=body.idempotency_key,
        )
    except ProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"data": _plan_payload(plan)}


@router.post("/projects/{project_id}/plans")
async def create_build_plan(
    project_id: str,
    body: PlanBody,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        plan = await build_runtime.plan_project(
            project_id=project_id,
            base_project_version_id=body.base_project_version_id,
            video_spec=body.video_spec,
            idempotency_key=body.idempotency_key,
        )
    except ProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": _plan_payload(plan)}


@router.get("/projects/{project_id}/plans/{plan_id}")
async def get_build_plan(
    project_id: str,
    plan_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        plan = await build_runtime.repo.get_plan(plan_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if plan.project_id != project_id:
        raise HTTPException(status_code=404, detail="build plan not found")
    return {"data": _plan_payload(plan)}


@router.post("/projects/{project_id}/builds")
async def start_build(
    project_id: str,
    body: BuildBody,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        build = await build_runtime.start_build(
            project_id=project_id,
            plan_id=body.plan_id,
            base_project_version_id=body.base_project_version_id,
            idempotency_key=body.idempotency_key,
            session_id=identity[1],
            user_id=identity[0],
        )
    except ProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"data": _build_payload(build)}


@router.get("/projects/{project_id}/builds/{build_id}")
async def get_general_build(
    project_id: str,
    build_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        return {"data": _build_payload(await build_runtime.repo.get_build(project_id, build_id))}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/builds/{build_id}/cancel")
async def cancel_general_build(
    project_id: str,
    build_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        return {"data": _build_payload(await build_runtime.cancel_build(
            project_id=project_id, build_id=build_id,
        ))}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/builds/{build_id}/retry")
async def retry_general_build(
    project_id: str,
    build_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        return {"data": _build_payload(await build_runtime.retry_failed_build(
            project_id=project_id, build_id=build_id,
        ))}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{project_id}/builds/{build_id}/steps")
async def list_build_steps(
    project_id: str,
    build_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        steps = await build_runtime.repo.list_build_steps(project_id, build_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"data": [item.model_dump(mode="json") for item in steps]}


@router.get("/projects/{project_id}/builds/{build_id}/validations")
async def list_build_validations(
    project_id: str,
    build_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        results = await build_runtime.repo.list_validation_results(project_id, build_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"data": [item.model_dump(mode="json") for item in results]}


@router.post("/projects/{project_id}/rebuilds")
async def apply_rebuild(
    project_id: str,
    body: RebuildBody,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        build = await build_runtime.apply_rebuild(
            project_id=project_id,
            plan_id=body.plan_id,
            base_project_version_id=body.base_project_version_id,
            idempotency_key=body.idempotency_key,
            session_id=identity[1],
            user_id=identity[0],
        )
    except ProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"data": _build_payload(build)}


@router.get("/projects/{project_id}/rebuilds/{build_id}")
async def get_build(
    project_id: str,
    build_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        return {"data": _build_payload(await build_runtime.repo.get_build(project_id, build_id))}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/rebuilds/{build_id}/cancel")
async def cancel_build(
    project_id: str,
    build_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        return {"data": _build_payload(await build_runtime.cancel_build(
            project_id=project_id, build_id=build_id,
        ))}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/artifacts/{version_id}/select")
async def select_artifact(
    project_id: str,
    version_id: str,
    body: SelectBody,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        artifact, version = await build_runtime.select_artifact(
            project_id=project_id,
            version_id=version_id,
            base_project_version_id=body.base_project_version_id,
            idempotency_key=body.idempotency_key,
        )
    except ProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"data": {
        "projectId": project_id,
        "artifactId": artifact.artifact_id,
        "versionId": artifact.id,
        "projectVersionId": version.id,
    }}


@router.post("/projects/{project_id}/exports")
async def export_project(
    project_id: str,
    body: ExportBody,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        export = await build_runtime.export_project(
            project_id=project_id,
            base_project_version_id=body.base_project_version_id,
            idempotency_key=body.idempotency_key,
            format=body.format,
        )
    except ProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": {
        "exportId": export.id,
        "projectId": export.project_id,
        "status": export.status,
        "uri": export.uri,
    }}


@router.get("/projects/{project_id}/versions")
async def list_versions(
    project_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    project = await _owned_and_bound(build_runtime, project_id, identity)
    versions = await build_runtime.repo.list_project_versions(project_id)
    return {"data": [{
        "id": item.id,
        "parentVersionId": item.parent_version_id,
        "timelineVersionId": item.timeline_version_id,
        "artifactVersionIds": list(item.selections.values()),
        "isCurrent": item.id == project.current_version_id,
        "createdAt": item.created_at.isoformat(),
    } for item in reversed(versions)]}


@router.post("/projects/{project_id}/versions/{version_id}/restore")
async def restore_version(
    project_id: str,
    version_id: str,
    body: RestoreVersionBody,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    try:
        version = await build_runtime.restore_project_version(
            project_id=project_id,
            restore_version_id=version_id,
            base_project_version_id=body.base_project_version_id,
            idempotency_key=body.idempotency_key,
        )
    except ProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"data": {"projectId": project_id, "projectVersionId": version.id}}


@router.get("/projects/{project_id}/events", response_model=None)
async def project_events(
    request: Request,
    project_id: str,
    identity: Annotated[tuple[str, str | None], Depends(_identity)],
    build_runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    after: int = Query(default=0, ge=0),
) -> StreamingResponse | dict:
    await _owned_and_bound(build_runtime, project_id, identity)
    last_event_id = request.headers.get("last-event-id", "")
    try:
        cursor_start = max(after, int(last_event_id)) if last_event_id else after
    except ValueError:
        cursor_start = after
    events = await build_runtime.repo.list_events(project_id, cursor_start)
    if "text/event-stream" not in request.headers.get("accept", ""):
        return {"data": [event.model_dump(mode="json") for event in events]}

    async def stream() -> AsyncIterator[str]:
        cursor = cursor_start
        while not await request.is_disconnected():
            pending = await build_runtime.repo.list_events(project_id, cursor)
            if not pending:
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)
                continue
            for event in pending:
                cursor = event.sequence
                payload = json.dumps(event.payload, ensure_ascii=False)
                yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


def _build_payload(build) -> dict:
    payload = {
        "buildId": build.id,
        "projectId": build.project_id,
        "status": build.status,
        "progress": build.progress,
        "message": build.message,
        "kind": build.kind,
        "estimatedCost": build.estimated_cost,
        "actualCost": build.actual_cost,
    }
    if build.project_version_id is not None:
        payload["projectVersionId"] = build.project_version_id
    if build.error is not None:
        payload["error"] = build.error
    return payload


def _plan_payload(plan) -> dict:
    return {
        "planId": plan.id,
        "projectId": plan.project_id,
        "kind": plan.kind,
        "status": plan.status,
        "baseProjectVersionId": plan.base_project_version_id,
        "workflowId": plan.workflow_id,
        "videoSpec": plan.video_spec.model_dump(mode="json") if plan.video_spec else None,
        "shotCount": len(plan.video_spec.shots) if plan.video_spec else 0,
        "estimatedCost": plan.estimated_cost,
        "steps": [item.model_dump(mode="json") for item in plan.items],
    }
