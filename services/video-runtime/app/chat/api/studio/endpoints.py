"""Feature-flagged Studio API built on the durable Deep Agent V2 harness."""
from __future__ import annotations

import os
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, Security, UploadFile
from pydantic import BaseModel, Field

from app.chat.schemas import ResponseModel
from app.chat.services.auth_service import auth_service
from app.chat.v2.container import get_harness, get_skill_catalog
from app.chat.v2.models import InputFile
from app.domain.skills import make_skill_lock
from app.orchestration.planner import plan_generic_sample

router = APIRouter(prefix="/studio", tags=["studio"])


class StudioProjectRequest(BaseModel):
    objective: str = Field(min_length=1)
    thread_id: str | None = None
    user_option: dict[str, Any] | None = None
    input_files: list[InputFile] = Field(default_factory=list)


class StudioCommandRequest(StudioProjectRequest):
    pass


class StudioSkillEnableRequest(BaseModel):
    enabled: bool = True


def _enabled_for(user_id: str) -> bool:
    if os.getenv("STUDIO_DYNAMIC_GRAPH_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        return False
    allowlist = {value.strip() for value in os.getenv("STUDIO_DYNAMIC_GRAPH_ROLLOUT_USERS", "").split(",") if value.strip()}
    return not allowlist or user_id in allowlist


def _skill_install_enabled() -> bool:
    """External upload is intentionally a separate rollout from Studio itself."""
    return os.getenv("STUDIO_SKILL_INSTALL_ENABLED", "false").lower() in {"1", "true", "yes"}


def _require_harness(user_id: str):
    if not _enabled_for(user_id):
        raise HTTPException(status_code=404, detail="Studio beta is not enabled for this user")
    harness = get_harness()
    if harness is None:
        raise HTTPException(status_code=503, detail="Deep Agent V2 harness is unavailable")
    return harness


async def _run_for_thread(harness, user_id: str, thread_id: str):
    run = await harness.repo.get_run_by_thread(user_id, thread_id)
    if not run:
        raise HTTPException(status_code=404, detail="Studio project not found")
    return run


@router.post("/projects")
async def create_project(
    request: StudioProjectRequest,
    app_language: str | None = Header(default=None, alias="X-App-Language"),
    user_id: str = Security(auth_service.get_current_user),
):
    harness = _require_harness(user_id)
    run = await harness.create_run(
        user_id=user_id,
        objective=request.objective,
        idempotency_key=str(uuid.uuid4()),
        thread_id=request.thread_id,
        user_option=request.user_option,
        input_files=request.input_files,
        output_language=app_language,
    )
    return ResponseModel.success(data={"project": run, "suggested_plan": [item.model_dump() for item in plan_generic_sample(request.objective, has_image=any(item.type == "image" for item in request.input_files), has_audio=any(item.type == "audio" for item in request.input_files))]})


@router.get("/projects/{thread_id}")
async def get_project(thread_id: str, user_id: str = Security(auth_service.get_current_user)):
    harness = _require_harness(user_id)
    run = await _run_for_thread(harness, user_id, thread_id)
    return ResponseModel.success(data=await harness.project_snapshot(run.id))


@router.post("/projects/{thread_id}/commands")
async def add_command(
    thread_id: str,
    request: StudioCommandRequest,
    app_language: str | None = Header(default=None, alias="X-App-Language"),
    user_id: str = Security(auth_service.get_current_user),
):
    harness = _require_harness(user_id)
    run = await _run_for_thread(harness, user_id, thread_id)
    updated = await harness.add_message(
        run.id, user_id, request.objective, str(uuid.uuid4()),
        user_option=request.user_option, input_files=request.input_files,
        thread_id=thread_id, output_language=app_language,
    )
    return ResponseModel.success(data={"project": updated, "suggested_plan": [item.model_dump() for item in plan_generic_sample(request.objective, has_image=any(item.type == "image" for item in request.input_files), has_audio=any(item.type == "audio" for item in request.input_files))]})


@router.get("/projects/{thread_id}/events")
async def get_events(thread_id: str, user_id: str = Security(auth_service.get_current_user)):
    harness = _require_harness(user_id)
    run = await _run_for_thread(harness, user_id, thread_id)
    return ResponseModel.success(data=await harness.repo.get_events(run.id))


@router.post("/projects/{thread_id}/artifacts/{artifact_version_id}/select")
async def select_artifact(thread_id: str, artifact_version_id: str, user_id: str = Security(auth_service.get_current_user)):
    harness = _require_harness(user_id)
    await _run_for_thread(harness, user_id, thread_id)
    return ResponseModel.success(data=await harness.repo.select_artifact(thread_id, artifact_version_id))


@router.get("/skills")
async def list_skills(user_id: str = Security(auth_service.get_current_user)):
    _require_harness(user_id)
    catalog = get_skill_catalog()
    return ResponseModel.success(data=[] if catalog is None else catalog.prompt_view())


@router.post("/skills/install")
async def install_external_skill(
    bundle: UploadFile = File(...),
    overwrite: bool = Form(False),
    user_id: str = Security(auth_service.get_current_user),
):
    """Install a safe external Skill bundle under a separate rollout flag.

    Knowledge/workflow Skills are installable now. Executable bundles require
    a live sandbox worker and are rejected instead of being silently disabled.
    """
    _require_harness(user_id)
    if not _skill_install_enabled():
        raise HTTPException(status_code=403, detail="External Skill installation is not enabled")
    from app.chat.config import get_settings
    from app.chat.v2.container import get_capability_registry, get_skill_catalog, reload_v2_skills
    from app.chat.v2.skill_install import (
        MAX_BUNDLE_BYTES, SkillInstallError, extract_skill_archive,
        install_skill_directory, validate_skill_directory,
    )
    from app.chat.v2.skill_catalog import SkillCatalog

    catalog = get_skill_catalog()
    registry = get_capability_registry()
    if catalog is None or registry is None:
        raise HTTPException(status_code=503, detail="Skill runtime is unavailable")
    try:
        payload = await bundle.read(MAX_BUNDLE_BYTES + 1)
        if len(payload) > MAX_BUNDLE_BYTES:
            raise SkillInstallError(f"skill archive exceeds {MAX_BUNDLE_BYTES} bytes")
        with tempfile.TemporaryDirectory(prefix="cuti-studio-skill-") as staging:
            source = extract_skill_archive(payload, Path(staging))
            validate_skill_directory(source)
            candidate = SkillCatalog([source.parent]).load(source.name)
            if candidate.contract is not None and not get_settings().DEEP_AGENT_V2_SANDBOX_ENABLED:
                raise SkillInstallError(
                    "executable Skills require an enabled sandbox worker; install a knowledge/workflow Skill instead"
                )
            name = install_skill_directory(
                source,
                get_settings().deep_agent_external_skill_root(),
                catalog=catalog,
                registry=registry,
                overwrite=overwrite,
            )
        count = reload_v2_skills()
    except (SkillInstallError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ResponseModel.success(data={"name": name, "skill_count": count})


@router.get("/projects/{thread_id}/skills")
async def list_project_skills(
    thread_id: str,
    user_id: str = Security(auth_service.get_current_user),
):
    harness = _require_harness(user_id)
    run = await _run_for_thread(harness, user_id, thread_id)
    return ResponseModel.success(data=run.skill_locks)


@router.post("/projects/{thread_id}/skills/{skill_id}/enable")
async def set_project_skill_enabled(
    thread_id: str,
    skill_id: str,
    request: StudioSkillEnableRequest,
    user_id: str = Security(auth_service.get_current_user),
):
    """Lock a catalog Skill to one project without changing global installation."""
    harness = _require_harness(user_id)
    run = await _run_for_thread(harness, user_id, thread_id)
    catalog = get_skill_catalog()
    if catalog is None or not catalog.has(skill_id):
        raise HTTPException(status_code=404, detail="Skill is not installed")
    skill = catalog.load(skill_id)
    if not skill.metadata.enabled:
        raise HTTPException(status_code=409, detail="Skill is globally disabled")
    lock = make_skill_lock(run.project_id, skill.metadata).model_copy(
        update={"enabled": request.enabled},
    )
    run.skill_locks = [item for item in run.skill_locks if item.skill_id != skill_id]
    run.skill_locks.append(lock)
    if request.enabled and skill_id not in run.activated_skills:
        run.activated_skills.append(skill_id)
    if not request.enabled:
        run.activated_skills = [item for item in run.activated_skills if item != skill_id]
    await harness.repo.save_run(run)
    await harness._emit(run.id, "project.skill_lock.updated", {
        "skill_id": skill_id,
        "enabled": request.enabled,
        "version": lock.version,
        "digest": lock.digest,
    })
    return ResponseModel.success(data=lock)
