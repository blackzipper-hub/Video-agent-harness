from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from typing import Protocol

from .models import (
    AgentRun, ArtifactEdge, ArtifactSelection, ArtifactVersion, ChatMessage,
    DomainEvent, PlanPatch, PlanRevision, RunSnapshot, RunStatus, Task,
    TaskAttempt, TaskStatus, ToolInvocation, UserCommand, now,
)

ACTIVE_RUN_STATUSES = {
    RunStatus.PLANNING, RunStatus.RUNNING, RunStatus.WAITING_EXTERNAL,
    RunStatus.WAITING_INPUT,
}
EXECUTING_RUN_STATUSES = {
    RunStatus.PLANNING, RunStatus.RUNNING, RunStatus.WAITING_EXTERNAL,
}
NONTERMINAL_TASK_STATUSES = {
    TaskStatus.PROPOSED,
    TaskStatus.BLOCKED,
    TaskStatus.READY,
    TaskStatus.RUNNING,
    TaskStatus.WAITING_EXTERNAL,
}


class ActiveRunLimitError(RuntimeError):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(
            f"The user already has the maximum number of active projects ({limit}). "
            "Finish, cancel, or wait for an existing project before starting another."
        )


class ActiveRunDeletionError(RuntimeError):
    def __init__(self):
        super().__init__("Active projects must be cancelled before they can be deleted.")


class V2Repository(Protocol):
    async def create_run(self, run: AgentRun, max_active_runs: int = 0) -> tuple[AgentRun, bool]: ...
    async def activate_run(self, run: AgentRun, max_active_runs: int = 0) -> AgentRun: ...
    async def fail_active_runs(self, reason: str) -> int: ...
    async def delete_run(self, run_id: str, user_id: str) -> dict[str, int] | None: ...
    async def get_run(self, run_id: str) -> AgentRun | None: ...
    async def get_run_by_thread(self, user_id: str, thread_id: str) -> AgentRun | None: ...
    async def list_runs(self, user_id: str) -> list[AgentRun]: ...
    async def save_run(self, run: AgentRun) -> None: ...
    async def snapshot(self, run_id: str) -> RunSnapshot | None: ...
    async def apply_patch(self, run: AgentRun, patch: PlanPatch) -> list[Task]: ...
    async def list_ready_tasks(self, run_id: str) -> list[Task]: ...
    async def claim_task(self, task_id: str) -> Task | None: ...
    async def save_task(self, task: Task) -> None: ...
    async def add_attempt(self, attempt: TaskAttempt) -> None: ...
    async def save_attempt(self, attempt: TaskAttempt) -> None: ...
    async def add_artifact(self, artifact: ArtifactVersion, edges: list[ArtifactEdge]) -> ArtifactVersion: ...
    async def select_artifact(self, project_id: str, version_id: str) -> ArtifactSelection: ...
    async def add_tool_invocation(self, invocation: ToolInvocation) -> None: ...
    async def save_tool_invocation(self, invocation: ToolInvocation) -> None: ...
    async def find_task_by_remote_operation(self, operation_id: str) -> tuple[AgentRun, Task] | None: ...
    async def list_active_runs(self) -> list[AgentRun]: ...
    async def list_waiting_tasks(self) -> list[Task]: ...
    async def append_event(self, event: DomainEvent) -> DomainEvent: ...
    async def get_events(self, run_id: str, after: int = 0) -> list[DomainEvent]: ...
    async def wait_for_events(self, run_id: str, timeout: float = 10.0) -> None: ...
    async def mark_source_event(self, key: str) -> bool: ...
    async def add_command(self, command: UserCommand) -> bool: ...
    async def add_chat_message(self, message: ChatMessage) -> ChatMessage: ...
    async def list_chat_messages(self, run_id: str) -> list[ChatMessage]: ...


class InMemoryV2Repository:
    def __init__(self) -> None:
        self.runs: dict[str, AgentRun] = {}
        self.revisions: dict[str, list[PlanRevision]] = defaultdict(list)
        self.tasks: dict[str, list[Task]] = defaultdict(list)
        self.attempts: dict[str, list[TaskAttempt]] = defaultdict(list)
        self.artifacts: dict[str, list[ArtifactVersion]] = defaultdict(list)
        self.edges: dict[str, list[ArtifactEdge]] = defaultdict(list)
        self.selections: dict[tuple[str, str], ArtifactSelection] = {}
        self.invocations: dict[str, ToolInvocation] = {}
        self.events: dict[str, list[DomainEvent]] = defaultdict(list)
        self.messages: dict[str, list[ChatMessage]] = defaultdict(list)
        self.idempotency: dict[tuple[str, str], str] = {}
        self.source_events: set[str] = set()
        self.commands: set[tuple[str, str]] = set()
        self.changed: dict[str, asyncio.Condition] = defaultdict(asyncio.Condition)
        self.lock = asyncio.Lock()

    async def create_run(self, run: AgentRun, max_active_runs: int = 0) -> tuple[AgentRun, bool]:
        async with self.lock:
            key = (run.user_id, run.idempotency_key)
            existing = self.idempotency.get(key)
            if existing:
                return deepcopy(self.runs[existing]), False
            active = sum(
                item.user_id == run.user_id and item.status in EXECUTING_RUN_STATUSES
                for item in self.runs.values()
            )
            if max_active_runs > 0 and active >= max_active_runs:
                raise ActiveRunLimitError(max_active_runs)
            self.runs[run.id] = deepcopy(run)
            self.idempotency[key] = run.id
            return deepcopy(run), True

    async def activate_run(self, run: AgentRun, max_active_runs: int = 0) -> AgentRun:
        async with self.lock:
            stored = self.runs[run.id]
            if stored.status not in EXECUTING_RUN_STATUSES:
                active = sum(
                    item.id != run.id
                    and item.user_id == run.user_id
                    and item.status in EXECUTING_RUN_STATUSES
                    for item in self.runs.values()
                )
                if max_active_runs > 0 and active >= max_active_runs:
                    raise ActiveRunLimitError(max_active_runs)
            run.status = RunStatus.PLANNING
            run.updated_at = now()
            self.runs[run.id] = deepcopy(run)
            return deepcopy(run)

    async def fail_active_runs(self, reason: str) -> int:
        async with self.lock:
            affected = 0
            for run in self.runs.values():
                has_unfinished = any(
                    task.status in NONTERMINAL_TASK_STATUSES
                    for task in self.tasks.get(run.id, [])
                )
                if run.status not in ACTIVE_RUN_STATUSES and not has_unfinished:
                    continue
                affected += 1
                run.status = RunStatus.FAILED
                run.last_response = reason
                run.updated_at = now()
                for task in self.tasks.get(run.id, []):
                    if task.status in NONTERMINAL_TASK_STATUSES:
                        task.status = TaskStatus.FAILED
                        task.error = reason
                        task.failure_category = "service_interrupted"
                        task.action_required = "Restart this project explicitly if it should continue."
                        task.execution_phase = "failed_on_service_interrupt"
                        task.finished_at = now()
                        task.updated_at = now()
            return affected

    async def delete_run(self, run_id: str, user_id: str) -> dict[str, int] | None:
        async with self.lock:
            run = self.runs.get(run_id)
            if not run or run.user_id != user_id:
                return None
            if run.status in EXECUTING_RUN_STATUSES:
                raise ActiveRunDeletionError()
            tasks = self.tasks.pop(run_id, [])
            task_ids = {task.id for task in tasks}
            attempts = sum(len(self.attempts.pop(task_id, [])) for task_id in task_ids)
            artifacts = self.artifacts.pop(run.project_id, [])
            edges = len(self.edges.pop(run.project_id, []))
            selections = [key for key in self.selections if key[0] == run.project_id]
            for key in selections:
                self.selections.pop(key, None)
            invocations = [key for key, value in self.invocations.items() if value.run_id == run_id]
            for key in invocations:
                self.invocations.pop(key, None)
            event_count = len(self.events.pop(run_id, []))
            message_count = len(self.messages.pop(run_id, []))
            revision_count = len(self.revisions.pop(run_id, []))
            self.commands = {key for key in self.commands if key[0] != run_id}
            self.idempotency.pop((run.user_id, run.idempotency_key), None)
            self.runs.pop(run_id, None)
            return {
                "runs": 1,
                "tasks": len(tasks),
                "attempts": attempts,
                "artifacts": len(artifacts),
                "artifact_edges": edges,
                "artifact_selections": len(selections),
                "tool_invocations": len(invocations),
                "events": event_count,
                "messages": message_count,
                "plan_revisions": revision_count,
                "legacy_records": 0,
            }

    async def get_run(self, run_id: str) -> AgentRun | None:
        run = self.runs.get(run_id)
        return deepcopy(run) if run else None

    async def get_run_by_thread(self, user_id: str, thread_id: str) -> AgentRun | None:
        candidates = [
            run for run in self.runs.values()
            if run.user_id == user_id and run.thread_id == thread_id
        ]
        return deepcopy(max(candidates, key=lambda item: item.updated_at)) if candidates else None

    async def list_runs(self, user_id: str) -> list[AgentRun]:
        return deepcopy(sorted(
            (run for run in self.runs.values() if run.user_id == user_id),
            key=lambda item: item.updated_at, reverse=True,
        ))

    async def save_run(self, run: AgentRun) -> None:
        run.updated_at = now()
        self.runs[run.id] = deepcopy(run)

    async def snapshot(self, run_id: str) -> RunSnapshot | None:
        run = self.runs.get(run_id)
        if not run:
            return None
        return RunSnapshot(
            run=deepcopy(run), revisions=deepcopy(self.revisions[run_id]),
            tasks=deepcopy(self.tasks[run_id]), artifacts=deepcopy(self.artifacts[run.project_id]),
            selections=deepcopy([
                value for (project_id, _), value in self.selections.items()
                if project_id == run.project_id
            ]), messages=deepcopy(self.messages[run_id]),
            last_event_sequence=max(
                (event.sequence for event in self.events[run_id]),
                default=0,
            ),
        )

    async def apply_patch(self, run: AgentRun, patch: PlanPatch) -> list[Task]:
        async with self.lock:
            stored = self.runs[run.id]
            if stored.current_revision != patch.base_revision:
                raise ValueError("plan revision changed before commit")
            number = stored.current_revision + 1
            revision = PlanRevision(
                run_id=run.id, number=number, parent_number=stored.current_revision,
                reason=patch.reason, patch=patch,
            )
            keys = {
                item.client_key: f"{run.id}:{item.client_key}"
                for item in patch.add_tasks
            }
            existing = {task.client_key: task.id for task in self.tasks[run.id]}
            dependency_ids = {**existing, **keys}
            created = [
                Task(
                    id=keys[item.client_key], run_id=run.id, revision=number,
                    client_key=item.client_key, capability_id=item.capability_id,
                    objective=item.objective,
                    input_artifact_version_ids=item.input_artifact_version_ids,
                    parameters=item.parameters,
                    depends_on=[dependency_ids.get(value, value) for value in item.depends_on],
                    resolved_skills=item.resolved_skills,
                    skill_context=item.skill_context,
                ) for item in patch.add_tasks
            ]
            for task in self.tasks[run.id]:
                if task.id in patch.cancel_task_ids or task.client_key in patch.cancel_task_ids:
                    task.status = TaskStatus.CANCELLED
                    task.updated_at = now()
            self.tasks[run.id].extend(deepcopy(created))
            self.revisions[run.id].append(revision)
            stored.current_revision = number
            stored.last_response = patch.response
            has_unfinished_tasks = any(
                task.status in NONTERMINAL_TASK_STATUSES
                for task in self.tasks[run.id]
            )
            stored.status = (
                RunStatus.COMPLETED if patch.goal_satisfied and not has_unfinished_tasks else
                RunStatus.WAITING_INPUT if patch.waiting_for_input else RunStatus.RUNNING
            )
            stored.updated_at = now()
            return deepcopy(created)

    async def list_ready_tasks(self, run_id: str) -> list[Task]:
        tasks = self.tasks.get(run_id, [])
        succeeded = {item.id for item in tasks if item.status == TaskStatus.SUCCEEDED}
        return deepcopy([
            item for item in tasks
            if item.status in {TaskStatus.PROPOSED, TaskStatus.BLOCKED}
            and set(item.depends_on) <= succeeded
        ])

    async def claim_task(self, task_id: str) -> Task | None:
        async with self.lock:
            for items in self.tasks.values():
                for index, stored in enumerate(items):
                    if stored.id != task_id:
                        continue
                    if stored.status not in {TaskStatus.PROPOSED, TaskStatus.BLOCKED}:
                        return None
                    claimed = deepcopy(stored)
                    claimed.status = TaskStatus.RUNNING
                    claimed.execution_phase = "claimed"
                    claimed.attempt_count += 1
                    claimed.started_at = claimed.started_at or now()
                    claimed.heartbeat_at = now()
                    claimed.finished_at = None
                    claimed.error = None
                    claimed.failure_category = None
                    claimed.action_required = None
                    claimed.updated_at = now()
                    items[index] = deepcopy(claimed)
                    parent = self.runs[claimed.run_id]
                    parent.status = RunStatus.RUNNING
                    parent.updated_at = now()
                    return claimed
        return None

    async def save_task(self, task: Task) -> None:
        task.updated_at = now()
        items = self.tasks[task.run_id]
        items[next(i for i, item in enumerate(items) if item.id == task.id)] = deepcopy(task)

    async def add_attempt(self, attempt: TaskAttempt) -> None:
        self.attempts[attempt.task_id].append(deepcopy(attempt))

    async def save_attempt(self, attempt: TaskAttempt) -> None:
        items = self.attempts[attempt.task_id]
        index = next((i for i, item in enumerate(items) if item.id == attempt.id), None)
        if index is None:
            items.append(deepcopy(attempt))
        else:
            items[index] = deepcopy(attempt)

    async def add_artifact(self, artifact: ArtifactVersion, edges: list[ArtifactEdge]) -> ArtifactVersion:
        versions = [
            item.version for item in self.artifacts[artifact.project_id]
            if item.artifact_id == artifact.artifact_id
        ]
        artifact.version = max(versions, default=0) + 1
        self.artifacts[artifact.project_id].append(deepcopy(artifact))
        self.edges[artifact.project_id].extend(deepcopy(edges))
        self.selections[(artifact.project_id, artifact.type)] = ArtifactSelection(
            project_id=artifact.project_id, type=artifact.type,
            artifact_version_id=artifact.id,
        )
        return deepcopy(artifact)

    async def select_artifact(self, project_id: str, version_id: str) -> ArtifactSelection:
        artifact = next(
            (item for item in self.artifacts[project_id] if item.id == version_id), None
        )
        if not artifact:
            raise LookupError("artifact version not found")
        selection = ArtifactSelection(
            project_id=project_id, type=artifact.type, artifact_version_id=version_id,
        )
        self.selections[(project_id, artifact.type)] = selection
        return deepcopy(selection)

    async def add_tool_invocation(self, invocation: ToolInvocation) -> None:
        if not any(
            item.idempotency_key == invocation.idempotency_key
            for item in self.invocations.values()
        ):
            self.invocations[invocation.id] = deepcopy(invocation)

    async def save_tool_invocation(self, invocation: ToolInvocation) -> None:
        invocation.updated_at = now()
        self.invocations[invocation.id] = deepcopy(invocation)

    async def find_task_by_remote_operation(self, operation_id: str) -> tuple[AgentRun, Task] | None:
        for run_id, tasks in self.tasks.items():
            for task in tasks:
                if task.remote_operation_id == operation_id:
                    return deepcopy(self.runs[run_id]), deepcopy(task)
        return None

    async def list_active_runs(self) -> list[AgentRun]:
        return deepcopy([
            run for run in self.runs.values()
            if run.status in ACTIVE_RUN_STATUSES
            or any(
                task.status in NONTERMINAL_TASK_STATUSES
                for task in self.tasks.get(run.id, [])
            )
        ])

    async def list_waiting_tasks(self) -> list[Task]:
        return deepcopy([
            task for tasks in self.tasks.values() for task in tasks
            if task.status == TaskStatus.WAITING_EXTERNAL and task.remote_operation_id
        ])

    async def append_event(self, event: DomainEvent) -> DomainEvent:
        event.sequence = len(self.events[event.run_id]) + 1
        self.events[event.run_id].append(deepcopy(event))
        async with self.changed[event.run_id]:
            self.changed[event.run_id].notify_all()
        return deepcopy(event)

    async def get_events(self, run_id: str, after: int = 0) -> list[DomainEvent]:
        return deepcopy([item for item in self.events.get(run_id, []) if item.sequence > after])

    async def wait_for_events(self, run_id: str, timeout: float = 10.0) -> None:
        try:
            async with self.changed[run_id]:
                await asyncio.wait_for(self.changed[run_id].wait(), timeout)
        except TimeoutError:
            return

    async def mark_source_event(self, key: str) -> bool:
        if key in self.source_events:
            return False
        self.source_events.add(key)
        return True

    async def add_command(self, command: UserCommand) -> bool:
        key = (command.run_id, command.idempotency_key)
        if key in self.commands:
            return False
        self.commands.add(key)
        return True

    async def add_chat_message(self, message: ChatMessage) -> ChatMessage:
        message.sequence = len(self.messages[message.run_id]) + 1
        self.messages[message.run_id].append(deepcopy(message))
        return deepcopy(message)

    async def list_chat_messages(self, run_id: str) -> list[ChatMessage]:
        return deepcopy(self.messages.get(run_id, []))
