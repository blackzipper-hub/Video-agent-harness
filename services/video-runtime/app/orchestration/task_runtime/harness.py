from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.chat.config import get_settings

from app.chat.v2.agent_trace import trace_agent_event
from app.chat.v2.capabilities import CapabilityRegistry
from app.orchestration.context import ContextAssembler
from app.orchestration.skills import SkillContext, SkillResolutionRequest, SkillResolver
from app.chat.v2.executors import CapabilityExecutor
from app.chat.v2.generation_limits import (
    count_in_flight_generation_tasks,
    is_video_generation_task,
)
from app.chat.v2.language import (
    active_language_skill,
    canonical_language_skill_name,
    explicit_language_skill,
    language_contract,
    output_language_instruction,
    resolve_output_language,
)
from app.chat.v2.models import (
    AgentRun, ArtifactEdge, ArtifactVersion, AttemptStatus, ChatMessage,
    DomainEvent, InputFile, PlanPatch, RunStatus, SourceEvent, Task,
    TaskAttempt, TaskStatus, ToolInvocation, UserCommand, now, uid,
)
from app.orchestration.policy import PlanValidator
from app.chat.v2.repository import NONTERMINAL_TASK_STATUSES, V2Repository
from app.orchestration.recovery import generated_content, markdown_media, payload_media_uri
from app.chat.v2.workflows import (
    active_workflow,
    capability_requires_workflow,
    inject_workflow_parameters,
    is_workflow_skill,
    parse_explicit_skill_names,
)
from app.chat.v2.token_usage import V2TokenUsageCallback, llm_attempt_audit
from langchain_core.runnables.config import RunnableConfig, var_child_runnable_config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskFailureDiagnosis:
    category: str
    detail: str
    retryable: bool
    action_required: str


def _safe_exception_detail(exc: BaseException) -> str:
    """Flatten wrapped service errors without leaking API keys into chat."""
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip()
        label = type(current).__name__
        parts.append(f"{label}: {text}" if text else label)
        current = (
            getattr(current, "original", None)
            or current.__cause__
            or current.__context__
        )
    detail = " <- ".join(dict.fromkeys(parts))[:800]
    return re.sub(r"sk-(?:proj-)?[A-Za-z0-9_-]{12,}", "sk-***", detail)


def _exception_chain(exc: BaseException):
    """Yield service exceptions through the wrappers used by stage executors."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = (
            getattr(current, "original", None)
            or current.__cause__
            or current.__context__
        )


def _diagnose_task_failure(task: Task, exc: BaseException) -> TaskFailureDiagnosis:
    detail = _safe_exception_detail(exc)
    lowered = detail.lower()
    # Provider bridges already return a structured retry contract. Executors may
    # wrap it in RuntimeError, so inspect the complete exception chain before
    # falling back to text heuristics. Losing this metadata previously turned a
    # retryable WaveSpeed reservation error into unhandled_task_error.
    for cause in _exception_chain(exc):
        category = getattr(cause, "category", None)
        retryable = getattr(cause, "retryable", None)
        if category and isinstance(retryable, bool):
            if category == "insufficient_credits" and retryable:
                action = (
                    "系统会在额度预占状态稳定后自动重试一次；若再次收到相同错误，"
                    "再检查 WaveSpeed 实时余额与账单状态。"
                )
            else:
                action = (
                    "系统会自动重试；若重试耗尽，请检查上游服务状态、账户配置和网络。"
                    if retryable else
                    "请检查上游服务返回的错误详情和任务输入后重新执行。"
                )
            return TaskFailureDiagnosis(str(category), detail, retryable, action)
    if "requires openai_api_key" in lowered or "deep_agent_v2_openai_api_key" in lowered:
        return TaskFailureDiagnosis(
            "missing_configuration", detail, False,
            "请配置 OPENAI_API_KEY 或 DEEP_AGENT_V2_OPENAI_API_KEY，然后重新提交。",
        )
    if any(token in lowered for token in ("invalid_api_key", "incorrect api key", "401 unauthorized")):
        return TaskFailureDiagnosis(
            "invalid_credentials", detail, False,
            "请更新有效的 OpenAI API Key，并确认该 Key 有音频转写权限。",
        )
    if any(token in lowered for token in ("insufficient_quota", "quota exceeded", "billing_hard_limit")):
        return TaskFailureDiagnosis(
            "quota_exhausted", detail, False,
            "请检查 OpenAI 项目的余额、用量上限和账单状态。",
        )
    if "no transcribable audio" in lowered or "no audio stream" in lowered:
        return TaskFailureDiagnosis(
            "missing_audio", detail, False,
            "请选择包含有效音轨的视频，或先为视频添加音频后再生成字幕。",
        )
    if "exceeds transcription limit" in lowered:
        return TaskFailureDiagnosis(
            "input_too_large", detail, False,
            "请压缩/缩短音频，或提高 SUBTITLE_TRANSCRIPTION_MAX_BYTES 配置。",
        )
    retryable_tokens = (
        "timeout", "timed out", "readtimeout", "connecttimeout", "connecterror",
        "connection refused", "connection reset", "temporarily unavailable",
        "service unavailable", " 429", " 500", " 502", " 503", " 504",
        "provider_pending_timeout", "retryablemediaserviceerror",
    )
    retryable = isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or any(
        token in lowered for token in retryable_tokens
    )
    if retryable:
        action = (
            "系统会自动重试；若重试耗尽，请检查 MEDIA_SERVICE_URL、媒体服务状态、"
            "代理网络以及上游 API 可用性。"
        )
        return TaskFailureDiagnosis("transient_service_error", detail, True, action)
    return TaskFailureDiagnosis(
        "unhandled_task_error", detail, False,
        "请根据上方错误检查输入与配置；修正后在当前对话中要求重新执行该步骤。",
    )


class DynamicHarness:
    def __init__(
        self,
        repository: V2Repository,
        capabilities: CapabilityRegistry,
        executor: CapabilityExecutor,
        validator: PlanValidator,
        context_assembler: ContextAssembler | None = None,
        skill_resolver: SkillResolver | None = None,
        *,
        max_parallel_generation_tasks: int | None = None,
        max_active_runs_per_user: int | None = None,
        task_max_attempts: int | None = None,
        task_timeout_seconds: float | None = None,
        transcription_timeout_seconds: float | None = None,
        provider_timeout_seconds: float | None = None,
        progress_initial_seconds: float | None = None,
        progress_interval_seconds: float | None = None,
        retry_base_delay_seconds: float | None = None,
    ):
        self.repo = repository
        self.capabilities = capabilities
        self.executor = executor
        self.validator = validator
        self.context_assembler = context_assembler or ContextAssembler()
        self.skill_resolver = skill_resolver
        self.max_parallel_generation_tasks = max(
            1,
            max_parallel_generation_tasks
            if max_parallel_generation_tasks is not None
            else getattr(validator, "max_parallel_generation_tasks", 2),
        )
        self.coordinator = None
        settings = get_settings()
        self.task_max_attempts = int(
            task_max_attempts or settings.DEEP_AGENT_V2_TASK_MAX_ATTEMPTS
        )
        self.max_active_runs_per_user = (
            max_active_runs_per_user
            if max_active_runs_per_user is not None
            else (
                settings.DEEP_AGENT_V2_MAX_ACTIVE_RUNS_PER_USER
                if settings.DEEP_AGENT_V2_ACTIVE_RUN_LIMIT_ENABLED
                else 0
            )
        )
        self.task_timeout_seconds = float(
            task_timeout_seconds or settings.DEEP_AGENT_V2_TASK_TIMEOUT_SECONDS
        )
        self.transcription_timeout_seconds = float(
            transcription_timeout_seconds
            or settings.DEEP_AGENT_V2_TRANSCRIPTION_TIMEOUT_SECONDS
        )
        self.provider_timeout_seconds = float(
            provider_timeout_seconds or settings.DEEP_AGENT_V2_PROVIDER_TIMEOUT_SECONDS
        )
        self.progress_initial_seconds = float(
            progress_initial_seconds or settings.DEEP_AGENT_V2_PROGRESS_INITIAL_SECONDS
        )
        self.progress_interval_seconds = float(
            progress_interval_seconds or settings.DEEP_AGENT_V2_PROGRESS_INTERVAL_SECONDS
        )
        self.retry_base_delay_seconds = float(
            retry_base_delay_seconds
            if retry_base_delay_seconds is not None
            else settings.DEEP_AGENT_V2_RETRY_BASE_DELAY_SECONDS
        )
        self.tasks: set[asyncio.Task] = set()
        self.execution_tasks: dict[str, asyncio.Task] = {}
        self.coordinator_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.closing = False

    def set_coordinator(self, coordinator) -> None:
        self.coordinator = coordinator

    def _spawn(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._task_finished)
        return task

    def _task_finished(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("V2 background task failed", exc_info=task.exception())

    async def close(self) -> None:
        self.closing = True
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        # Never spend model/provider tokens implicitly after a restart. A
        # graceful stop fails every unfinished project immediately; a hard
        # process kill is repaired by resume_active_runs() on the next boot.
        await self.repo.fail_active_runs(
            "The backend stopped before this project completed. Resume it explicitly to continue."
        )
        if self.coordinator:
            await self.coordinator.close()

    async def create_run(
        self,
        *,
        user_id: str,
        objective: str,
        idempotency_key: str,
        thread_id: str | None = None,
        user_option: dict | None = None,
        input_files: list[InputFile] | None = None,
        output_language: str | None = None,
    ) -> AgentRun:
        if thread_id:
            existing = await self.repo.get_run_by_thread(user_id, thread_id)
            if existing:
                return await self.add_message(
                    existing.id, user_id, objective, idempotency_key,
                    user_option=user_option, input_files=input_files,
                    output_language=output_language,
                )
        stable_id = thread_id or hashlib.sha256(
            f"{user_id}:{idempotency_key}".encode()
        ).hexdigest()[:32]
        activated_skills = self._merge_activated_skills([], objective)
        run = AgentRun(
            thread_id=stable_id, project_id=stable_id, user_id=user_id,
            title=objective.strip()[:80], objective=objective,
            idempotency_key=idempotency_key, user_option=user_option,
            output_language=resolve_output_language(
                objective,
                requested=output_language,
                language_skill=active_language_skill(activated_skills),
            ),
            input_files=input_files or [],
            activated_skills=activated_skills,
        )
        stored, created = await self.repo.create_run(
            run, max_active_runs=self.max_active_runs_per_user,
        )
        if not created:
            return stored
        await self._emit(stored.id, "run.created", {
            "objective": objective, "thread_id": stored.thread_id,
        })
        await self._append_message(stored.id, "user", objective, {
            "user_option": user_option,
            "thread_id": stored.thread_id,
            "input_files": [
                item.model_dump(mode="json") for item in input_files or []
            ],
        })
        self._spawn(self._coordinate(stored.id, "New user goal."))
        return stored

    async def add_message(
        self,
        run_id: str,
        user_id: str,
        content: str,
        idempotency_key: str,
        *,
        user_option: dict | None = None,
        input_files: list[InputFile] | None = None,
        thread_id: str | None = None,
        output_language: str | None = None,
    ) -> AgentRun:
        run = await self.repo.get_run(run_id)
        if not run or run.user_id != user_id:
            raise LookupError("run not found")
        activated_skills = self._merge_activated_skills(
            run.activated_skills, content
        )
        run.objective = content
        run.output_language = resolve_output_language(
            content,
            requested=output_language,
            current=run.output_language,
            language_skill=active_language_skill(activated_skills),
        )
        run.goal_started_revision = run.current_revision
        run.status = RunStatus.PLANNING
        run = await self.repo.activate_run(
            run, max_active_runs=self.max_active_runs_per_user,
        )
        if not await self.repo.add_command(UserCommand(
            run_id=run_id, content=content, idempotency_key=idempotency_key,
        )):
            return run
        if user_option is not None:
            run.user_option = user_option
        if input_files:
            known = {(item.type, item.url) for item in run.input_files}
            run.input_files.extend(
                item for item in input_files if (item.type, item.url) not in known
            )
        run.activated_skills = activated_skills
        activated_by_confirmation = None
        if active_workflow(run.activated_skills) is None:
            confirmed = await self._confirmed_workflow_from_reply(run_id, content)
            if confirmed:
                helpers = [
                    name for name in run.activated_skills
                    if not is_workflow_skill(name)
                ]
                run.activated_skills = [confirmed, *helpers]
                activated_by_confirmation = confirmed
        session_thread_id = (thread_id or run.thread_id or "").strip() or run.thread_id
        await self._append_message(run_id, "user", content, {
            "user_option": user_option,
            "thread_id": session_thread_id,
            "input_files": [item.model_dump(mode="json") for item in input_files or []],
            "activated_skills": list(run.activated_skills),
        })
        await self.repo.save_run(run)
        if activated_by_confirmation:
            await self._emit(run_id, "workflow.activated", {
                "skill_name": activated_by_confirmation,
                "source": "user_confirmation",
                "content": content,
            })
        await self._refresh_summary(run_id)
        await self._emit(run_id, "run.message.received", {"content": content})
        self._spawn(self._coordinate(run_id, "The user changed or extended the goal."))
        return run

    async def _coordinate(self, run_id: str, observation: str) -> None:
        if not self.coordinator:
            raise RuntimeError("V2 coordinator is not configured")
        async with self.coordinator_locks[run_id]:
            run = await self.repo.get_run(run_id)
            if not run or run.status in {
                RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED,
            }:
                return
            snapshot = await self.repo.snapshot(run_id)
            context = self.context_assembler.assemble(snapshot)
            await self._emit(run_id, "agent.started", {"observation": observation})
            try:
                await self.coordinator.run(
                    run_id, f"{observation}\n\nAuthoritative business context:\n{context}"
                )
            except Exception as exc:
                logger.exception("Deep Agent coordination failed")
                error_message = self._format_coordination_error(exc)
                trace_agent_event(
                    get_settings(),
                    "coordination.failed",
                    run_id=run_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                    observation=observation,
                )
                snapshot = await self.repo.snapshot(run_id)
                active = [task for task in snapshot.tasks if task.status in {
                    TaskStatus.PROPOSED, TaskStatus.BLOCKED, TaskStatus.READY,
                    TaskStatus.RUNNING, TaskStatus.WAITING_EXTERNAL,
                }] if snapshot else []
                if snapshot and active:
                    snapshot.run.status = (
                        RunStatus.WAITING_EXTERNAL
                        if any(item.status == TaskStatus.WAITING_EXTERNAL for item in active)
                        else RunStatus.RUNNING
                    )
                    snapshot.run.last_response = error_message
                    await self.repo.save_run(snapshot.run)
                    await self._append_message(run_id, "assistant", error_message)
                    await self._emit(run_id, "agent.coordination_failed", {
                        "error": error_message, "recoverable": True,
                    })
                else:
                    if snapshot:
                        # A coordination error with no unfinished capability is
                        # terminal. Keeping it as PLANNING makes service startup
                        # silently spend tokens on the same failed request again.
                        snapshot.run.status = RunStatus.FAILED
                        snapshot.run.last_response = error_message
                        await self.repo.save_run(snapshot.run)
                    await self._append_message(run_id, "assistant", error_message)
                    await self._emit(run_id, "agent.coordination_failed", {
                        "error": error_message, "recoverable": True,
                    })
                return
            await self._emit(run_id, "agent.completed")

    async def project_snapshot(self, run_id: str) -> dict:
        snapshot = await self.repo.snapshot(run_id)
        if not snapshot:
            raise LookupError("run not found")
        return snapshot.model_dump(mode="json")

    def _ensure_project_thread_parameters(
        self,
        run: AgentRun,
        patch: PlanPatch,
    ) -> PlanPatch:
        """Guarantee stage skills receive the session project thread_id + workflow mode."""
        thread_id = (run.thread_id or "").strip()
        workflow = active_workflow(run.activated_skills)
        if (not thread_id and workflow is None) or not patch.add_tasks:
            return patch
        for planned in patch.add_tasks:
            capability = self.capabilities.get(planned.capability_id)
            schema = capability.parameters_schema
            parameters = dict(planned.parameters or {})
            # Workflow defaults belong only to generation stages. Injecting
            # Seedance fields into strict post-production schemas (for example
            # media.concat) makes otherwise valid tasks impossible to create.
            if workflow is not None and capability_requires_workflow(
                capability.id
            ):
                parameters = inject_workflow_parameters(parameters, workflow)
            if capability.id in {"api.provider.generate", "api.ark_protocol.generate"}:
                profile = (
                    dict(parameters.get("body") or {})
                    if capability.id == "api.ark_protocol.generate"
                    else parameters
                )
                dependency_names = set(workflow.skill_dependencies if workflow else [])
                provider_language = (
                    "zh-CN"
                    if "seedance2" in dependency_names
                    or "seedance2" in run.activated_skills
                    else "auto"
                )
                resolved_language_contract = language_contract(
                    run.output_language,
                    provider_prompt_language=provider_language,
                )
                workflow_contract = profile.get("_workflow_contract")
                if isinstance(workflow_contract, dict):
                    workflow_contract = dict(workflow_contract)
                    workflow_contract["language_contract"] = resolved_language_contract
                    profile["_workflow_contract"] = workflow_contract
                else:
                    profile["_language_contract"] = resolved_language_contract
                if capability.id == "api.ark_protocol.generate":
                    parameters["body"] = profile
            if (
                workflow is not None
                and workflow.skill_name == "cuti-product-workflow"
                and capability.id in {"api.provider.generate", "api.ark_protocol.generate"}
            ):
                profile = (
                    dict(parameters.get("body") or {})
                    if capability.id == "api.ark_protocol.generate"
                    else parameters
                )
                contract = dict(profile.get("_workflow_contract") or {})
                voice_profile = contract.get("voice_profile")
                if isinstance(voice_profile, dict) and voice_profile:
                    voice_profile = dict(voice_profile)
                    voice_profile["language"] = language_contract(
                        run.output_language,
                    )["spoken_language"]
                    contract["voice_profile"] = voice_profile
                    canonical = json.dumps(
                        voice_profile,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    contract["voice_profile_hash"] = hashlib.sha256(
                        canonical.encode("utf-8")
                    ).hexdigest()
                    profile["_workflow_contract"] = contract
                    if capability.id == "api.ark_protocol.generate":
                        parameters["body"] = profile
            if (
                thread_id
                and not (
                    schema
                    and schema.get("additionalProperties") is False
                    and "thread_id" not in schema.get("properties", {})
                    and "project_thread_id" not in schema.get("properties", {})
                )
                and not str(parameters.get("thread_id") or "").strip()
                and not str(parameters.get("project_thread_id") or "").strip()
            ):
                parameters["thread_id"] = thread_id
            planned.parameters = parameters
        return patch

    def _resolve_stage_skills(self, run: AgentRun, patch: PlanPatch) -> PlanPatch:
        if self.skill_resolver is None:
            return patch
        for planned in patch.add_tasks:
            capability = self.capabilities.get(
                planned.capability_id,
                require_enabled=False,
            )
            workflow = active_workflow(run.activated_skills)
            context = self.skill_resolver.resolve(SkillResolutionRequest(
                workflow_skill_id=workflow.skill_name if workflow is not None else None,
                activated_skill_ids=list(run.activated_skills),
                project_skill_locks=list(run.skill_locks),
                capability_id=capability.id,
                output_type=capability.output_type,
                capability_skill_id=capability.skill_name,
                constraint_contract=planned.constraint_contract,
            ))
            language_instruction = output_language_instruction(
                run.output_language,
                user_request=run.objective,
                language_skill=active_language_skill(run.activated_skills),
            )
            if context is None:
                context = SkillContext(instructions=language_instruction)
            else:
                context.instructions = "\n\n---\n\n".join(
                    [language_instruction, context.instructions]
                )
            planned.skill_context = context
            planned.resolved_skills = (
                list(context.applied_skills) if context is not None else []
            )
        return patch

    @staticmethod
    def _workflow_skills_from_text(text: str) -> list[str]:
        return [
            name for name in parse_explicit_skill_names(text)
            if is_workflow_skill(name)
        ]

    @classmethod
    def _merge_activated_workflows(
        cls,
        existing: list[str] | None,
        text: str,
    ) -> list[str]:
        """Activate explicitly named workflow skills; latest explicit wins as primary."""
        current = [name for name in (existing or []) if is_workflow_skill(name)]
        incoming = cls._workflow_skills_from_text(text)
        if not incoming:
            return current
        # Keep one active workflow: last explicit mention replaces prior workflows.
        primary = incoming[-1]
        extras = [name for name in incoming[:-1] if name != primary]
        return [primary, *extras]

    def _merge_activated_skills(
        self,
        existing: list[str] | None,
        text: str,
    ) -> list[str]:
        """Persist explicit helper Skills while preserving one primary workflow."""
        workflows = self._merge_activated_workflows(existing, text)
        incoming_language = explicit_language_skill(text)
        helpers = [
            name for name in (existing or [])
            if not is_workflow_skill(name)
            and (incoming_language is None or canonical_language_skill_name(name) is None)
        ]
        for raw_name in parse_explicit_skill_names(text):
            name = canonical_language_skill_name(raw_name) or raw_name
            if is_workflow_skill(name) or name in helpers:
                continue
            if self.skill_resolver is not None and self.skill_resolver.catalog.has(name):
                helpers.append(name)
        return [*workflows, *helpers]

    async def _confirmed_workflow_from_reply(
        self,
        run_id: str,
        content: str,
    ) -> str | None:
        """Resolve an affirmative reply against the latest input interruption.

        Resume buttons send ordinary localized chat text. Activation must be
        derived from the persisted workflow-confirm event, not from an agent's
        prose response.
        """
        text = (content or "").strip().lower()
        affirmatives = {
            "yes", "y", "ok", "okay", "confirm", "confirmed",
            "continue", "continue generation", "resume", "resume and continue",
            "确认", "同意", "好的", "可以", "就这个", "用这个",
            "继续", "继续生成", "恢复", "恢复并继续",
        }
        affirmative_prefixes = (
            "确认使用", "用 ", "使用 ", "继续生成", "恢复并继续",
        )
        if text not in affirmatives and not text.startswith(affirmative_prefixes):
            return None

        events = await self.repo.get_events(run_id, after=0)
        for event in reversed(events):
            if event.type != "run.waiting_input":
                continue
            interruption = (event.payload or {}).get("interruption") or {}
            if interruption.get("category") != "workflow_confirm":
                return None
            skill_name = interruption.get("skill_name")
            if skill_name and is_workflow_skill(skill_name):
                return skill_name
            return None
        return None

    async def _workflow_from_confirmation_reply(
        self,
        run_id: str,
        content: str,
    ) -> str | None:
        """If user affirms after workflow_confirm, activate the proposed skill."""
        text = (content or "").strip().lower()
        affirmatives = {
            "yes", "y", "ok", "okay", "confirm", "confirmed",
            "确认", "同意", "好的", "可以", "就这个", "用这个",
        }
        if text not in affirmatives and not any(
            text.startswith(item) for item in ("确认使用", "用 ", "使用 ")
        ):
            return None
        events = await self.repo.get_events(run_id, after=0)
        for event in reversed(events):
            if event.type != "run.waiting_input":
                continue
            interruption = (event.payload or {}).get("interruption") or {}
            if interruption.get("category") != "workflow_confirm":
                continue
            skill_name = interruption.get("skill_name")
            if skill_name and is_workflow_skill(skill_name):
                return skill_name
            break
        return None

    async def commit_agent_patch(self, run_id: str, patch: PlanPatch) -> dict:
        snapshot = await self.repo.snapshot(run_id)
        if not snapshot:
            raise LookupError("run not found")
        patch = self._ensure_project_thread_parameters(snapshot.run, patch)
        self.validator.validate(snapshot, patch)
        patch = self._resolve_stage_skills(snapshot.run, patch)
        created = await self.repo.apply_patch(snapshot.run, patch)
        interruption = (
            patch.interruption.model_dump(mode="json")
            if patch.interruption is not None
            else None
        )
        trace_agent_event(
            get_settings(),
            "plan.committed",
            run_id=run_id,
            revision=patch.base_revision + 1,
            reason=patch.reason,
            tasks=[item.model_dump(mode="json") for item in created],
            cancelled_task_ids=patch.cancel_task_ids,
            goal_satisfied=patch.goal_satisfied,
            waiting_for_input=patch.waiting_for_input,
            interruption=interruption,
            response=patch.response,
        )
        await self._emit(run_id, "plan.revised", {
            "revision": patch.base_revision + 1,
            "reason": patch.reason,
            "tasks": [item.model_dump(mode="json") for item in created],
            "cancelled_task_ids": patch.cancel_task_ids,
            "goal_satisfied": patch.goal_satisfied,
            "waiting_for_input": patch.waiting_for_input,
            "interruption": interruption,
            "response": patch.response,
        })
        if patch.response.strip():
            await self._append_message(run_id, "assistant", patch.response)
        if patch.goal_satisfied:
            await self._emit(run_id, "run.completed", {"response": patch.response})
        elif patch.waiting_for_input:
            await self._emit(run_id, "run.waiting_input", {
                "response": patch.response,
                "interruption": interruption,
                "what_happened": interruption["what_happened"],
                "why_interrupted": interruption["why_interrupted"],
                "why_confirm": interruption["what_next"],
                "confirmation_required": interruption["requires_confirmation"],
                "will_auto_resume": interruption["will_auto_resume"],
                "auto_resume_seconds": interruption["auto_resume_seconds"],
                "skill_name": interruption.get("skill_name"),
                "skill_resource": interruption.get("skill_resource"),
                "skill_policy": interruption.get("skill_policy"),
                "capability_id": interruption.get("capability_id"),
                "task_id": interruption.get("task_id"),
                "stage": interruption.get("stage"),
                "interrupt_category": interruption["category"],
            })
        else:
            await self._schedule_ready(run_id)
        await self._refresh_summary(run_id)
        return {
            "accepted": True, "revision": patch.base_revision + 1,
            "task_ids": [item.id for item in created],
        }

    async def record_agent_response(self, run_id: str, response: str) -> None:
        """Record non-plan commentary without implying goal satisfaction."""
        run = await self.repo.get_run(run_id)
        if not run:
            return
        run.last_response = response
        snapshot = await self.repo.snapshot(run_id)
        active = bool(snapshot and any(
            task.status in {
                TaskStatus.PROPOSED, TaskStatus.BLOCKED, TaskStatus.READY,
                TaskStatus.RUNNING, TaskStatus.WAITING_EXTERNAL,
            }
            for task in snapshot.tasks
        ))
        # Only PlanPatch(goal_satisfied=True) may complete a run. Plain
        # assistant text must never flip PLANNING → COMPLETED.
        if run.status == RunStatus.PLANNING and active:
            run.status = RunStatus.RUNNING
        await self.repo.save_run(run)
        await self._append_message(run_id, "assistant", response)
        await self._refresh_summary(run_id)

    async def _append_message(
        self, run_id: str, role: str, content: str, metadata: dict | None = None,
    ) -> ChatMessage | None:
        if not content.strip():
            return None
        message = await self.repo.add_chat_message(ChatMessage(
            run_id=run_id, role=role, content=content.strip(), metadata=metadata or {},
        ))
        await self._emit(run_id, "chat.message.created", {
            "message": message.model_dump(mode="json"),
        })
        return message

    async def _refresh_summary(self, run_id: str) -> None:
        snapshot = await self.repo.snapshot(run_id)
        if not snapshot:
            return
        summary, sequence = self.context_assembler.update_durable_summary(snapshot)
        snapshot.run.durable_summary = summary
        snapshot.run.summary_through_sequence = sequence
        await self.repo.save_run(snapshot.run)

    async def emit_agent_event(self, run_id: str, event_type: str, payload: dict) -> None:
        await self._emit(run_id, event_type, payload)

    @staticmethod
    def _reports_progress(task: Task) -> bool:
        return (
            task.capability_id in {
                "media.transcribe", "media.subtitle_burn", "api.provider.generate",
            }
            or "video.generate" in task.capability_id
            or "video_gen" in task.capability_id
        )

    def _attempt_timeout(self, task: Task) -> float:
        if task.capability_id == "media.transcribe":
            return self.transcription_timeout_seconds
        if task.capability_id == "api.provider.generate" or "video.generate" in task.capability_id:
            return self.provider_timeout_seconds
        return self.task_timeout_seconds

    async def _report_task_progress(self, task: Task) -> None:
        """Push durable progress into the chat while a long attempt is active."""
        try:
            await asyncio.sleep(self.progress_initial_seconds)
            while task.status == TaskStatus.RUNNING and not self.closing:
                anchor = task.heartbeat_at or task.started_at or now()
                elapsed = max(0, round((now() - anchor).total_seconds()))
                is_provider_submission_delay = bool(
                    task.capability_id == "api.provider.generate"
                    and task.execution_phase == "submitting"
                    and not task.remote_operation_id
                )
                if is_provider_submission_delay:
                    content = (
                        f"⚠️ 任务提交耗时异常：{task.objective}。已等待 {elapsed} 秒，"
                        "尚未取得上游任务 ID；系统仍在监控，达到超时阈值后会自动重试。"
                    )
                    severity = "warning"
                else:
                    content = (
                        f"进度更新：{task.objective} 仍在处理，已用时 {elapsed} 秒，"
                        f"当前阶段为 {task.execution_phase}。"
                    )
                    severity = "info"
                await self._append_message(task.run_id, "assistant", content, {
                    "kind": "task_progress",
                    "severity": severity,
                    "task_id": task.id,
                    "capability_id": task.capability_id,
                    "phase": task.execution_phase,
                    "elapsed_seconds": elapsed,
                    "remote_operation_id": task.remote_operation_id,
                })
                await self._emit(task.run_id, "task.progress_reported", {
                    "task_id": task.id,
                    "phase": task.execution_phase,
                    "elapsed_seconds": elapsed,
                    "severity": severity,
                    "remote_operation_id": task.remote_operation_id,
                })
                await asyncio.sleep(self.progress_interval_seconds)
        except asyncio.CancelledError:
            return

    async def _schedule_ready(self, run_id: str) -> None:
        snapshot = await self.repo.snapshot(run_id)
        if not snapshot:
            return
        in_flight = count_in_flight_generation_tasks(
            snapshot.tasks,
            self.capabilities,
        )
        limit = self.max_parallel_generation_tasks
        slots = max(0, limit - in_flight)
        for task in await self.repo.list_ready_tasks(run_id):
            if task.id in self.execution_tasks:
                continue
            is_gen = is_video_generation_task(self.capabilities, task)
            if is_gen:
                if slots <= 0:
                    continue
                slots -= 1
            task = await self.repo.claim_task(task.id)
            if task is None:
                continue
            task.max_attempts = self.task_max_attempts
            await self.repo.save_task(task)
            await self._emit(run_id, "task.started", {
                "task_id": task.id,
                "capability_id": task.capability_id,
                "attempt": task.attempt_count,
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "phase": task.execution_phase,
            })
            if self._reports_progress(task):
                await self._append_message(run_id, "assistant", (
                    f"已开始：{task.objective}（第 {task.attempt_count}/"
                    f"{self.task_max_attempts} 次尝试）。"
                ), {
                    "kind": "task_started",
                    "task_id": task.id,
                    "capability_id": task.capability_id,
                    "attempt": task.attempt_count,
                    "max_attempts": self.task_max_attempts,
                })
            execution = self._spawn(self._execute(task))
            self.execution_tasks[task.id] = execution
            execution.add_done_callback(
                lambda _finished, task_id=task.id, active_run_id=run_id:
                self._execution_finished(task_id, active_run_id)
            )

    def _execution_finished(self, task_id: str, run_id: str) -> None:
        self.execution_tasks.pop(task_id, None)
        # A retryable provider-poll timeout is persisted back to PROPOSED.
        # Scheduling after removing the local ownership marker lets it resume
        # polling the same remote id without creating another provider job.
        if not self.closing:
            self._spawn(self._schedule_ready(run_id))

    async def _execute(self, task: Task) -> None:
        snapshot = await self.repo.snapshot(task.run_id)
        if not snapshot:
            return
        attempt = TaskAttempt(
            run_id=task.run_id, task_id=task.id, number=max(1, task.attempt_count),
            # Stable across recovery: if the process dies after the provider
            # accepts a request but before its id is persisted, a retry can be
            # deduplicated by downstream executors that support idempotency.
            idempotency_key=f"{task.run_id}:{task.id}:submit",
        )
        await self.repo.add_attempt(attempt)
        invocation = ToolInvocation(
            run_id=task.run_id, task_id=task.id, tool_name=task.capability_id,
            idempotency_key=attempt.idempotency_key,
            arguments={
                "objective": task.objective,
                "input_artifact_version_ids": task.input_artifact_version_ids,
                "parameters": task.parameters,
                "skill_context": (
                    task.skill_context.model_dump(mode="json")
                    if task.skill_context is not None
                    else None
                ),
            },
        )
        await self.repo.add_tool_invocation(invocation)
        task.execution_phase = (
            "provider_polling" if task.remote_operation_id else "submitting"
        )
        task.heartbeat_at = now()
        await self.repo.save_task(task)
        await self._emit(task.run_id, "capability.started", {
            "task_id": task.id,
            "capability_id": task.capability_id,
            "attempt": attempt.number,
            "idempotency_key": attempt.idempotency_key,
            "phase": task.execution_phase,
        })
        trace_agent_event(
            get_settings(),
            "capability.started",
            run_id=task.run_id,
            task_id=task.id,
            capability_id=task.capability_id,
            objective=task.objective,
            input_artifact_version_ids=task.input_artifact_version_ids,
            parameters=task.parameters,
        )
        progress_reporter = (
            self._spawn(self._report_task_progress(task))
            if self._reports_progress(task)
            else None
        )
        try:
            # Default Deep Agent video delegates to full_auto so VideoAgent stage
            # gates auto-continue after the user already asked to generate.
            if (
                task.capability_id in {
                    "video.generate",
                    "video_gen.generate",
                    "video.pipeline.generate",
                    "video.edit",
                }
            ):
                params = dict(task.parameters or {})
                require_confirm = bool(
                    params.get("require_user_confirmation")
                    or params.get("require_confirmation")
                )
                option = dict(snapshot.run.user_option or {})
                if require_confirm:
                    option["full_auto"] = False
                elif "full_auto" not in option:
                    option["full_auto"] = True if "full_auto" not in params else bool(params.get("full_auto"))
                elif "full_auto" in params:
                    option["full_auto"] = bool(params.get("full_auto"))
                if option != dict(snapshot.run.user_option or {}):
                    snapshot.run.user_option = option
                    await self.repo.save_run(snapshot.run)
            async def remote_submitted(remote_id: str, provider: str) -> None:
                task.remote_operation_id = remote_id
                task.execution_phase = "provider_polling"
                task.heartbeat_at = now()
                attempt.remote_operation_id = remote_id
                invocation.result = {
                    "remote_operation_id": remote_id,
                    "provider": provider,
                    "phase": task.execution_phase,
                }
                await self.repo.save_task(task)
                await self.repo.save_attempt(attempt)
                await self.repo.save_tool_invocation(invocation)
                await self._emit(task.run_id, "task.remote_submitted", {
                    "task_id": task.id,
                    "remote_operation_id": remote_id,
                    "provider": provider,
                    "phase": task.execution_phase,
                })
                if self._reports_progress(task):
                    await self._append_message(task.run_id, "assistant", (
                        f"任务已提交到 {provider}，远程任务 ID：{remote_id}。"
                        "系统将持续跟踪状态，如有异常会自动反馈。"
                    ), {
                        "kind": "task_remote_submitted",
                        "task_id": task.id,
                        "provider": provider,
                        "remote_operation_id": remote_id,
                    })

            usage_callback = V2TokenUsageCallback(
                self.repo,
                run_id=task.run_id,
                scope="stage",
                task_id=task.id,
                capability_id=task.capability_id,
                attempt=attempt.number,
            )
            config_token = var_child_runnable_config.set(
                RunnableConfig(callbacks=[usage_callback])
            )
            try:
                with llm_attempt_audit(usage_callback.audit_attempt):
                    result = await asyncio.wait_for(
                        self.executor.submit(
                            snapshot.run,
                            task,
                            snapshot.artifacts,
                            attempt.idempotency_key,
                            on_remote_submitted=remote_submitted,
                        ),
                        timeout=self._attempt_timeout(task),
                    )
            finally:
                var_child_runnable_config.reset(config_token)
            task.remote_operation_id = result.remote_run_id
            task.remote_thread_id = result.remote_thread_id
            task.heartbeat_at = now()
            attempt.remote_operation_id = result.remote_run_id
            invocation.status = result.status
            invocation.result = {
                "remote_operation_id": result.remote_run_id,
                "remote_thread_id": result.remote_thread_id,
            }
            await self.repo.save_tool_invocation(invocation)
            if result.artifact:
                await self._complete_task(task, attempt, result.artifact)
                return
            task.status = TaskStatus.WAITING_EXTERNAL
            task.execution_phase = "waiting_external"
            attempt.status = AttemptStatus.WAITING_EXTERNAL
            await self.repo.save_task(task)
            await self.repo.save_attempt(attempt)
            snapshot.run.status = RunStatus.WAITING_EXTERNAL
            await self.repo.save_run(snapshot.run)
            await self._emit(task.run_id, "task.waiting_external", {
                "task_id": task.id, "remote_operation_id": result.remote_run_id,
                "remote_thread_id": result.remote_thread_id,
            })
        except Exception as exc:
            diagnosis = _diagnose_task_failure(task, exc)
            trace_agent_event(
                get_settings(),
                "capability.failed",
                run_id=task.run_id,
                task_id=task.id,
                capability_id=task.capability_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            provider_still_running = bool(
                task.remote_operation_id
                and "provider_pending_timeout" in str(exc)
            )
            if provider_still_running:
                task.status = TaskStatus.PROPOSED
                task.error = None
                task.execution_phase = "queued_after_poll_timeout"
                task.heartbeat_at = now()
                attempt.status = AttemptStatus.WAITING_EXTERNAL
                attempt.error = str(exc)
                attempt.finished_at = now()
                invocation.status = "poll_timeout"
                invocation.result = {
                    "remote_operation_id": task.remote_operation_id,
                    "error": str(exc),
                    "retryable": True,
                }
                await self.repo.save_task(task)
                await self.repo.save_attempt(attempt)
                await self.repo.save_tool_invocation(invocation)
                await self._emit(task.run_id, "task.polling_timeout", {
                    "task_id": task.id,
                    "remote_operation_id": task.remote_operation_id,
                    "retryable": True,
                    "action": "resume_provider_polling",
                })
                await self._append_message(task.run_id, "assistant", (
                    f"⚠️ 上游任务仍在运行，但本轮轮询已超时：{task.objective}。"
                    f"远程任务 ID：{task.remote_operation_id}。系统将自动继续轮询，"
                    "不会重复创建生成任务。"
                ), {
                    "kind": "task_retry",
                    "severity": "warning",
                    "task_id": task.id,
                    "attempt": task.attempt_count,
                    "remote_operation_id": task.remote_operation_id,
                    "action": "resume_provider_polling",
                })
                return
            if diagnosis.retryable and task.attempt_count < self.task_max_attempts:
                delay = min(60.0, max(
                    15.0 if diagnosis.category == "insufficient_credits" else 0.0,
                    self.retry_base_delay_seconds * (2 ** max(0, task.attempt_count - 1)),
                ))
                task.status = TaskStatus.PROPOSED
                task.error = diagnosis.detail
                task.failure_category = diagnosis.category
                task.action_required = None
                task.execution_phase = "retry_scheduled"
                task.heartbeat_at = now()
                attempt.status = AttemptStatus.FAILED
                attempt.error = diagnosis.detail
                attempt.finished_at = now()
                invocation.status = "retry_scheduled"
                invocation.result = {
                    "error": diagnosis.detail,
                    "category": diagnosis.category,
                    "retryable": True,
                    "retry_delay_seconds": delay,
                }
                await self.repo.save_task(task)
                await self.repo.save_attempt(attempt)
                await self.repo.save_tool_invocation(invocation)
                run = await self.repo.get_run(task.run_id)
                if run:
                    run.status = RunStatus.RUNNING
                    await self.repo.save_run(run)
                message = (
                    f"⚠️ 任务出现暂时异常：{task.objective}\n\n"
                    f"- 类型：{diagnosis.category}\n"
                    f"- 详情：{diagnosis.detail}\n"
                    f"- 处理：系统将在 {delay:g} 秒后自动进行第 "
                    f"{task.attempt_count + 1}/{self.task_max_attempts} 次尝试\n"
                    f"- 如重试仍失败：{diagnosis.action_required}"
                )
                await self._append_message(task.run_id, "assistant", message, {
                    "kind": "task_retry",
                    "severity": "warning",
                    "task_id": task.id,
                    "category": diagnosis.category,
                    "attempt": task.attempt_count,
                    "next_attempt": task.attempt_count + 1,
                    "max_attempts": self.task_max_attempts,
                    "retry_delay_seconds": delay,
                })
                await self._emit(task.run_id, "task.retry_scheduled", {
                    "task_id": task.id,
                    "category": diagnosis.category,
                    "attempt": task.attempt_count,
                    "next_attempt": task.attempt_count + 1,
                    "max_attempts": self.task_max_attempts,
                    "retry_delay_seconds": delay,
                    "error": diagnosis.detail,
                })
                if delay > 0:
                    await asyncio.sleep(delay)
                return
            task.status, task.error = TaskStatus.FAILED, diagnosis.detail
            task.failure_category = diagnosis.category
            task.action_required = diagnosis.action_required
            task.execution_phase = "failed"
            task.heartbeat_at = now()
            task.finished_at = now()
            attempt.status, attempt.error, attempt.finished_at = (
                AttemptStatus.FAILED, diagnosis.detail, now()
            )
            invocation.status, invocation.result = "failed", {
                "error": diagnosis.detail,
                "category": diagnosis.category,
            }
            await self.repo.save_task(task)
            await self.repo.save_attempt(attempt)
            await self.repo.save_tool_invocation(invocation)
            await self._emit(task.run_id, "task.failed", {
                "task_id": task.id,
                "error": diagnosis.detail,
                "category": diagnosis.category,
                "retryable": diagnosis.retryable,
            })
            run = await self.repo.get_run(task.run_id)
            failure_message = (
                f"❌ 任务已停止：{task.objective}\n\n"
                f"- 类型：{diagnosis.category}\n"
                f"- 详情：{diagnosis.detail}\n"
                f"- 已尝试：{task.attempt_count}/{self.task_max_attempts} 次\n"
                f"- 需要处理：{diagnosis.action_required}"
            )
            if run:
                run.status = RunStatus.WAITING_INPUT
                run.last_response = failure_message
                await self.repo.save_run(run)
            await self._append_message(task.run_id, "assistant", failure_message, {
                "kind": "task_user_action_required",
                "severity": "error",
                "task_id": task.id,
                "category": diagnosis.category,
                "attempt": task.attempt_count,
                "max_attempts": self.task_max_attempts,
                "action_required": diagnosis.action_required,
            })
            await self._emit(task.run_id, "task.user_action_required", {
                "task_id": task.id,
                "category": diagnosis.category,
                "error": diagnosis.detail,
                "action_required": diagnosis.action_required,
            })
        finally:
            if progress_reporter is not None:
                progress_reporter.cancel()

    async def _complete_task(
        self, task: Task, attempt: TaskAttempt, data: dict,
    ) -> ArtifactVersion:
        run = await self.repo.get_run(task.run_id)
        capability = self.capabilities.get(task.capability_id)
        artifact = ArtifactVersion(
            project_id=run.project_id, type=capability.output_type,
            produced_by_task_id=task.id,
            title=data.get("title") or capability.output_type.title(),
            summary=data.get("summary") or data.get("content") or "",
            uri=data.get("uri") or data.get("url"),
            metadata=data.get("metadata") or data,
        )
        artifact = await self.repo.add_artifact(artifact, [
            ArtifactEdge(
                project_id=run.project_id, source_version_id=version_id,
                target_version_id=artifact.id, relation="used_as_input",
            ) for version_id in task.input_artifact_version_ids
        ])
        trace_agent_event(
            get_settings(),
            "capability.succeeded",
            run_id=task.run_id,
            task_id=task.id,
            capability_id=task.capability_id,
            artifact_id=artifact.id,
            artifact_type=artifact.type,
            title=artifact.title,
            summary=artifact.summary,
        )
        task.status, task.error = TaskStatus.SUCCEEDED, None
        task.failure_category = None
        task.action_required = None
        task.execution_phase = "succeeded"
        task.heartbeat_at = now()
        task.finished_at = now()
        attempt.status, attempt.finished_at = AttemptStatus.SUCCEEDED, now()
        await self.repo.save_task(task)
        await self.repo.save_attempt(attempt)
        run.status = RunStatus.RUNNING
        await self.repo.save_run(run)
        await self._emit(task.run_id, "artifact.created", {
            "task_id": task.id, "artifact": artifact.model_dump(mode="json"),
        })
        if capability.output_type == "action_suggestions":
            await self._emit(task.run_id, "dialog.action_suggestions.created", {
                "task_id": task.id,
                "suggestions": artifact.metadata.get("suggestions", []),
                "reply_type": artifact.metadata.get("reply_type", "choice"),
            })
        await self._emit(task.run_id, "task.succeeded", {"task_id": task.id})
        if self._reports_progress(task):
            await self._append_message(task.run_id, "assistant", (
                f"✅ 已完成：{task.objective}。产物：{artifact.title}。"
            ), {
                "kind": "task_succeeded",
                "task_id": task.id,
                "capability_id": task.capability_id,
                "artifact_id": artifact.id,
                "artifact_type": artifact.type,
                "attempt": task.attempt_count,
            })
        await self._schedule_ready(task.run_id)
        self._spawn(self._coordinate(
            task.run_id, f"Task {task.id} produced artifact {artifact.id}.",
        ))
        return artifact

    async def ingest_source_event(self, event: SourceEvent) -> bool:
        fingerprint = event.source_event_id or hashlib.sha256(
            f"{event.remote_run_id}:{event.event_type}:{event.content}:{event.payload}".encode()
        ).hexdigest()
        if not await self.repo.mark_source_event(fingerprint):
            return False
        found = await self.repo.find_task_by_remote_operation(event.remote_run_id)
        if not found:
            raise LookupError("remote operation is not mapped to a V2 task")
        _, task = found
        capability = self.capabilities.get(task.capability_id)
        if event.event_type in capability.terminal_events:
            content = generated_content(event.event_type, event.content, event.payload)
            title, uri = markdown_media(content)
            attempt = TaskAttempt(
                run_id=task.run_id, task_id=task.id, number=1,
                idempotency_key=f"{task.run_id}:{task.id}:result",
                remote_operation_id=event.remote_run_id,
                status=AttemptStatus.WAITING_EXTERNAL,
            )
            await self.repo.add_attempt(attempt)
            await self._complete_task(task, attempt, {
                "title": event.payload.get("title") or title,
                "summary": content,
                "uri": payload_media_uri(event.payload) or uri,
                "metadata": event.payload,
            })
        elif event.event_type.endswith("_progress") or event.event_type == "progress":
            await self._emit(task.run_id, "task.progress", {
                "task_id": task.id, **event.payload,
            })
        elif event.event_type in {"error", "failed", "cancelled", "skill_failed"}:
            task.status = (
                TaskStatus.CANCELLED if event.event_type == "cancelled" else TaskStatus.FAILED
            )
            task.error = event.content or f"Downstream execution {event.event_type}"
            task.execution_phase = task.status.value
            task.heartbeat_at = now()
            task.finished_at = now()
            await self.repo.save_task(task)
            await self._emit(task.run_id, f"task.{task.status.value}", {
                "task_id": task.id, "error": task.error,
            })
            self._spawn(self._coordinate(task.run_id, f"External task ended: {task.error}"))
        elif event.event_type == "interrupt":
            await self._handle_remote_interrupt(task, event)
        else:
            if event.remote_run_id and event.remote_run_id != task.remote_operation_id:
                task.remote_operation_id = event.remote_run_id
                await self.repo.save_task(task)
            await self._emit(task.run_id, "task.downstream_message", {
                "task_id": task.id, "event_type": event.event_type,
                "content": event.content, "payload": event.payload,
            })
        return True

    @staticmethod
    def _interrupt_user_facing(interrupt_data: Any, *, full_auto: bool) -> dict[str, Any]:
        data = interrupt_data if isinstance(interrupt_data, dict) else {}
        step = str(data.get("step") or data.get("stage") or "").strip()
        message = str(
            data.get("message_default")
            or data.get("message")
            or data.get("content")
            or ""
        ).strip()
        disable_auto = bool(data.get("disable_auto_resume"))
        failure = data.get("failure") if isinstance(data.get("failure"), dict) else None
        stage_labels = {
            "after_music": "音乐/配乐阶段已完成",
            "after_outline": "故事大纲已生成",
            "after_character": "角色设计已完成",
            "after_storyboard_detail": "分镜细节已完成",
            "after_keyframe_reflection": "关键帧修订已完成",
            "after_shots": "镜头视频已生成",
            "music": "音乐生成",
            "outline": "大纲生成",
            "character": "角色生成",
        }
        what = stage_labels.get(step, message or "下游视频流水线暂停")
        if failure:
            why = str(
                failure.get("user_message")
                or failure.get("message")
                or data.get("message_default")
                or "生成阶段失败，已暂停以免带病继续。"
            )
            need = "需要你查看失败原因并决定是否重试/调整后再继续。"
            will_auto = False
            seconds = 0
        elif disable_auto:
            why = message or "这一步需要你明确点头后才会继续。"
            need = "请点「继续生成」，系统才会进入下一步。"
            will_auto = False
            seconds = 0
        elif full_auto:
            smart = data.get("smart_clip") if isinstance(data.get("smart_clip"), dict) else None
            seconds = 60 if smart and smart.get("status") == "ready" else 15
            why = (
                message
                or "当前这一步已经做完，系统会稍停一下，方便你看看中间结果。"
            )
            need = (
                f"你已经同意开始生成。大约 {seconds} 秒后会自动继续；"
                "也可以马上点「继续生成」。"
            )
            will_auto = True
        else:
            why = message or "做完当前这一步后会先停一下，等你确认再继续。"
            need = "请点「继续生成」，系统才会进入下一步（例如分镜、画面或成片）。"
            will_auto = False
            seconds = 0
        response = (
            f"发生了什么：{what}\n"
            f"为什么中断：{why}\n"
            f"为什么需要确认：{need}"
        )
        return {
            "response": response,
            "what_happened": what,
            "why_interrupted": why,
            "why_confirm": need,
            "step": step or None,
            "will_auto_resume": will_auto,
            "auto_resume_seconds": seconds,
            "disable_auto_resume": disable_auto or bool(failure),
        }

    async def _handle_remote_interrupt(self, task: Task, event: SourceEvent) -> None:
        interrupt_data = event.payload.get("interrupt_data") or event.payload
        if not isinstance(interrupt_data, dict):
            interrupt_data = {"message_default": str(event.content or interrupt_data)}
        message_id = (
            interrupt_data.get("message_id")
            or event.payload.get("message_id")
        )
        try:
            task.remote_interrupt_msgid = int(message_id) if message_id is not None else None
        except (TypeError, ValueError):
            task.remote_interrupt_msgid = None
        if event.remote_run_id:
            task.remote_operation_id = event.remote_run_id
        task.status = TaskStatus.WAITING_EXTERNAL
        await self.repo.save_task(task)
        run = await self.repo.get_run(task.run_id)
        capability = self.capabilities.get(task.capability_id, require_enabled=False)
        full_auto = bool((run.user_option or {}).get("full_auto")) if run else False
        # Prefer explicit full_auto from interrupt payload / downstream run if present.
        if isinstance(interrupt_data.get("full_auto"), bool):
            full_auto = bool(interrupt_data.get("full_auto"))
        explained = self._interrupt_user_facing(interrupt_data, full_auto=full_auto)
        failure = (
            interrupt_data.get("failure")
            if isinstance(interrupt_data.get("failure"), dict)
            else None
        )
        provenance = {
            "skill_name": capability.skill_name,
            "skill_resource": "SKILL.md" if capability.skill_name else None,
            "capability_id": task.capability_id,
            "task_id": task.id,
            "stage": explained.get("step"),
        }
        can_resume = bool(
            run and task.remote_operation_id and task.remote_interrupt_msgid is not None
        )
        if not failure and can_resume:
            # Atomic capabilities never stop for legacy downstream stage gates.
            # Preserve an audit/display event, then resume immediately.
            bypass = {
                **explained,
                "why_confirm": (
                    "该中断不是已激活 Skill 要求的人工确认，平台已自动跳过。"
                ),
                "will_auto_resume": True,
                "auto_resume_seconds": 0,
                "disable_auto_resume": False,
            }
            bypass["response"] = (
                f"发生了什么：{bypass['what_happened']}\n"
                f"为什么中断：{bypass['why_interrupted']}\n"
                f"为什么需要确认：{bypass['why_confirm']}"
            )
            await self._emit(task.run_id, "task.downstream_message", {
                "task_id": task.id, "event_type": event.event_type,
                "content": event.content, "payload": event.payload,
            })
            await self._emit(task.run_id, "run.interruption", {
                **provenance,
                "interrupt_category": "routine_downstream_gate",
                "confirmation_required": False,
                "paused": False,
                "response": bypass["response"],
                "what_happened": bypass["what_happened"],
                "why_interrupted": bypass["why_interrupted"],
                "why_confirm": bypass["why_confirm"],
                "will_auto_resume": True,
                "auto_resume_seconds": 0,
                "interrupt_data": {**interrupt_data, **bypass},
            })
            await self.resume_interrupt(
                task.run_id,
                run.user_id,
                "",
                idempotency_key=f"auto-bypass:{task.id}:{task.remote_interrupt_msgid}",
            )
            return
        if not failure:
            explained = {
                **explained,
                "why_interrupted": (
                    "下游报告了中断，但没有可用的恢复标识，平台无法安全自动继续。"
                ),
                "why_confirm": "请检查任务状态；确认后系统将重新规划或重试。",
                "will_auto_resume": False,
                "auto_resume_seconds": 0,
                "disable_auto_resume": True,
            }
            explained["response"] = (
                f"发生了什么：{explained['what_happened']}\n"
                f"为什么中断：{explained['why_interrupted']}\n"
                f"为什么需要确认：{explained['why_confirm']}"
            )
        if run:
            run.status = RunStatus.WAITING_INPUT
            run.last_response = explained["response"]
            await self.repo.save_run(run)
        await self._emit(task.run_id, "task.downstream_message", {
            "task_id": task.id, "event_type": event.event_type,
            "content": event.content, "payload": event.payload,
        })
        await self._emit(task.run_id, "run.waiting_input", {
            "task_id": task.id,
            "remote_operation_id": task.remote_operation_id,
            "remote_thread_id": task.remote_thread_id,
            "interrupt_msgid": task.remote_interrupt_msgid,
            "interrupt_data": {
                **interrupt_data,
                **{k: explained[k] for k in (
                    "what_happened", "why_interrupted", "why_confirm",
                    "will_auto_resume", "auto_resume_seconds", "disable_auto_resume",
                )},
            },
            "response": explained["response"],
            "what_happened": explained["what_happened"],
            "why_interrupted": explained["why_interrupted"],
            "why_confirm": explained["why_confirm"],
            "confirmation_required": True,
            "will_auto_resume": explained["will_auto_resume"],
            "auto_resume_seconds": explained["auto_resume_seconds"],
            "interrupt_category": "failure" if failure else "blocked",
            **provenance,
        })

    async def resume_interrupt(
        self,
        run_id: str,
        user_id: str,
        response: str = "",
        *,
        idempotency_key: str | None = None,
        user_option: dict | None = None,
        input_files: list[InputFile] | None = None,
        thread_id: str | None = None,
        output_language: str | None = None,
    ) -> AgentRun:
        """Resume a VideoAgent interrupt via resume_data, not Deep Agent re-planning."""
        snapshot = await self.repo.snapshot(run_id)
        if not snapshot or snapshot.run.user_id != user_id:
            raise LookupError("run not found")
        waiting = next(
            (
                task for task in reversed(snapshot.tasks)
                if task.status == TaskStatus.WAITING_EXTERNAL
                and task.remote_operation_id
                and task.remote_interrupt_msgid is not None
            ),
            None,
        )
        if waiting is None or input_files:
            return await self.add_message(
                run_id, user_id, response or "继续",
                idempotency_key or f"resume:{run_id}:{uid()}",
                user_option=user_option,
                input_files=input_files,
                thread_id=thread_id,
                output_language=output_language,
            )

        from app.services.task_enqueue_service import prepare_resume_task
        from app.services.queue import create_task_queue
        from app.services.redis.connection import get_redis_stream_service

        resume_payload = {
            "run_id": waiting.remote_operation_id,
            "interrupt_msgid": waiting.remote_interrupt_msgid,
        }
        task_data = await prepare_resume_task(resume_payload, user_id)
        redis_service = await get_redis_stream_service()
        sqs_service = create_task_queue()
        await sqs_service.add_task_to_queue(task_data)
        thread_id = waiting.remote_thread_id or task_data["thread_id"]
        await redis_service.add_task_index(thread_id, task_data["run_id"])

        waiting.remote_operation_id = task_data["run_id"]
        waiting.remote_interrupt_msgid = None
        waiting.status = TaskStatus.WAITING_EXTERNAL
        waiting.error = None
        await self.repo.save_task(waiting)
        snapshot.run.status = RunStatus.WAITING_EXTERNAL
        await self.repo.save_run(snapshot.run)
        if response.strip():
            await self._append_message(run_id, "user", response.strip(), {
                "resume_interrupt": True,
                "remote_operation_id": task_data["run_id"],
            })
        await self._emit(run_id, "task.waiting_external", {
            "task_id": waiting.id,
            "remote_operation_id": waiting.remote_operation_id,
            "remote_thread_id": waiting.remote_thread_id,
            "resumed": True,
        })
        return snapshot.run

    async def select_artifact(self, run_id: str, user_id: str, version_id: str):
        snapshot = await self.repo.snapshot(run_id)
        if not snapshot or snapshot.run.user_id != user_id:
            raise LookupError("run not found")
        selection = await self.repo.select_artifact(snapshot.run.project_id, version_id)
        await self._emit(run_id, "artifact.selected", {
            "selection": selection.model_dump(mode="json"),
        })
        self._spawn(self._coordinate(run_id, f"User selected artifact {version_id}."))
        return selection

    async def extract_frame(
        self,
        run_id: str,
        user_id: str,
        *,
        timestamp: float | None = None,
        position: str = "timestamp",
        version_id: str | None = None,
        video_url: str | None = None,
        image_format: str = "jpeg",
        select: bool = True,
    ) -> ArtifactVersion:
        """User- or API-initiated still-frame extract → durable image artifact."""
        snapshot = await self.repo.snapshot(run_id)
        if not snapshot or snapshot.run.user_id != user_id:
            raise LookupError("run not found")

        source: ArtifactVersion | None = None
        resolved_url = (video_url or "").strip() or None
        if version_id:
            source = next(
                (item for item in snapshot.artifacts if item.id == version_id),
                None,
            )
            if not source:
                raise LookupError("run not found")
            resolved_url = (
                resolved_url
                or source.uri
                or (source.metadata or {}).get("result_url")
                or (source.metadata or {}).get("video_url")
                or (source.metadata or {}).get("url")
            )
            if isinstance(resolved_url, str):
                resolved_url = resolved_url.strip() or None
        if not resolved_url:
            for item in snapshot.run.input_files:
                if item.type == "video" and item.url:
                    resolved_url = item.url
                    break
        if not resolved_url:
            raise ValueError(
                "extract_frame requires version_id with a video URI, "
                "video_url, or a video upload on the run"
            )

        from app.chat.v2.host_gateway import HostGateway, HostGatewayError

        try:
            result = await HostGateway().media_extract_frame({
                "video_url": resolved_url,
                "timestamp": timestamp,
                "position": position,
                "format": image_format,
                "run_id": run_id,
            })
        except HostGatewayError as exc:
            raise ValueError(str(exc)) from exc

        edges: list[ArtifactEdge] = []
        artifact = ArtifactVersion(
            project_id=snapshot.run.project_id,
            type="image",
            produced_by_task_id="user:media.extract_frame",
            title=str(result.get("title") or "Extracted video frame"),
            summary=str(result.get("summary") or ""),
            uri=result.get("uri") or result.get("result_url"),
            metadata={
                **result,
                "capability_id": "media.extract_frame",
                "source_artifact_version_id": source.id if source else None,
                "source_video_url": resolved_url,
                "timestamp": result.get("timestamp", timestamp),
                "position": result.get("position", position),
            },
        )
        if source is not None:
            edges.append(ArtifactEdge(
                project_id=snapshot.run.project_id,
                source_version_id=source.id,
                target_version_id=artifact.id,
                relation="frame_from",
            ))
        artifact = await self.repo.add_artifact(artifact, edges)
        await self._emit(run_id, "artifact.created", {
            "task_id": None,
            "artifact": artifact.model_dump(mode="json"),
            "source": "user_extract_frame",
        })
        if select:
            selection = await self.repo.select_artifact(
                snapshot.run.project_id, artifact.id,
            )
            await self._emit(run_id, "artifact.selected", {
                "selection": selection.model_dump(mode="json"),
            })
        await self._append_message(
            run_id,
            "user",
            (
                f"Selected frame at {float(result.get('timestamp', timestamp or 0.0)):.2f}s "
                f"from video → image artifact {artifact.id}"
            ),
            {"extract_frame": True, "artifact_id": artifact.id},
        )
        self._spawn(self._coordinate(
            run_id,
            (
                f"User extracted frame at {float(result.get('timestamp', timestamp or 0.0)):.2f}s "
                f"as image artifact {artifact.id}."
            ),
        ))
        return artifact

    async def cancel(self, run_id: str, user_id: str) -> AgentRun:
        snapshot = await self.repo.snapshot(run_id)
        if not snapshot or snapshot.run.user_id != user_id:
            raise LookupError("run not found")
        for task in snapshot.tasks:
            if task.status not in {
                TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED,
            }:
                execution = self.execution_tasks.get(task.id)
                if execution and not execution.done():
                    execution.cancel()
                task.status = TaskStatus.CANCELLED
                await self.repo.save_task(task)
        snapshot.run.status = RunStatus.CANCELLED
        await self.repo.save_run(snapshot.run)
        await self._emit(run_id, "run.cancelled")
        return snapshot.run

    async def delete_run(self, run_id: str, user_id: str) -> dict[str, int]:
        """Permanently delete a terminal run and all database-owned children."""
        deleted = await self.repo.delete_run(run_id, user_id)
        if deleted is None:
            raise LookupError("run not found")
        return deleted

    async def resume_active_runs(self) -> int:
        """Recover durable provider work without repeating a paid submission.

        Submitted remote operations remain externally waiting and are picked up
        by the result reconciler. Tasks that did not persist a remote operation
        are returned to the local queue with their original task/idempotency
        identity. The coordinator is intentionally not called during recovery.
        """
        runs = await self.repo.list_active_runs()
        recovered = await self._recover_orphaned_running_tasks(runs)
        for run in runs:
            await self._schedule_ready(run.id)
        return recovered

    async def _recover_orphaned_running_tasks(
        self,
        runs: list[AgentRun] | None = None,
    ) -> int:
        """Repair tasks whose in-process executor disappeared.

        A task with a remote operation is recoverable by the result reconciler.
        A task without one was never durably submitted, so it is returned to the
        queue. In both cases an explicit durable event explains the transition.
        """
        recovered = 0
        active_runs = runs if runs is not None else await self.repo.list_active_runs()
        for run in active_runs:
            snapshot = await self.repo.snapshot(run.id)
            if not snapshot:
                continue
            for task in snapshot.tasks:
                if task.status != TaskStatus.RUNNING or task.id in self.execution_tasks:
                    continue
                previous_phase = task.execution_phase
                if task.remote_operation_id and task.execution_phase == "provider_polling":
                    task.status = TaskStatus.PROPOSED
                    task.execution_phase = "queued_after_recovery"
                    recovery_action = "resume_provider_polling"
                elif task.remote_operation_id:
                    task.status = TaskStatus.WAITING_EXTERNAL
                    task.execution_phase = "waiting_external"
                    recovery_action = "resume_remote_tracking"
                else:
                    task.status = TaskStatus.PROPOSED
                    task.execution_phase = "queued_after_recovery"
                    recovery_action = "requeue_submission"
                task.heartbeat_at = now()
                task.error = None
                task.failure_category = None
                task.action_required = None
                await self.repo.save_task(task)
                await self._emit(run.id, "task.recovered", {
                    "task_id": task.id,
                    "previous_status": TaskStatus.RUNNING.value,
                    "previous_phase": previous_phase,
                    "status": task.status.value,
                    "phase": task.execution_phase,
                    "remote_operation_id": task.remote_operation_id,
                    "action": recovery_action,
                })
                await self._append_message(run.id, "assistant", (
                    f"⚠️ 检测到服务重启时任务仍未结束：{task.objective}。"
                    f"系统已执行恢复动作 {recovery_action}，"
                    + (
                        f"并将继续跟踪远程任务 {task.remote_operation_id}。"
                        if task.remote_operation_id
                        else "将使用同一幂等提交标识自动重新执行。"
                    )
                ), {
                    "kind": "task_recovered",
                    "severity": "warning",
                    "task_id": task.id,
                    "previous_phase": previous_phase,
                    "phase": task.execution_phase,
                    "action": recovery_action,
                    "remote_operation_id": task.remote_operation_id,
                })
                recovered += 1
        return recovered

    @staticmethod
    def _format_coordination_error(exc: BaseException) -> str:
        name = type(exc).__name__
        detail = str(exc).strip() or name
        lowered = f"{name} {detail}".lower()
        if any(
            token in lowered
            for token in (
                "connection error",
                "connecttimeout",
                "connect error",
                "timed out",
                "timeout",
                "proxy",
                "apiconnectionerror",
                "apitimeouterror",
            )
        ):
            return (
                "Coordinator LLM unreachable (OpenAI connection/proxy failure). "
                "Check that Clash/VPN is running with remaining traffic and that "
                f"HTTP(S)_PROXY reaches a working mixed-port. Details: {detail}"
            )
        return f"Coordination failed ({name}): {detail}"

    async def _emit(
        self, run_id: str, event_type: str, payload: dict | None = None,
    ) -> DomainEvent:
        return await self.repo.append_event(DomainEvent(
            run_id=run_id, type=event_type, payload=payload or {},
        ))
