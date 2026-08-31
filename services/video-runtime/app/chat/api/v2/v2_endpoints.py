from __future__ import annotations

import json
import re
import secrets
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Header, Query, Request, Security, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.chat.config import get_settings
from app.chat.exceptions import BusinessException, BusinessExceptionCode
from app.chat.schemas import ResponseModel
from app.chat.services.auth_service import auth_service
from app.chat.services.agent.prompt_shield import input_rail
from app.chat.utils.file_utils import process_uploaded_files
from app.chat.v2.container import get_harness
from app.chat.v2.language import LanguageSkillConflictError
from app.chat.v2.models import (
    AgentRun, ArtifactSelection, ChatMessage, DomainEvent, InputFile, RunSnapshot,
    RunStatus, SourceEvent,
)
from app.chat.v2.repository import ActiveRunDeletionError, ActiveRunLimitError
from app.chat.v2.token_usage import summarize_usage

router = APIRouter(prefix="/v2", tags=["deep-agent-v2"])


class CreateRunRequest(BaseModel):
    objective: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    thread_id: str | None = None
    user_option: dict[str, Any] | None = None
    input_files: list[InputFile] = Field(default_factory=list)


class MessageRequest(BaseModel):
    content: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    thread_id: str | None = None
    user_option: dict[str, Any] | None = None
    input_files: list[InputFile] = Field(default_factory=list)


class ResumeRequest(BaseModel):
    """Resume a VideoAgent interrupt. Accepts `content` (preferred) or legacy `response`."""

    content: str = ""
    response: str = ""
    idempotency_key: str = Field(min_length=1)
    thread_id: str | None = None
    user_option: dict[str, Any] | None = None
    input_files: list[InputFile] = Field(default_factory=list)

    def resolved_content(self) -> str:
        return (self.content or self.response or "继续").strip() or "继续"

class InternalEventResponse(BaseModel):
    accepted: bool
    deduplicated: bool


class UploadFilesResponse(BaseModel):
    files: list[InputFile]


class DeleteRunResponse(BaseModel):
    deleted: bool = True
    records: dict[str, int]


def require_harness():
    harness = get_harness()
    if harness is None:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR, "Deep Agent V2 is disabled",
        )
    return harness


def not_found() -> BusinessException:
    return BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "V2 run not found")


def active_run_limit(error: ActiveRunLimitError) -> BusinessException:
    return BusinessException(BusinessExceptionCode.BUSINESS_ERROR, str(error))


async def screen_content(content: str) -> str:
    sanitized, blocked, reason = await input_rail(content)
    if blocked:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            reason or "The request was blocked by the prompt safety policy",
        )
    normalized = sanitized.strip()
    if not normalized:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER, "Message content is empty",
        )
    return normalized


@router.post("/runs", response_model=ResponseModel[AgentRun])
async def create_run(
    request: CreateRunRequest,
    app_language: Annotated[str | None, Header(alias="X-App-Language")] = None,
    user_id: str = Security(auth_service.get_current_user),
):
    objective = await screen_content(request.objective)
    try:
        run = await require_harness().create_run(
            user_id=user_id, objective=objective,
            idempotency_key=request.idempotency_key, thread_id=request.thread_id,
            user_option=request.user_option, input_files=request.input_files,
            output_language=app_language,
        )
    except ActiveRunLimitError as error:
        raise active_run_limit(error)
    except LanguageSkillConflictError as error:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER, str(error)
        )
    return ResponseModel.success(data=run)


@router.post("/uploads", response_model=ResponseModel[UploadFilesResponse])
async def upload_files(
    files: list[UploadFile] = File(...),
    _user_id: str = Security(auth_service.get_current_user),
):
    """Upload conversation files through the existing Cuti media pipeline."""
    images, audio_files, video_files = await process_uploaded_files(files)
    uploaded = [
        InputFile(
            type=kind,
            url=item.url,
            filename=getattr(item, "filename", None),
            metadata=item.model_dump(mode="json"),
        )
        for kind, items in (
            ("image", images),
            ("audio", audio_files),
            ("video", video_files),
        )
        for item in items
    ]
    return ResponseModel.success(data=UploadFilesResponse(files=uploaded))


@router.get("/runs", response_model=ResponseModel[list[AgentRun]])
async def list_runs(
    user_id: str = Security(auth_service.get_current_user),
):
    return ResponseModel.success(data=await require_harness().repo.list_runs(user_id))


@router.get("/runs/{run_id}", response_model=ResponseModel[RunSnapshot])
async def get_run(
    run_id: str,
    user_id: str = Security(auth_service.get_current_user),
):
    snapshot = await require_harness().repo.snapshot(run_id)
    if not snapshot or snapshot.run.user_id != user_id:
        raise not_found()
    return ResponseModel.success(data=snapshot)


@router.get("/runs/{run_id}/token-usage", response_model=ResponseModel[dict[str, Any]])
async def get_token_usage(
    run_id: str,
    user_id: str = Security(auth_service.get_current_user),
):
    harness = require_harness()
    run = await harness.repo.get_run(run_id)
    if not run or run.user_id != user_id:
        raise not_found()
    return ResponseModel.success(data=summarize_usage(await harness.repo.get_events(run_id)))


@router.delete("/runs/{run_id}", response_model=ResponseModel[DeleteRunResponse])
async def delete_run(
    run_id: str,
    user_id: str = Security(auth_service.get_current_user),
):
    try:
        records = await require_harness().delete_run(run_id, user_id)
    except LookupError:
        raise not_found()
    except ActiveRunDeletionError as error:
        raise BusinessException(BusinessExceptionCode.BUSINESS_ERROR, str(error))
    return ResponseModel.success(data=DeleteRunResponse(records=records))


@router.post("/runs/{run_id}/messages", response_model=ResponseModel[AgentRun])
async def add_message(
    run_id: str,
    request: MessageRequest,
    app_language: Annotated[str | None, Header(alias="X-App-Language")] = None,
    user_id: str = Security(auth_service.get_current_user),
):
    try:
        content = await screen_content(request.content)
        run = await require_harness().add_message(
            run_id, user_id, content, request.idempotency_key,
            user_option=request.user_option, input_files=request.input_files,
            thread_id=request.thread_id,
            output_language=app_language,
        )
    except LookupError:
        raise not_found()
    except ActiveRunLimitError as error:
        raise active_run_limit(error)
    except LanguageSkillConflictError as error:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER, str(error)
        )
    return ResponseModel.success(data=run)


@router.get("/runs/{run_id}/messages", response_model=ResponseModel[list[ChatMessage]])
async def list_messages(
    run_id: str,
    user_id: str = Security(auth_service.get_current_user),
):
    harness = require_harness()
    run = await harness.repo.get_run(run_id)
    if not run or run.user_id != user_id:
        raise not_found()
    return ResponseModel.success(data=await harness.repo.list_chat_messages(run_id))


@router.get(
    "/runs/{run_id}/event-log",
    response_model=ResponseModel[list[DomainEvent]],
)
async def list_event_log(
    run_id: str,
    limit: int = Query(500, ge=1, le=1000),
    user_id: str = Security(auth_service.get_current_user),
):
    harness = require_harness()
    run = await harness.repo.get_run(run_id)
    if not run or run.user_id != user_id:
        raise not_found()
    events = await harness.repo.get_events(run_id)
    trace_events = [
        event for event in events
        if event.type != "agent.message.delta"
    ]
    return ResponseModel.success(data=trace_events[-limit:])


@router.get(
    "/runs/{run_id}/events",
    response_class=StreamingResponse,
    response_model=None,
)
async def stream_events(
    run_id: str,
    after: int = Query(0, ge=0),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    user_id: str = Security(auth_service.get_current_user),
):
    harness = require_harness()
    run = await harness.repo.get_run(run_id)
    if not run or run.user_id != user_id:
        raise not_found()
    cursor = max(after, int(last_event_id) if last_event_id and last_event_id.isdigit() else 0)

    async def generate():
        nonlocal cursor
        while True:
            events = await harness.repo.get_events(run_id, cursor)
            for event in events:
                cursor = event.sequence
                payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"
            current = await harness.repo.get_run(run_id)
            if current and current.status in {
                RunStatus.WAITING_INPUT, RunStatus.COMPLETED,
                RunStatus.FAILED, RunStatus.CANCELLED,
            } and not await harness.repo.get_events(run_id, cursor):
                break
            yield ": heartbeat\n\n"
            await harness.repo.wait_for_events(run_id, 5)

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/cancel", response_model=ResponseModel[AgentRun])
async def cancel_run(
    run_id: str,
    user_id: str = Security(auth_service.get_current_user),
):
    try:
        run = await require_harness().cancel(run_id, user_id)
    except LookupError:
        raise not_found()
    return ResponseModel.success(data=run)


@router.post("/runs/{run_id}/resume", response_model=ResponseModel[AgentRun])
async def resume_run(
    run_id: str,
    request: ResumeRequest,
    app_language: Annotated[str | None, Header(alias="X-App-Language")] = None,
    user_id: str = Security(auth_service.get_current_user),
):
    """Resume a downstream VideoAgent interrupt, or continue chat planning."""
    try:
        text = request.resolved_content()
        content = await screen_content(text) if text.strip() else "继续"
        run = await require_harness().resume_interrupt(
            run_id,
            user_id,
            content,
            idempotency_key=request.idempotency_key,
            user_option=request.user_option,
            input_files=request.input_files,
            thread_id=request.thread_id,
            output_language=app_language,
        )
    except LookupError:
        raise not_found()
    except ActiveRunLimitError as error:
        raise active_run_limit(error)
    return ResponseModel.success(data=run)


@router.post(
    "/runs/{run_id}/artifacts/{version_id}/select",
    response_model=ResponseModel[ArtifactSelection],
)
async def select_artifact(
    run_id: str,
    version_id: str,
    user_id: str = Security(auth_service.get_current_user),
):
    try:
        selection = await require_harness().select_artifact(run_id, user_id, version_id)
    except LookupError:
        raise not_found()
    return ResponseModel.success(data=selection)


class ExtractFrameRequest(BaseModel):
    timestamp: float | None = Field(default=None, ge=0, description="Seconds from video start")
    position: str = Field(default="timestamp", pattern="^(timestamp|last)$")
    version_id: str | None = None
    video_url: str | None = None
    format: str = Field(default="jpeg", description="jpeg | png")
    select: bool = True


@router.post(
    "/runs/{run_id}/extract-frame",
    response_model=ResponseModel[dict[str, Any]],
)
async def extract_frame(
    run_id: str,
    request: ExtractFrameRequest,
    user_id: str = Security(auth_service.get_current_user),
):
    """Extract a still frame from a video artifact or uploaded video URL."""
    try:
        artifact = await require_harness().extract_frame(
            run_id,
            user_id,
            timestamp=request.timestamp,
            position=request.position,
            version_id=request.version_id,
            video_url=request.video_url,
            image_format=request.format,
            select=request.select,
        )
    except LookupError:
        raise not_found()
    except ValueError as exc:
        raise BusinessException(BusinessExceptionCode.BUSINESS_ERROR, str(exc)) from exc
    return ResponseModel.success(data=artifact.model_dump(mode="json"))


@router.post(
    "/runs/{run_id}/artifacts/{version_id}/extract-frame",
    response_model=ResponseModel[dict[str, Any]],
)
async def extract_frame_from_artifact(
    run_id: str,
    version_id: str,
    request: ExtractFrameRequest,
    user_id: str = Security(auth_service.get_current_user),
):
    try:
        artifact = await require_harness().extract_frame(
            run_id,
            user_id,
            timestamp=request.timestamp,
            position=request.position,
            version_id=version_id,
            video_url=request.video_url,
            image_format=request.format,
            select=request.select,
        )
    except LookupError:
        raise not_found()
    except ValueError as exc:
        raise BusinessException(BusinessExceptionCode.BUSINESS_ERROR, str(exc)) from exc
    return ResponseModel.success(data=artifact.model_dump(mode="json"))


class SkillReloadResponse(BaseModel):
    skill_count: int


class SkillInstallResponse(BaseModel):
    name: str
    skill_count: int


class SkillInfo(BaseModel):
    name: str
    description: str
    trust_level: str
    enabled: bool
    capability_id: str | None = None
    bundle_digest: str | None = None
    installed_at: str | None = None


class SkillEnabledRequest(BaseModel):
    enabled: bool


def list_skill_info() -> list[SkillInfo]:
    from app.chat.v2.container import get_capability_registry, get_skill_catalog

    catalog = get_skill_catalog()
    registry = get_capability_registry()
    if catalog is None or registry is None:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR, "Deep Agent V2 is disabled",
        )
    capabilities = {
        item.skill_name: item
        for item in registry.list(include_disabled=True)
        if item.skill_name
    }
    values = []
    for metadata in catalog.list_metadata():
        capability = capabilities.get(metadata.name)
        loaded_skill = catalog.load(metadata.name)
        install_metadata = {}
        install_file = metadata.path.parent / ".cuti-install.json"
        if install_file.is_file():
            try:
                install_metadata = json.loads(install_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                install_metadata = {}
        values.append(SkillInfo(
            name=metadata.name,
            description=metadata.description,
            trust_level=metadata.trust_level,
            enabled=(
                metadata.enabled
                if loaded_skill.contract is None
                else bool(capability and capability.enabled)
            ),
            capability_id=capability.id if capability else None,
            bundle_digest=capability.bundle_digest if capability else None,
            installed_at=install_metadata.get("installed_at"),
        ))
    return values


@router.get("/skills", response_model=ResponseModel[list[SkillInfo]])
async def list_available_skills(
    _user_id: str = Security(auth_service.get_current_user),
):
    return ResponseModel.success(data=list_skill_info())


def require_internal_token(authorization: str | None) -> None:
    token = get_settings().DEEP_AGENT_V2_INTERNAL_EVENT_TOKEN or get_settings().CUTI_SERVICE_TOKEN
    if not token:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR, "Internal event token is not configured",
        )
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not secrets.compare_digest(supplied, token):
        raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "Invalid service token")


class HostCapabilityRequest(BaseModel):
    capability: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    allowed_capabilities: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    skill_name: str | None = None


@router.post("/internal/host/dispatch")
async def internal_host_dispatch(
    body: HostCapabilityRequest,
    authorization: Annotated[str | None, Header()] = None,
):
    """Generic host-capability gateway for sandbox Skills (http/media/artifact)."""
    require_internal_token(authorization)
    from app.chat.v2.host_gateway import HostGateway, HostGatewayError

    gateway = HostGateway()
    try:
        result = await gateway.dispatch(
            body.capability,
            body.payload,
            allowed_capabilities=body.allowed_capabilities or [
                "http.fetch", "http.request", "media.concat", "media.extract_frame", "provider.generate",
                "artifact.read", "artifact.write", "log", "progress",
            ],
            allowed_domains=body.allowed_domains,
        )
    except HostGatewayError as exc:
        raise BusinessException(BusinessExceptionCode.BUSINESS_ERROR, str(exc)) from exc
    return ResponseModel.success(data=result)


def _require_ark_bridge_auth(authorization: str | None) -> None:
    """Accept service token or the injected cuti-ark-bridge placeholder key."""
    settings = get_settings()
    token = settings.DEEP_AGENT_V2_INTERNAL_EVENT_TOKEN or settings.CUTI_SERVICE_TOKEN
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if token and secrets.compare_digest(supplied, token):
        return
    if supplied.startswith("cuti-ark-bridge"):
        return
    # Also allow empty only when no token configured (local single-user).
    if not token and not supplied:
        return
    raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "Invalid Ark bridge token")


@router.api_route(
    "/internal/host/ark/api/v3/contents/generations/tasks",
    methods=["POST", "GET"],
)
async def ark_protocol_tasks(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
):
    """Ark-compatible create/list — WaveSpeed under the hood for seedance2 scripts."""
    _require_ark_bridge_auth(authorization)
    from app.chat.v2.ark_protocol_bridge import create_ark_task_via_wavespeed

    if request.method == "GET":
        return {"items": [], "total": 0}
    body = await request.json()
    if not isinstance(body, dict):
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "JSON object required")
    try:
        return await create_ark_task_via_wavespeed(
            body,
            fallbacks_json=get_settings().DEEP_AGENT_V2_PROVIDER_FALLBACKS_JSON,
        )
    except ValueError as exc:
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, str(exc)) from exc
    except Exception as exc:
        raise BusinessException(BusinessExceptionCode.BUSINESS_ERROR, str(exc)) from exc


@router.api_route(
    "/internal/host/ark/api/v3/contents/generations/tasks/{task_id}",
    methods=["GET", "DELETE"],
)
async def ark_protocol_task_detail(
    task_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
):
    _require_ark_bridge_auth(authorization)
    from app.chat.v2.ark_protocol_bridge import delete_ark_task, get_ark_task

    if request.method == "DELETE":
        return await delete_ark_task(task_id)
    return await get_ark_task(task_id)


@router.post(
    "/internal/video-agent/events",
    response_model=ResponseModel[InternalEventResponse],
)
async def internal_video_agent_event(
    event: SourceEvent,
    authorization: Annotated[str | None, Header()] = None,
):
    require_internal_token(authorization)
    try:
        accepted = await require_harness().ingest_source_event(event)
    except LookupError:
        raise not_found()
    return ResponseModel.success(data=InternalEventResponse(
        accepted=accepted, deduplicated=not accepted,
    ))


@router.post("/internal/skills/reload", response_model=ResponseModel[SkillReloadResponse])
async def internal_reload_skills(
    authorization: Annotated[str | None, Header()] = None,
):
    require_internal_token(authorization)
    from app.chat.v2.container import reload_v2_skills
    try:
        count = reload_v2_skills()
    except RuntimeError as exc:
        raise BusinessException(BusinessExceptionCode.BUSINESS_ERROR, str(exc))
    return ResponseModel.success(data=SkillReloadResponse(skill_count=count))


@router.post("/internal/skills/install", response_model=ResponseModel[SkillInstallResponse])
async def internal_install_skill(
    bundle: UploadFile = File(...),
    overwrite: bool = Form(False),
    authorization: Annotated[str | None, Header()] = None,
):
    require_internal_token(authorization)
    from app.chat.v2.container import (
        get_capability_registry, get_skill_catalog, reload_v2_skills,
    )
    from app.chat.v2.skill_install import (
        MAX_BUNDLE_BYTES,
        SkillInstallError,
        extract_skill_archive,
        install_skill_directory,
    )

    catalog = get_skill_catalog()
    registry = get_capability_registry()
    if catalog is None or registry is None:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR, "Deep Agent V2 is disabled",
        )
    try:
        data = await bundle.read(MAX_BUNDLE_BYTES + 1)
        if len(data) > MAX_BUNDLE_BYTES:
            raise SkillInstallError(f"skill archive exceeds {MAX_BUNDLE_BYTES} bytes")
        with tempfile.TemporaryDirectory(prefix="cuti-skill-upload-") as staging:
            source = extract_skill_archive(data, Path(staging))
            name = install_skill_directory(
                source,
                get_settings().deep_agent_external_skill_root(),
                catalog=catalog,
                registry=registry,
                overwrite=overwrite,
            )
        count = reload_v2_skills()
    except (SkillInstallError, ValueError, zipfile.BadZipFile) as exc:
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, str(exc))
    return ResponseModel.success(data=SkillInstallResponse(name=name, skill_count=count))


@router.post(
    "/internal/skills/install-archive",
    response_model=ResponseModel[SkillInstallResponse],
)
async def internal_install_skill_archive(
    request: Request,
    overwrite: bool = Query(False),
    authorization: Annotated[str | None, Header()] = None,
):
    require_internal_token(authorization)
    from app.chat.v2.container import (
        get_capability_registry, get_skill_catalog, reload_v2_skills,
    )
    from app.chat.v2.skill_install import (
        MAX_BUNDLE_BYTES,
        SkillInstallError,
        extract_skill_archive,
        install_skill_directory,
    )

    catalog = get_skill_catalog()
    registry = get_capability_registry()
    if catalog is None or registry is None:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR, "Deep Agent V2 is disabled",
        )
    data = await request.body()
    if len(data) > MAX_BUNDLE_BYTES:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER,
            f"skill archive exceeds {MAX_BUNDLE_BYTES} bytes",
        )
    try:
        with tempfile.TemporaryDirectory(prefix="cuti-skill-upload-") as staging:
            source = extract_skill_archive(data, Path(staging))
            name = install_skill_directory(
                source,
                get_settings().deep_agent_external_skill_root(),
                catalog=catalog,
                registry=registry,
                overwrite=overwrite,
            )
        count = reload_v2_skills()
    except (SkillInstallError, ValueError, zipfile.BadZipFile) as exc:
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, str(exc))
    return ResponseModel.success(data=SkillInstallResponse(name=name, skill_count=count))


@router.get("/internal/skills", response_model=ResponseModel[list[SkillInfo]])
async def internal_list_skills(
    authorization: Annotated[str | None, Header()] = None,
):
    require_internal_token(authorization)
    return ResponseModel.success(data=list_skill_info())


@router.patch(
    "/internal/skills/{name}/enabled",
    response_model=ResponseModel[SkillReloadResponse],
)
async def internal_set_skill_enabled(
    name: str,
    request: SkillEnabledRequest,
    authorization: Annotated[str | None, Header()] = None,
):
    require_internal_token(authorization)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "Invalid skill name")
    root = get_settings().deep_agent_external_skill_root().resolve()
    target = (root / name).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "Invalid skill path")
    if not (target / "SKILL.md").is_file():
        raise BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "Skill not found")
    marker = target / ".cuti-disabled"
    if request.enabled:
        marker.unlink(missing_ok=True)
    else:
        marker.write_text("disabled\n", encoding="utf-8")
    from app.chat.v2.container import reload_v2_skills
    return ResponseModel.success(data=SkillReloadResponse(
        skill_count=reload_v2_skills(),
    ))


@router.delete("/internal/skills/{name}", response_model=ResponseModel[SkillReloadResponse])
async def internal_delete_skill(
    name: str,
    authorization: Annotated[str | None, Header()] = None,
):
    require_internal_token(authorization)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "Invalid skill name")
    root = get_settings().deep_agent_external_skill_root().resolve()
    target = (root / name).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "Invalid skill path")
    if not target.is_dir():
        raise BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "Skill not found")
    shutil.rmtree(target)
    from app.chat.v2.container import reload_v2_skills
    return ResponseModel.success(data=SkillReloadResponse(
        skill_count=reload_v2_skills(),
    ))
