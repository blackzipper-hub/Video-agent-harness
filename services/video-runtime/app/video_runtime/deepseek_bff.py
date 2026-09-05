from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import tempfile
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .api import get_runtime
from .deepseek_client import DeepSeekHarnessClient, DeepSeekHarnessError
from .runtime import VideoBuildRuntime
from .models import MediaArtifactVersion
from .upload_security import sign_uploaded_file as _sign_uploaded_file
from .workflow_plans import WORKFLOW_ID_COMPILERS, UNAVAILABLE_WORKFLOW_MODES
from app.chat.utils.file_utils import process_uploaded_files


router = APIRouter(prefix="/v2", tags=["deepseek-compatibility-bff"])
studio_router = APIRouter(prefix="/studio", tags=["deepseek-studio-compatibility-bff"])
client: DeepSeekHarnessClient | None = None


def set_deepseek_client(value: DeepSeekHarnessClient | None) -> None:
    global client
    client = value


def get_deepseek_client() -> DeepSeekHarnessClient:
    if client is None:
        raise HTTPException(status_code=503, detail="DeepSeek compatibility backend is not configured")
    return client


async def identity(
    user_id: str | None = Header(default=None, alias="X-Video-User-Id"),
) -> str:
    return user_id or os.getenv("VIDEO_RUNTIME_LOCAL_USER_ID", "local-user")


class CreateRunBody(BaseModel):
    objective: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    thread_id: str | None = None
    user_option: dict[str, Any] | None = None
    input_files: list[dict[str, Any]] = Field(default_factory=list)
    workflow_id: str | None = None
    activated_skill_ids: list[str] = Field(default_factory=list)


class MessageBody(BaseModel):
    content: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    user_option: dict[str, Any] | None = None
    input_files: list[dict[str, Any]] = Field(default_factory=list)
    workflow_id: str | None = None
    activated_skill_ids: list[str] = Field(default_factory=list)


class StudioProjectBody(BaseModel):
    objective: str = Field(min_length=1)
    thread_id: str | None = None
    user_option: dict[str, Any] | None = None
    input_files: list[dict[str, Any]] = Field(default_factory=list)
    workflow_id: str | None = None
    activated_skill_ids: list[str] = Field(default_factory=list)


class StudioSkillEnableBody(BaseModel):
    enabled: bool


def success(data: Any) -> dict[str, Any]:
    return {"code": 0, "message": "success", "data": data}


def _prompt_input_files(input_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose project Artifact identities to the model without storage URLs or receipts."""
    return [{
        "artifact_id": item.get("artifact_id"),
        "type": item.get("type"),
        "filename": item.get("filename"),
    } for item in input_files]


async def _import_input_files(
    runtime: VideoBuildRuntime,
    project_id: str,
    user_id: str,
    input_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Commit verified uploads as project-owned immutable source artifacts."""
    current = {item.artifact_id: item for item in await runtime.repo.current_artifacts(project_id)}
    imported: list[dict[str, Any]] = []
    for item in input_files:
        kind = str(item.get("type") or "").strip().lower()
        url = str(item.get("url") or "").strip()
        metadata = dict(item.get("metadata") or {})
        receipt = str(metadata.get("upload_receipt") or "")
        if kind not in {"image", "audio", "video"} or not url:
            raise HTTPException(status_code=422, detail="Uploaded media has an invalid type or URL")
        if not hmac.compare_digest(receipt, _sign_uploaded_file(user_id, kind, url)):
            raise HTTPException(status_code=422, detail="Uploaded media receipt is missing or invalid")
        logical_id = str(metadata.get("artifact_id") or f"source:{hashlib.sha256(url.encode()).hexdigest()[:24]}")
        artifact = current.get(logical_id)
        if artifact is None:
            artifact = await runtime.repo.add_artifact(MediaArtifactVersion(
                artifact_id=logical_id,
                project_id=project_id,
                type=f"source_{kind}",
                uri=url,
                title=str(item.get("filename") or f"Uploaded {kind}"),
                summary="Project-owned uploaded source media",
                content_digest=hashlib.sha256(url.encode()).hexdigest(),
                provider_id="cuti-upload",
                provenance={"uploaded_by": user_id},
                metadata={**metadata, "media_type": kind, "source": "upload"},
            ))
            current[logical_id] = artifact
        imported.append({
            **item,
            "artifact_id": logical_id,
            "metadata": {**metadata, "artifact_id": logical_id},
        })
    return imported


async def _project_and_binding(
    runtime: VideoBuildRuntime,
    project_id: str,
    user_id: str,
):
    try:
        project = await runtime.repo.get_project(project_id)
        if project.user_id != user_id:
            raise LookupError("project not found")
        binding = await runtime.repo.latest_session_binding(project_id, user_id)
        return project, binding
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="V2 run not found") from exc


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(item.get("text", ""))
            for item in value
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def _json_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _tool_result(data: dict[str, Any]) -> tuple[str, str, bool]:
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    source = message.get("source") if isinstance(message.get("source"), dict) else {}
    call_id = str(source.get("callId") or "")
    outputs: list[str] = []
    is_error = False
    for block in message.get("content", []):
        if not isinstance(block, dict) or block.get("type") != "tool-result":
            continue
        call_id = str(block.get("toolCallId") or call_id)
        is_error = is_error or bool(block.get("isError"))
        content = block.get("content")
        if isinstance(content, str):
            outputs.append(content)
        elif isinstance(content, list):
            outputs.extend(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
    return call_id, "\n".join(item for item in outputs if item), is_error


def _usage_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    chunk = data.get("chunk") if isinstance(data.get("chunk"), dict) else {}
    usage = chunk.get("usage") if isinstance(chunk.get("usage"), dict) else None
    if chunk.get("type") != "usage" or usage is None:
        return None
    input_tokens = int(usage.get("inputTokens") or 0)
    output_tokens = int(usage.get("outputTokens") or 0)
    cache_read_tokens = int(usage.get("cacheReadTokens") or 0)
    cache_write_tokens = int(usage.get("cacheWriteTokens") or 0)
    reasoning_tokens = int(usage.get("reasoningTokens") or 0)
    return {
        "scope": "deepseek-harness",
        "turn": data.get("turn"),
        "step": data.get("step"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": (
            input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
        ),
        "status": "succeeded",
    }


_CREATE_PROMPT_MARKER = "CUTI_VIDEO_CREATE_V1"
_CHECKPOINT_PROMPT_MARKER = "CUTI_VIDEO_CHECKPOINT_V1"
_RUN_CONTEXT_OPERATION = "compat-run-context"
_RUN_CONTEXT_KEY = "initial"
_SKILL_SELECTION_SEPARATOR = "\n\nServer-resolved video Skill selection:\n"


_EXPLICIT_SKILL = re.compile(r"(?<![A-Za-z0-9_-])[$/]([A-Za-z0-9][A-Za-z0-9_-]{0,63})")


def _requested_skill_selection(
    runtime: VideoBuildRuntime,
    requested_workflow: str | None,
    requested_activated: list[str],
    text: str,
) -> tuple[str | None, list[str]]:
    """Resolve explicit UI and `$skill` choices from the unified Runtime catalog."""
    explicit = [
        match.group(1) for match in _EXPLICIT_SKILL.finditer(text)
        if runtime.skills.catalog.has(match.group(1))
    ]
    explicit_workflows = []
    helpers = list(requested_activated)
    for skill_id in explicit:
        metadata = runtime.skills.catalog.load(skill_id).metadata
        if (metadata.metadata or {}).get("kind") == "workflow":
            explicit_workflows.append(skill_id)
        elif skill_id not in helpers:
            helpers.append(skill_id)
    if requested_workflow:
        return requested_workflow, helpers
    unique_workflows = list(dict.fromkeys(explicit_workflows))
    if len(unique_workflows) > 1:
        raise HTTPException(status_code=422, detail="Only one Workflow Skill can be selected per request")
    return (unique_workflows[0] if unique_workflows else None), helpers


def _validate_workflow_selection(
    runtime: VideoBuildRuntime,
    workflow: str | None,
) -> None:
    if not workflow:
        return
    if workflow == "cuti.seedance-story":
        raise HTTPException(
            status_code=409,
            detail=(
                "Workflow is unavailable: cuti.seedance-story is a hidden legacy "
                "compatibility workflow; select an installed Cuti Workflow Skill"
            ),
        )
    if not runtime.skills.catalog.has(workflow):
        # Dedicated Video Plugins may expose a Workflow alias without adding a
        # synthetic SKILL.md (for example cuti.music-video).  Accept it only
        # when the loaded plugin publishes an explicit, selectable compiler
        # contract; a bare contributions.workflows entry still fails closed.
        views = []
        for loaded in runtime.plugins.loaded:
            describe = getattr(loaded.implementation, "describe_workflows", None)
            if callable(describe):
                views.extend(describe())
        view = next((item for item in views if item.get("id") == workflow), None)
        if view is None:
            raise HTTPException(
                status_code=422,
                detail=f"Workflow Skill or plugin is not installed: {workflow}",
            )
        if not view.get("available") or not view.get("userSelectable") or not view.get("compiler"):
            reason = view.get("unavailableReason") or "no dedicated selectable compiler"
            raise HTTPException(
                status_code=409,
                detail=f"Workflow is unavailable: {workflow}: {reason}",
            )
        return
    metadata = runtime.skills.catalog.load(workflow).metadata
    if (metadata.metadata or {}).get("kind") != "workflow":
        raise HTTPException(status_code=422, detail=f"Skill is not a workflow: {workflow}")
    spec = runtime.skills.workflows.get(workflow)
    mode = spec.mode if spec is not None else ""
    contract = WORKFLOW_ID_COMPILERS.get(workflow)
    if contract is None or contract[0] != mode:
        reason = UNAVAILABLE_WORKFLOW_MODES.get(mode) or (
            f"No dedicated compiler for workflow {workflow} with mode {mode}"
        )
        raise HTTPException(status_code=409, detail=f"Workflow is unavailable: {workflow}: {reason}")


def _visible_user_text(value: str) -> str:
    """Keep BFF orchestration instructions out of the legacy chat transcript."""
    if value.startswith(f"{_CHECKPOINT_PROMPT_MARKER}\n"):
        return ""
    controls_separator = "\n\nCurrent creation controls and inputs:\n"
    if controls_separator in value:
        value = value.split(controls_separator, 1)[0]
    if _SKILL_SELECTION_SEPARATOR in value:
        value = value.split(_SKILL_SELECTION_SEPARATOR, 1)[0]
    if not value.startswith(f"{_CREATE_PROMPT_MARKER}\n"):
        return value
    first_line = value.splitlines()[1] if len(value.splitlines()) > 1 else ""
    prefix = "VISIBLE_USER_REQUEST_JSON: "
    if not first_line.startswith(prefix):
        return value
    try:
        parsed = json.loads(first_line[len(prefix):])
    except json.JSONDecodeError:
        return value
    return parsed if isinstance(parsed, str) else value


def _initial_video_build_prompt(
    *,
    objective: str,
    project_id: str,
    base_project_version_id: str,
    idempotency_key: str,
    user_option: dict[str, Any] | None,
    input_files: list[dict[str, Any]],
    workflow_id: str | None = None,
    activated_skill_ids: list[str] | None = None,
) -> str:
    """Give DeepSeek a deterministic product-flow contract for `/create`."""
    options = user_option or {}
    activated = list(dict.fromkeys(activated_skill_ids or []))
    safe_inputs = _prompt_input_files(input_files)
    staged = os.getenv("VIDEO_STAGED_PLANNING_ENABLED", "false").lower() in {
        "1", "true", "yes", "on",
    }
    lines = [
        _CREATE_PROMPT_MARKER,
        f"VISIBLE_USER_REQUEST_JSON: {json.dumps(objective, ensure_ascii=False)}",
        "This request comes from the fully automatic video creation entry.",
        (
            "Planning uses Cuti continuous PlanPatch semantics: start from ProjectIntent only. "
            "The Runtime will persist it, then repeatedly wake this same DeepSeek Session after "
            "each completed task frontier so you can inspect real Artifacts and append the next "
            "tasks. Do not try to precompile the complete production DAG in this turn."
            if os.getenv("VIDEO_CONTINUOUS_PLAN_PATCH_ENABLED", "false").lower()
            in {"1", "true", "yes", "on"} else
            "Planning uses the configured Workflow planning contract."
        ),
        (
            "The Video Runtime BFF has already completed the video_project_create stage "
            f"and bound project {project_id} to this Session. Do not create another project."
        ),
        f"The required base_project_version_id is {base_project_version_id}.",
        f"Creation controls: {json.dumps(options, ensure_ascii=False, sort_keys=True)}",
        f"Uploaded project Source Artifacts: {json.dumps(safe_inputs, ensure_ascii=False, sort_keys=True)}",
        (
            "Use only the uploaded artifact_id values as VideoSpec.source_asset_ids and shot "
            "reference_asset_ids. Do not copy media URLs into VideoSpec and never invent an asset id."
        ),
        (
            "Create a ProjectIntent containing only known goals and constraints. Do not invent "
            "shots, captions, timing, or other details that depend on media not generated yet."
            if staged else
            "Turn the visible user request and creation controls into one complete, valid VideoSpec."
        ),
        (
            f"Set {'ProjectIntent' if staged else 'VideoSpec'}.activated_skill_ids exactly to "
            f"{json.dumps(activated, ensure_ascii=False)}. "
            "Always use automation.mode automatic."
        ),
        "Respect duration, aspect ratio, resolution, selected image/video providers, and attachments when present.",
        (
            "Normalize UI provider names for VideoSpec: seedance_2_* means providers.video "
            "seedance-2.0; gpt_image_2 means providers.image gpt-image-2. If the UI value is "
            "auto, follow the loaded Workflow's default. An exact model named by the visible "
            "user request has higher priority: in particular, never normalize Seedance 2.5 "
            "to Seedance 2.0. Use providers.music suno unless the user explicitly requests "
            "another installed provider."
        ),
        "Optional string fields must be omitted or set to an empty string; never send JSON null.",
        (
            "After video_workflow_load, follow that Skill's instructions, including any "
            "video_skill_load and video_skill_read_resource calls it names. Also call "
            "video_skill_load once for every returned skillDependencies entry. Read markdown "
            "links to bundled files and required paths under references/. Helper Skills stay in context; "
            "do not copy them into activated_skill_ids unless the user or a "
            "project lock already activated them."
        ),
    ]
    if workflow_id:
        lines.append(
            f"The workflow is explicitly selected or project-locked: {json.dumps(workflow_id)}. "
            "Call video_workflow_load for it, then set workflow_id exactly to that id."
        )
    else:
        lines.append(
            "No workflow was explicitly selected. Call video_workflow_list, choose the best "
            "available user-selectable workflow from its descriptions, call video_workflow_load "
            "for that workflow, and set workflow_id to its exact id. Never choose an "
            "unavailable workflow and never silently substitute another workflow after a failure."
        )
    if activated:
        lines.append(
            "Call video_skill_load for every activated_skill_id before constructing VideoSpec."
        )
    lines.extend([
        "Then perform these tool calls in order without asking for confirmation:",
        (
            "1. video_project_plan with the existing project_id and base_project_version_id, "
            f"using idempotency_key plan:{idempotency_key} and "
            + ("project_intent." if staged else "video_spec.")
        ),
        (
            "2. video_project_build with the returned plan_id and the same base version, "
            f"using idempotency_key build:{idempotency_key}."
        ),
        "Do not stop after explaining or displaying the plan. Start the build in this turn.",
        f"Visible user request: {objective}",
    ])
    return "\n".join(lines)


async def _effective_skill_selection(
    runtime: VideoBuildRuntime,
    project_id: str,
    requested_workflow: str | None,
    requested_activated: list[str],
) -> tuple[str | None, list[str]]:
    locked_workflow: str | None = None
    activated: list[str] = []
    for lock in await runtime.list_project_skill_locks(project_id):
        if not lock.enabled:
            continue
        if not runtime.skills.catalog.has(lock.skill_id):
            raise HTTPException(status_code=409, detail=f"Locked Skill is no longer installed: {lock.skill_id}")
        metadata = runtime.skills.catalog.load(lock.skill_id).metadata
        if (metadata.metadata or {}).get("kind") == "workflow":
            locked_workflow = lock.skill_id
        else:
            activated.append(lock.skill_id)

    workflow = requested_workflow or locked_workflow
    _validate_workflow_selection(runtime, workflow)
    for skill_id in requested_activated:
        if not runtime.skills.catalog.has(skill_id):
            raise HTTPException(status_code=422, detail=f"Skill is not installed: {skill_id}")
        metadata = runtime.skills.catalog.load(skill_id).metadata
        if (metadata.metadata or {}).get("kind") == "workflow":
            raise HTTPException(status_code=422, detail=f"Workflow Skill must use workflow_id: {skill_id}")
        activated.append(skill_id)
    return workflow, list(dict.fromkeys(activated))


def _selection_context(workflow_id: str | None, activated_skill_ids: list[str]) -> str:
    return _SKILL_SELECTION_SEPARATOR + json.dumps({
        "workflow_id": workflow_id,
        "activated_skill_ids": activated_skill_ids,
        "instruction": (
            "Preserve non-null workflow_id and activated_skill_ids when editing the creative "
            "VideoSpec. Post-production on existing Artifacts is workflow-independent: call "
            "video_artifact_list and video_plan_patch_capability_list, load the recommended Skill, "
            "then use video_plan_patch_preview without switching or recompiling the generation "
            "Workflow. If workflow_id is null, inspect the existing project instead of changing it."
        ),
    }, ensure_ascii=False, sort_keys=True)


def _event_entries(history: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in history.get("events", []):
        if isinstance(entry, dict) and isinstance(entry.get("event"), dict):
            result.append(entry["event"])
    return result


def _status(events: list[dict[str, Any]]) -> str:
    if not events:
        return "planning"
    boundary = next(
        (item for item in reversed(events) if item.get("type") in {"turn/start", "turn/end"}),
        None,
    )
    if boundary is None or boundary.get("type") == "turn/start":
        return "running"
    reason = boundary.get("data", {}).get("reason", {}) if isinstance(boundary.get("data"), dict) else {}
    kind = reason.get("kind") if isinstance(reason, dict) else None
    if kind == "error":
        return "failed"
    if kind == "aborted":
        return "cancelled"
    return "completed"


def _messages(project_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event_type == "user/message":
            source = data.get("source")
            if isinstance(source, dict) and source.get("kind") != "user":
                continue
            role, content = "user", _visible_user_text(_text(data.get("content")))
        elif event_type == "assistant/message":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            role, content = "assistant", _text(message.get("content"))
        else:
            continue
        if not content:
            continue
        milliseconds = event.get("time") if isinstance(event.get("time"), (int, float)) else 0
        created = datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()
        messages.append({
            "id": f"dsh-{project_id}-{event.get('seq', len(messages))}",
            "run_id": project_id,
            "role": role,
            "content": content,
            "sequence": event.get("seq", len(messages) + 1),
            "metadata": {"source": "deepseek-harness"},
            "created_at": created,
        })
    return messages


def _run(
    project,
    session_id: str,
    events: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    messages = _messages(project.id, events)
    assistant = [item["content"] for item in messages if item["role"] == "assistant"]
    user = [item["content"] for item in messages if item["role"] == "user"]
    return {
        "id": project.id,
        "thread_id": session_id,
        "project_id": project.id,
        "title": project.title,
        "objective": user[0] if user else project.title,
        "status": _status(events),
        "current_revision": 0,
        "last_response": assistant[-1] if assistant else "",
        "output_language": "en",
        "user_option": (context or {}).get("user_option"),
        "input_files": (context or {}).get("input_files", []),
        "workflow_id": (context or {}).get("workflow_id"),
        "activated_skill_ids": (context or {}).get("activated_skill_ids", []),
        "skill_locks": [],
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


async def _run_with_context(
    runtime: VideoBuildRuntime,
    project,
    session_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    encoded = await runtime.repo.get_operation_result(
        project.id, _RUN_CONTEXT_OPERATION, _RUN_CONTEXT_KEY,
    )
    context: dict[str, Any] = {}
    if encoded:
        try:
            parsed = json.loads(encoded)
            if isinstance(parsed, dict):
                context = parsed
        except json.JSONDecodeError:
            pass
    result = _run(project, session_id, events, context)
    locks = await runtime.list_project_skill_locks(project.id)
    result["skill_locks"] = [item.model_dump(mode="json") for item in locks]
    locked_workflow = next((
        item.skill_id for item in locks
        if item.enabled
        and runtime.skills.catalog.has(item.skill_id)
        and (runtime.skills.catalog.load(item.skill_id).metadata.metadata or {}).get("kind") == "workflow"
    ), None)
    if locked_workflow:
        result["workflow_id"] = locked_workflow
    return result


async def _history(dsh: DeepSeekHarnessClient, session_id: str) -> list[dict[str, Any]]:
    try:
        return _event_entries(await dsh.history(session_id, max_messages=1000))
    except DeepSeekHarnessError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _create_route_session(
    dsh: DeepSeekHarnessClient,
    requested_session_id: str | None,
) -> str:
    """Create the route Session, replacing only an orphaned id collision.

    A local Runtime snapshot can be restored from an older backup while the
    DeepSeek Session store still contains the URL's Session. That URL has no
    authoritative Project binding, so keeping its id would mix two project
    histories. Start a fresh Session and let the returned ``thread_id`` move
    Create Space to the new durable binding.
    """
    preset = os.getenv("VIDEO_AGENT_PRESET", "video")
    if requested_session_id:
        existing_session_ids = {
            str(item.get("sessionId"))
            for item in await dsh.list_sessions()
            if item.get("sessionId")
        }
        if requested_session_id in existing_session_ids:
            return await dsh.create_session(agent_preset=preset)
    return await dsh.create_session(
        session_id=requested_session_id,
        agent_preset=preset,
    )


async def _snapshot(
    runtime: VideoBuildRuntime,
    dsh: DeepSeekHarnessClient,
    project,
    session_id: str,
) -> dict[str, Any]:
    project = await runtime.repo.get_project(project.id)
    events = await _history(dsh, session_id)
    artifacts = await runtime.repo.current_artifacts(project.id)
    version = await runtime.repo.get_project_version(project.current_version_id)
    messages = _messages(project.id, events)
    builds = await runtime.repo.list_builds(project.id)
    runtime_tasks: list[dict[str, Any]] = []
    status_map = {
        "pending": "ready", "completed": "succeeded",
        "running": "running", "waiting_external": "waiting_external",
        "failed": "failed", "cancelled": "cancelled",
    }
    for build in builds[:3]:
        for step in await runtime.repo.list_build_steps(project.id, build.id):
            runtime_tasks.append({
                "id": step.id,
                "run_id": project.id,
                "revision": 0,
                "capability_id": step.capability or step.plan_step_id,
                "objective": step.plan_step_id.replace("-", " "),
                "input_artifact_version_ids": [],
                "depends_on": [],
                "status": status_map.get(step.status, "ready"),
                "remote_operation_id": step.remote_operation_id,
                "error": step.error,
                "progress": 100 if step.status == "completed" else round(build.progress * 100),
                "progress_message": build.message,
                "created_at": build.created_at.isoformat(),
                "updated_at": step.updated_at.isoformat(),
                "resolved_skills": [
                    item.model_dump(mode="json") for item in step.resolved_skills
                ],
            })
    return {
        "run": await _run_with_context(runtime, project, session_id, events),
        "revisions": [],
        "tasks": runtime_tasks,
        "artifacts": [{
            **item.model_dump(mode="json"),
            "artifact_type": item.type,
            "produced_by_task_id": item.provenance.get("task_id", ""),
        } for item in artifacts],
        "selections": [{
            "project_id": project.id,
            "type": item.type,
            "artifact_type": item.type,
            "artifact_version_id": item.id,
            "updated_at": project.updated_at.isoformat(),
        } for item in artifacts if version.selections.get(item.artifact_id) == item.id],
        "messages": messages,
        "last_event_sequence": max((item.get("seq", -1) + 1 for item in events), default=0),
    }


@router.post("/runs")
async def create_run(
    body: CreateRunBody,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    existing = await runtime.repo.get_compatibility_run(user_id, body.idempotency_key)
    if existing is not None:
        project_id, session_id = existing
        project, _binding = await _project_and_binding(runtime, project_id, user_id)
        return success(await _run_with_context(
            runtime, project, session_id, await _history(dsh, session_id),
        ))
    requested_workflow, requested_activated = _requested_skill_selection(
        runtime, body.workflow_id, body.activated_skill_ids, body.objective,
    )
    _validate_workflow_selection(runtime, requested_workflow)
    try:
        session_id = await _create_route_session(dsh, body.thread_id)
        project, initial_version = await runtime.create_project(
            user_id=user_id,
            title=body.objective[:200],
        )
        await runtime.bind_session(project_id=project.id, session_id=session_id, user_id=user_id)
        imported_input_files = await _import_input_files(
            runtime, project.id, user_id, body.input_files,
        )
        if imported_input_files:
            project = await runtime.repo.get_project(project.id)
            initial_version = await runtime.repo.get_project_version(project.current_version_id)
        await runtime.repo.remember_compatibility_run(
            user_id=user_id,
            idempotency_key=body.idempotency_key,
            project_id=project.id,
            session_id=session_id,
        )
        workflow_id, activated_skill_ids = await _effective_skill_selection(
            runtime, project.id, requested_workflow, requested_activated,
        )
        await runtime.repo.remember_operation_result(
            project.id,
            _RUN_CONTEXT_OPERATION,
            _RUN_CONTEXT_KEY,
            json.dumps({
                "user_option": body.user_option,
                "input_files": imported_input_files,
                "workflow_id": workflow_id,
                "activated_skill_ids": activated_skill_ids,
            }, ensure_ascii=False, sort_keys=True),
        )
        await dsh.prompt(
            session_id,
            _initial_video_build_prompt(
                objective=body.objective,
                project_id=project.id,
                base_project_version_id=initial_version.id,
                idempotency_key=body.idempotency_key,
                user_option=body.user_option,
                input_files=imported_input_files,
                workflow_id=workflow_id,
                activated_skill_ids=activated_skill_ids,
            ),
        )
        return success(_run(project, session_id, [], {
            "user_option": body.user_option,
            "input_files": imported_input_files,
            "workflow_id": workflow_id,
            "activated_skill_ids": activated_skill_ids,
        }))
    except DeepSeekHarnessError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/runs")
async def list_runs(
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    rows = []
    for project in await runtime.repo.list_projects(user_id):
        try:
            binding = await runtime.repo.latest_session_binding(project.id, user_id)
        except LookupError:
            continue
        try:
            history = await _history(dsh, binding.session_id)
        except HTTPException as exc:
            if exc.status_code != 502 or "session-not-found" not in str(exc.detail):
                raise
            history = []
        rows.append(await _run_with_context(
            runtime, project, binding.session_id, history,
        ))
    return success(rows)


@router.get("/runs/{project_id}")
async def get_run(
    project_id: str,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    project, binding = await _project_and_binding(runtime, project_id, user_id)
    return success(await _snapshot(runtime, dsh, project, binding.session_id))


@router.post("/runs/{project_id}/messages")
async def add_message(
    project_id: str,
    body: MessageBody,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    project, binding = await _project_and_binding(runtime, project_id, user_id)
    if await runtime.repo.get_operation_result(
        project_id, "compat-message", body.idempotency_key,
    ) is not None:
        return success(await _run_with_context(
            runtime, project, binding.session_id, await _history(dsh, binding.session_id),
        ))
    try:
        imported_input_files = await _import_input_files(
            runtime, project_id, user_id, body.input_files,
        )
        if imported_input_files:
            project = await runtime.repo.get_project(project_id)
        # A draft `/create` project may reuse an existing DeepSeek Session whose
        # earlier conversation was only an inspection or acceptance test.  A raw
        # follow-up can then inherit that stale instruction and stop before plan
        # and build.  Re-apply the product-flow contract only while the project is
        # genuinely empty; established projects keep normal conversational edits.
        is_empty_creation = (
            not [
                item for item in await runtime.repo.current_artifacts(project_id)
                if not item.type.startswith("source_")
            ]
            and not await runtime.repo.list_builds(project_id)
        )
        requested_workflow, requested_activated = _requested_skill_selection(
            runtime, body.workflow_id, body.activated_skill_ids, body.content,
        )
        workflow_id, activated_skill_ids = await _effective_skill_selection(
            runtime, project_id, requested_workflow, requested_activated,
        )
        prompt = (
            _initial_video_build_prompt(
                objective=body.content,
                project_id=project_id,
                base_project_version_id=project.current_version_id,
                idempotency_key=body.idempotency_key,
                user_option=body.user_option,
                input_files=imported_input_files,
                workflow_id=workflow_id,
                activated_skill_ids=activated_skill_ids,
            )
            if is_empty_creation
            else body.content
        )
        if not is_empty_creation and (body.user_option or imported_input_files):
            prompt += (
                "\n\nCurrent creation controls and inputs:\n"
                f"{json.dumps({'user_option': body.user_option or {}, 'input_files': _prompt_input_files(imported_input_files)}, ensure_ascii=False, sort_keys=True)}"
            )
        if not is_empty_creation:
            prompt += _selection_context(workflow_id, activated_skill_ids)
        await dsh.prompt(binding.session_id, prompt)
        await runtime.repo.remember_operation_result(
            project_id, "compat-message", body.idempotency_key, binding.session_id,
        )
    except DeepSeekHarnessError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return success(await _run_with_context(
        runtime, project, binding.session_id, await _history(dsh, binding.session_id),
    ))


@router.post("/runs/{project_id}/resume")
async def resume_run(
    project_id: str,
    body: MessageBody,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    return await add_message(project_id, body, user_id, runtime, dsh)


@router.get("/runs/{project_id}/messages")
async def list_messages(
    project_id: str,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    project, binding = await _project_and_binding(runtime, project_id, user_id)
    return success(_messages(project.id, await _history(dsh, binding.session_id)))


def _compat_event(project_id: str, event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if event_type in {"user/message", "assistant/message"}:
        role = "user" if event_type == "user/message" else "assistant"
        source = data.get("source")
        if role == "user" and isinstance(source, dict) and source.get("kind") != "user":
            return None
        content_source = data.get("content") if role == "user" else (
            data.get("message", {}).get("content")
            if isinstance(data.get("message"), dict) else ""
        )
        mapped_type = "chat.message.created"
        milliseconds = event.get("time") if isinstance(event.get("time"), (int, float)) else 0
        created_at = datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()
        visible_content = (
            _visible_user_text(_text(content_source)) if role == "user"
            else _text(content_source)
        )
        if not visible_content:
            return None
        payload = {"message": {
            "id": f"dsh-{project_id}-{event.get('seq', 0)}",
            "run_id": project_id,
            "role": role,
            "content": visible_content,
            "sequence": event.get("seq", 0) + 1,
            "metadata": {"source": "deepseek-harness"},
            "created_at": created_at,
        }}
    elif event_type == "assistant/chunk":
        chunk = data.get("chunk") if isinstance(data.get("chunk"), dict) else {}
        usage = _usage_payload(data)
        if usage is not None:
            mapped_type, payload = "llm.usage", usage
        # Only final-answer text is streamed into chat. Reasoning blocks remain
        # private; the production-progress UI exposes phase/tool/build telemetry.
        elif chunk.get("type") != "text-delta" or not isinstance(chunk.get("text"), str):
            return None
        else:
            mapped_type = "agent.message.delta"
            payload = {
                "delta": chunk["text"],
                "message_id": f"stream-{project_id}-turn-{data.get('turn', 0)}",
            }
    elif event_type == "tool/call":
        mapped_type = "agent.tool.started"
        payload = {
            "turn": data.get("turn"),
            "step": data.get("step"),
            "call_id": str(data.get("callId") or ""),
            "tool": str(data.get("name") or "unknown"),
            "input": _json_arguments(data.get("arguments")),
        }
    elif event_type == "tool/result":
        call_id, output, is_error = _tool_result(data)
        mapped_type = "agent.tool.completed"
        payload = {
            "turn": data.get("turn"),
            "step": data.get("step"),
            "call_id": call_id,
            "output": output,
            "is_error": is_error,
            "error": output if is_error else None,
        }
    elif event_type == "turn/start":
        mapped_type, payload = "agent.started", data
    elif event_type == "turn/end":
        status = _status([event])
        mapped_type = {
            "completed": "run.completed",
            "failed": "run.failed",
            "cancelled": "run.cancelled",
        }.get(status, "run.completed")
        payload = {**data, "status": status}
    elif event_type == "request/header":
        header = data.get("header") if isinstance(data.get("header"), dict) else {}
        config = header.get("config") if isinstance(header.get("config"), dict) else {}
        mapped_type = "llm.request.started"
        payload = {
            "provider": config.get("provider"),
            "model": config.get("model"),
            "reasoning_effort": config.get("reasoningEffort"),
            "tool_count": len(header.get("tools", [])) if isinstance(header.get("tools"), list) else 0,
        }
    elif event_type == "request/context":
        mapped_type = "llm.context.ready"
        payload = {
            "provider": data.get("provider"),
            "model": data.get("model"),
            "context_window": data.get("contextWindow"),
        }
    elif event_type == "step/start":
        mapped_type, payload = "agent.step.started", data
    elif event_type == "step/end":
        mapped_type, payload = "agent.step.completed", data
    elif event_type == "agent/inbox/spliced":
        inserted = data.get("inserted") if isinstance(data.get("inserted"), list) else []
        mapped_type = "agent.context.updated"
        payload = {
            "target": data.get("target"),
            "inserted_count": len(inserted),
            "removed_count": int(data.get("removedCount") or 0),
        }
    else:
        mapped_type, payload = f"deepseek.{event_type}", data
    milliseconds = event.get("time") if isinstance(event.get("time"), (int, float)) else 0
    sequence = event.get("seq", 0) + 1
    return {
        "id": f"dsh-{project_id}-{event.get('seq', 0)}",
        "run_id": project_id,
        "sequence": sequence,
        "type": mapped_type,
        "payload": payload,
        "created_at": datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(),
    }


@router.get("/runs/{project_id}/event-log")
async def event_log(
    project_id: str,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    _project, binding = await _project_and_binding(runtime, project_id, user_id)
    return success([
        mapped for item in await _history(dsh, binding.session_id)
        if (mapped := _compat_event(project_id, item)) is not None
    ])


@router.get("/runs/{project_id}/events", response_model=None)
async def stream_events(
    request: Request,
    project_id: str,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    _project, binding = await _project_and_binding(runtime, project_id, user_id)

    async def stream() -> AsyncIterator[str]:
        cursor = after
        while not await request.is_disconnected():
            events = [
                item for item in await _history(dsh, binding.session_id)
                if isinstance(item.get("seq"), int) and item["seq"] + 1 > cursor
            ]
            if not events:
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)
                continue
            for item in events:
                mapped = _compat_event(project_id, item)
                if mapped is None:
                    cursor = item["seq"] + 1
                    continue
                cursor = mapped["sequence"]
                yield (
                    f"id: {mapped['sequence']}\nevent: {mapped['type']}\n"
                    f"data: {json.dumps(mapped, ensure_ascii=False)}\n\n"
                )

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/runs/{project_id}/cancel")
async def cancel_run(
    project_id: str,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    project, binding = await _project_and_binding(runtime, project_id, user_id)
    try:
        await dsh.cancel(binding.session_id)
    except DeepSeekHarnessError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return success(await _run_with_context(
        runtime, project, binding.session_id, await _history(dsh, binding.session_id),
    ))


@router.post("/runs/{project_id}/artifacts/{version_id}/select")
async def select_artifact(
    project_id: str,
    version_id: str,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    project, _binding = await _project_and_binding(runtime, project_id, user_id)
    current = await runtime.repo.get_project_version(project.current_version_id)
    already_selected = next(
        (item for item in await runtime.repo.current_artifacts(project_id) if item.id == version_id),
        None,
    )
    if already_selected is not None:
        artifact, version = already_selected, current
    else:
        try:
            artifact, version = await runtime.select_artifact(
                project_id=project_id,
                version_id=version_id,
                base_project_version_id=current.id,
                idempotency_key=f"compat-select:{current.id}:{version_id}",
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="artifact version not found") from exc
    return success({
        "project_id": project_id,
        "type": artifact.type,
        "artifact_version_id": artifact.id,
        "updated_at": version.created_at.isoformat(),
    })


@router.get("/runs/{project_id}/token-usage")
async def token_usage(
    project_id: str,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    _project, binding = await _project_and_binding(runtime, project_id, user_id)
    records = [
        usage for event in await _history(dsh, binding.session_id)
        if (usage := _usage_payload(
            event.get("data") if isinstance(event.get("data"), dict) else {}
        )) is not None
    ]
    input_tokens = sum(int(item["input_tokens"]) for item in records)
    output_tokens = sum(int(item["output_tokens"]) for item in records)
    cached_tokens = sum(int(item["cached_tokens"]) for item in records)
    reasoning_tokens = sum(int(item["reasoning_tokens"]) for item in records)
    total_tokens = sum(int(item["total_tokens"]) for item in records)
    return success({
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
        "requested_tokens": 0,
        "calls": len(records),
        "failed_calls": 0,
        "retry_attempts": 0,
        "by_scope": {"deepseek-harness": {
            "calls": len(records),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }} if records else {},
        "by_task": [],
        "records": records,
        "attempt_records": [],
    })


@router.get("/skills")
async def list_skills(
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    return success(runtime.skills.prompt_view())


@router.post("/uploads")
async def upload_files(
    user_id: Annotated[str, Depends(identity)],
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    images, audio_files, video_files = await process_uploaded_files(files)
    uploaded = []
    for kind, items in (("image", images), ("audio", audio_files), ("video", video_files)):
        for item in items:
            metadata = item.model_dump(mode="json")
            metadata["upload_receipt"] = _sign_uploaded_file(user_id, kind, item.url)
            uploaded.append({
                "type": kind,
                "url": item.url,
                "filename": getattr(item, "filename", None),
                "metadata": metadata,
            })
    return success({"files": uploaded})


async def _studio_project(
    runtime: VideoBuildRuntime,
    session_id: str,
    user_id: str,
):
    try:
        return await runtime.repo.project_for_session(session_id, user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Studio project not found") from exc


@studio_router.post("/projects")
async def studio_create_project(
    body: StudioProjectBody,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    created = await create_run(
        CreateRunBody(
            objective=body.objective,
            idempotency_key=f"studio-create:{uuid4()}",
            thread_id=body.thread_id,
            user_option=body.user_option,
            input_files=body.input_files,
            workflow_id=body.workflow_id,
            activated_skill_ids=body.activated_skill_ids,
        ),
        user_id,
        runtime,
        dsh,
    )
    return success({"project": created["data"], "suggested_plan": []})


@studio_router.get("/projects/{session_id}")
async def studio_get_project(
    session_id: str,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    project, binding = await _studio_project(runtime, session_id, user_id)
    return success(await _snapshot(runtime, dsh, project, binding.session_id))


@studio_router.post("/projects/{session_id}/commands")
async def studio_add_command(
    session_id: str,
    body: StudioProjectBody,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    project, binding = await _studio_project(runtime, session_id, user_id)
    imported_input_files = await _import_input_files(
        runtime, project.id, user_id, body.input_files,
    )
    if imported_input_files:
        project = await runtime.repo.get_project(project.id)
    requested_workflow, requested_activated = _requested_skill_selection(
        runtime, body.workflow_id, body.activated_skill_ids, body.objective,
    )
    workflow_id, activated_skill_ids = await _effective_skill_selection(
        runtime, project.id, requested_workflow, requested_activated,
    )
    try:
        prompt = body.objective
        if body.user_option or imported_input_files:
            prompt += (
                "\n\nCurrent creation controls and inputs:\n"
                f"{json.dumps({'user_option': body.user_option or {}, 'input_files': _prompt_input_files(imported_input_files)}, ensure_ascii=False, sort_keys=True)}"
            )
        await dsh.prompt(
            binding.session_id,
            prompt + _selection_context(workflow_id, activated_skill_ids),
        )
    except DeepSeekHarnessError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return success({
        "project": await _run_with_context(
            runtime, project, binding.session_id, await _history(dsh, binding.session_id),
        ),
        "suggested_plan": [],
    })


@studio_router.get("/projects/{session_id}/events")
async def studio_events(
    session_id: str,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    dsh: Annotated[DeepSeekHarnessClient, Depends(get_deepseek_client)],
) -> dict[str, Any]:
    project, binding = await _studio_project(runtime, session_id, user_id)
    return success([
        mapped for item in await _history(dsh, binding.session_id)
        if (mapped := _compat_event(project.id, item)) is not None
    ])


@studio_router.get("/projects/{session_id}/skills")
async def studio_project_skills(
    session_id: str,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    project, _binding = await _studio_project(runtime, session_id, user_id)
    return success([
        item.model_dump(mode="json")
        for item in await runtime.list_project_skill_locks(project.id)
    ])


@studio_router.post("/projects/{session_id}/skills/{skill_id}/enable")
async def studio_set_project_skill(
    session_id: str,
    skill_id: str,
    body: StudioSkillEnableBody,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    project, _binding = await _studio_project(runtime, session_id, user_id)
    try:
        lock = await runtime.set_project_skill_enabled(
            project_id=project.id,
            skill_id=skill_id,
            enabled=body.enabled,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return success(lock.model_dump(mode="json"))


@studio_router.post("/projects/{session_id}/artifacts/{version_id}/select")
async def studio_select_artifact(
    session_id: str,
    version_id: str,
    user_id: Annotated[str, Depends(identity)],
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    project, _binding = await _studio_project(runtime, session_id, user_id)
    return await select_artifact(project.id, version_id, user_id, runtime)


@studio_router.get("/skills")
async def studio_list_skills(
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    return await list_skills(runtime)


@studio_router.post("/skills/install")
async def studio_install_skill(
    runtime: Annotated[VideoBuildRuntime, Depends(get_runtime)],
    bundle: Annotated[UploadFile, File()],
    overwrite: Annotated[bool, Form()] = False,
) -> dict[str, Any]:
    from app.chat.v2.skill_install import (
        MAX_BUNDLE_BYTES,
        SkillInstallError,
        extract_skill_archive,
        install_skill_directory,
    )

    data = await bundle.read(MAX_BUNDLE_BYTES + 1)
    try:
        with tempfile.TemporaryDirectory(prefix="cuti-skill-upload-") as directory:
            source = extract_skill_archive(data, Path(directory))
            name = install_skill_directory(
                source,
                runtime.skills.external_root,
                catalog=runtime.skills.catalog,
                registry=runtime.skills.capabilities,
                overwrite=overwrite,
            )
        skill_count = await runtime.reload_skills()
    except (SkillInstallError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return success({"name": name, "skill_count": skill_count})
