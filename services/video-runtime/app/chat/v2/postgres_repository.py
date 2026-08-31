from __future__ import annotations

import asyncio
import json
import re

import asyncpg

from .models import (
    AgentRun, ArtifactEdge, ArtifactSelection, ArtifactVersion, ChatMessage,
    DomainEvent, PlanPatch, PlanRevision, RunSnapshot, RunStatus, Task, TaskAttempt,
    TaskStatus, ToolInvocation, UserCommand, now,
)
from .repository import (
    ACTIVE_RUN_STATUSES,
    EXECUTING_RUN_STATUSES,
    NONTERMINAL_TASK_STATUSES,
    ActiveRunDeletionError,
    ActiveRunLimitError,
)


def _json(model) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False)


def _load(model, value):
    return model.model_validate(json.loads(value) if isinstance(value, str) else value)


class PostgresV2Repository:
    """JSON-backed V2 store. Schema creation is explicit; startup never mutates DDL."""

    def __init__(self, pool: asyncpg.Pool, schema: str):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
            raise ValueError("invalid V2 database schema")
        self.pool = pool
        self.schema = schema

    @classmethod
    async def connect(cls, database_url: str, schema: str) -> "PostgresV2Repository":
        return cls(await asyncpg.create_pool(database_url, min_size=1, max_size=10), schema)

    async def close(self) -> None:
        await self.pool.close()

    async def create_run(self, run: AgentRun, max_active_runs: int = 0) -> tuple[AgentRun, bool]:
        async with self.pool.acquire() as connection, connection.transaction():
            # Serialize slot allocation for one user across Studio/Agent processes.
            await connection.execute("SELECT pg_advisory_xact_lock(hashtext($1))", run.user_id)
            value = await connection.fetchval(
                f"SELECT payload FROM {self.schema}.agent_runs WHERE user_id=$1 AND idempotency_key=$2",
                run.user_id, run.idempotency_key,
            )
            if value:
                return _load(AgentRun, value), False
            if max_active_runs > 0:
                active = await connection.fetchval(
                    f"SELECT count(*) FROM {self.schema}.agent_runs WHERE user_id=$1 AND status=ANY($2::text[])",
                    run.user_id, [item.value for item in EXECUTING_RUN_STATUSES],
                )
                if active >= max_active_runs:
                    raise ActiveRunLimitError(max_active_runs)
            await connection.execute(
                f"""INSERT INTO {self.schema}.agent_runs
                (id,user_id,project_id,thread_id,idempotency_key,status,current_revision,updated_at,payload)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)""",
                run.id, run.user_id, run.project_id, run.thread_id, run.idempotency_key,
                run.status.value, run.current_revision, run.updated_at, _json(run),
            )
            return run, True

    async def activate_run(self, run: AgentRun, max_active_runs: int = 0) -> AgentRun:
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(hashtext($1))", run.user_id)
            current_status = await connection.fetchval(
                f"SELECT status FROM {self.schema}.agent_runs WHERE id=$1 FOR UPDATE", run.id,
            )
            if current_status is None:
                raise LookupError("run not found")
            if max_active_runs > 0 and current_status not in {
                item.value for item in EXECUTING_RUN_STATUSES
            }:
                active = await connection.fetchval(
                    f"""SELECT count(*) FROM {self.schema}.agent_runs
                    WHERE user_id=$1 AND id<>$2 AND status=ANY($3::text[])""",
                    run.user_id, run.id, [item.value for item in EXECUTING_RUN_STATUSES],
                )
                if active >= max_active_runs:
                    raise ActiveRunLimitError(max_active_runs)
            run.status = RunStatus.PLANNING
            run.updated_at = now()
            await connection.execute(
                f"""UPDATE {self.schema}.agent_runs SET status=$1,current_revision=$2,
                updated_at=$3,payload=$4::jsonb WHERE id=$5""",
                run.status.value, run.current_revision, run.updated_at, _json(run), run.id,
            )
            return run

    async def fail_active_runs(self, reason: str) -> int:
        async with self.pool.acquire() as connection, connection.transaction():
            rows = await connection.fetch(
                f"""SELECT runs.id,runs.payload FROM {self.schema}.agent_runs AS runs
                WHERE runs.status=ANY($1::text[]) OR EXISTS (
                    SELECT 1 FROM {self.schema}.tasks AS tasks
                    WHERE tasks.run_id=runs.id AND tasks.status=ANY($2::text[])
                ) FOR UPDATE OF runs""",
                [item.value for item in ACTIVE_RUN_STATUSES],
                [item.value for item in NONTERMINAL_TASK_STATUSES],
            )
            for row in rows:
                run = _load(AgentRun, row["payload"])
                run.status = RunStatus.FAILED
                run.last_response = reason
                run.updated_at = now()
                await connection.execute(
                    f"""UPDATE {self.schema}.agent_runs SET status=$1,updated_at=$2,payload=$3::jsonb
                    WHERE id=$4""",
                    run.status.value, run.updated_at, _json(run), run.id,
                )
                task_rows = await connection.fetch(
                    f"SELECT payload FROM {self.schema}.tasks WHERE run_id=$1 AND status=ANY($2::text[]) FOR UPDATE",
                    run.id, [item.value for item in NONTERMINAL_TASK_STATUSES],
                )
                for task_row in task_rows:
                    task = _load(Task, task_row["payload"])
                    task.status = TaskStatus.FAILED
                    task.error = reason
                    task.failure_category = "service_interrupted"
                    task.action_required = "Restart this project explicitly if it should continue."
                    task.execution_phase = "failed_on_service_interrupt"
                    task.finished_at = now()
                    task.updated_at = now()
                    await connection.execute(
                        f"UPDATE {self.schema}.tasks SET status=$1,payload=$2::jsonb WHERE id=$3",
                        task.status.value, _json(task), task.id,
                    )
            return len(rows)

    async def delete_run(self, run_id: str, user_id: str) -> dict[str, int] | None:
        async with self.pool.acquire() as connection, connection.transaction():
            value = await connection.fetchval(
                f"SELECT payload FROM {self.schema}.agent_runs WHERE id=$1 FOR UPDATE", run_id,
            )
            if not value:
                return None
            run = _load(AgentRun, value)
            if run.user_id != user_id:
                return None
            if run.status in EXECUTING_RUN_STATUSES:
                raise ActiveRunDeletionError()

            task_rows = await connection.fetch(
                f"SELECT id,remote_operation_id FROM {self.schema}.tasks WHERE run_id=$1", run_id,
            )
            task_ids = [row["id"] for row in task_rows]
            remote_ids = [row["remote_operation_id"] for row in task_rows if row["remote_operation_id"]]

            counts: dict[str, int] = {}

            async def delete(label: str, sql: str, *args) -> None:
                result = await connection.execute(sql, *args)
                counts[label] = int(result.rsplit(" ", 1)[-1])

            if task_ids:
                await delete(
                    "attempts",
                    f"DELETE FROM {self.schema}.task_attempts WHERE task_id=ANY($1::text[])",
                    task_ids,
                )
            else:
                counts["attempts"] = 0
            await delete("tool_invocations", f"DELETE FROM {self.schema}.tool_invocations WHERE run_id=$1", run_id)
            await delete("events", f"DELETE FROM {self.schema}.events WHERE run_id=$1", run_id)
            await delete("messages", f"DELETE FROM {self.schema}.chat_messages WHERE run_id=$1", run_id)
            await delete("commands", f"DELETE FROM {self.schema}.commands WHERE run_id=$1", run_id)
            await delete("plan_revisions", f"DELETE FROM {self.schema}.plan_revisions WHERE run_id=$1", run_id)
            await delete("tasks", f"DELETE FROM {self.schema}.tasks WHERE run_id=$1", run_id)
            await delete("artifact_edges", f"DELETE FROM {self.schema}.artifact_edges WHERE project_id=$1", run.project_id)
            await delete("artifact_selections", f"DELETE FROM {self.schema}.artifact_selections WHERE project_id=$1", run.project_id)
            await delete("artifacts", f"DELETE FROM {self.schema}.artifact_versions WHERE project_id=$1", run.project_id)

            legacy_records = 0
            if remote_ids:
                legacy_tables = await connection.fetch(
                    """SELECT table_name FROM information_schema.columns
                    WHERE table_schema='public' AND column_name='run_id'
                    AND (table_name LIKE 'video_%' OR table_name='conversation_runs')"""
                )
                for table_row in legacy_tables:
                    table = table_row["table_name"]
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                        continue
                    result = await connection.execute(
                        f'DELETE FROM public."{table}" WHERE run_id::text=ANY($1::text[])',
                        remote_ids,
                    )
                    legacy_records += int(result.rsplit(" ", 1)[-1])
            counts["legacy_records"] = legacy_records
            await delete("runs", f"DELETE FROM {self.schema}.agent_runs WHERE id=$1", run_id)
            return counts

    async def get_run(self, run_id: str) -> AgentRun | None:
        value = await self.pool.fetchval(
            f"SELECT payload FROM {self.schema}.agent_runs WHERE id=$1", run_id
        )
        return _load(AgentRun, value) if value else None

    async def get_run_by_thread(self, user_id: str, thread_id: str) -> AgentRun | None:
        value = await self.pool.fetchval(
            f"""SELECT payload FROM {self.schema}.agent_runs
            WHERE user_id=$1 AND thread_id=$2 ORDER BY updated_at DESC LIMIT 1""",
            user_id, thread_id,
        )
        return _load(AgentRun, value) if value else None

    async def list_runs(self, user_id: str) -> list[AgentRun]:
        rows = await self.pool.fetch(
            f"SELECT payload FROM {self.schema}.agent_runs WHERE user_id=$1 ORDER BY updated_at DESC",
            user_id,
        )
        return [_load(AgentRun, row["payload"]) for row in rows]

    async def save_run(self, run: AgentRun) -> None:
        run.updated_at = now()
        await self.pool.execute(
            f"""UPDATE {self.schema}.agent_runs SET status=$1,current_revision=$2,
            updated_at=$3,payload=$4::jsonb WHERE id=$5""",
            run.status.value, run.current_revision, run.updated_at, _json(run), run.id,
        )

    async def snapshot(self, run_id: str) -> RunSnapshot | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                run_value = await connection.fetchval(
                    f"SELECT payload FROM {self.schema}.agent_runs WHERE id=$1", run_id
                )
                if not run_value:
                    return None
                run = _load(AgentRun, run_value)
                revisions = await connection.fetch(
                    f"SELECT payload FROM {self.schema}.plan_revisions WHERE run_id=$1 ORDER BY number", run_id
                )
                tasks = await connection.fetch(
                    f"SELECT payload FROM {self.schema}.tasks WHERE run_id=$1 ORDER BY created_at,id", run_id
                )
                artifacts = await connection.fetch(
                    f"SELECT payload FROM {self.schema}.artifact_versions WHERE project_id=$1 ORDER BY created_at,id", run.project_id
                )
                selections = await connection.fetch(
                    f"SELECT payload FROM {self.schema}.artifact_selections WHERE project_id=$1", run.project_id
                )
                messages = await connection.fetch(
                    f"SELECT payload FROM {self.schema}.chat_messages WHERE run_id=$1 ORDER BY sequence,id", run_id
                )
                last_event_sequence = await connection.fetchval(
                    f"SELECT COALESCE(MAX(sequence), 0) FROM {self.schema}.events WHERE run_id=$1", run_id
                )
        return RunSnapshot(
            run=run,
            revisions=[_load(PlanRevision, row["payload"]) for row in revisions],
            tasks=[_load(Task, row["payload"]) for row in tasks],
            artifacts=[_load(ArtifactVersion, row["payload"]) for row in artifacts],
            selections=[_load(ArtifactSelection, row["payload"]) for row in selections],
            messages=[_load(ChatMessage, row["payload"]) for row in messages],
            last_event_sequence=last_event_sequence or 0,
        )

    async def apply_patch(self, run: AgentRun, patch: PlanPatch) -> list[Task]:
        async with self.pool.acquire() as connection, connection.transaction():
            value = await connection.fetchval(
                f"SELECT payload FROM {self.schema}.agent_runs WHERE id=$1 FOR UPDATE", run.id
            )
            stored = _load(AgentRun, value)
            if stored.current_revision != patch.base_revision:
                raise ValueError("plan revision changed before commit")
            number = stored.current_revision + 1
            revision = PlanRevision(
                run_id=run.id, number=number, parent_number=stored.current_revision,
                reason=patch.reason, patch=patch,
            )
            # client_key is only unique inside a run.  Workflow Skills deliberately
            # reuse readable keys (for example ``short-drama-outline-v1``), while
            # the tasks table has a global primary key.  Namespace persisted task
            # ids by run and translate dependency keys at the repository boundary.
            keys = {
                item.client_key: f"{run.id}:{item.client_key}"
                for item in patch.add_tasks
            }
            existing_rows = await connection.fetch(
                f"SELECT payload FROM {self.schema}.tasks WHERE run_id=$1", run.id
            )
            existing_tasks = [_load(Task, row["payload"]) for row in existing_rows]
            existing = {
                task.client_key: task.id
                for task in existing_tasks
            }
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
            await connection.execute(
                f"INSERT INTO {self.schema}.plan_revisions(id,run_id,number,payload) VALUES($1,$2,$3,$4::jsonb)",
                revision.id, run.id, number, _json(revision),
            )
            for task in created:
                await connection.execute(
                    f"""INSERT INTO {self.schema}.tasks
                    (id,run_id,status,remote_operation_id,created_at,payload)
                    VALUES($1,$2,$3,NULL,$4,$5::jsonb)""",
                    task.id, task.run_id, task.status.value, task.created_at, _json(task),
                )
            for task_id in patch.cancel_task_ids:
                task_id = existing.get(task_id, task_id)
                value = await connection.fetchval(
                    f"SELECT payload FROM {self.schema}.tasks WHERE id=$1 FOR UPDATE", task_id
                )
                task = _load(Task, value)
                task.status = TaskStatus.CANCELLED
                await connection.execute(
                    f"UPDATE {self.schema}.tasks SET status=$1,payload=$2::jsonb WHERE id=$3",
                    task.status.value, _json(task), task.id,
                )
                for index, existing_task in enumerate(existing_tasks):
                    if existing_task.id == task.id:
                        existing_tasks[index] = task
                        break
            stored.current_revision = number
            stored.last_response = patch.response
            has_unfinished_tasks = any(
                task.status in NONTERMINAL_TASK_STATUSES
                for task in [*existing_tasks, *created]
            )
            stored.status = (
                RunStatus.COMPLETED if patch.goal_satisfied and not has_unfinished_tasks else
                RunStatus.WAITING_INPUT if patch.waiting_for_input else RunStatus.RUNNING
            )
            stored.updated_at = now()
            await connection.execute(
                f"""UPDATE {self.schema}.agent_runs SET status=$1,current_revision=$2,
                updated_at=$3,payload=$4::jsonb WHERE id=$5""",
                stored.status, number, stored.updated_at, _json(stored), stored.id,
            )
            return created

    async def list_ready_tasks(self, run_id: str) -> list[Task]:
        rows = await self.pool.fetch(f"SELECT payload FROM {self.schema}.tasks WHERE run_id=$1", run_id)
        tasks = [_load(Task, row["payload"]) for row in rows]
        succeeded = {item.id for item in tasks if item.status == TaskStatus.SUCCEEDED}
        return [
            item for item in tasks
            if item.status in {TaskStatus.PROPOSED, TaskStatus.BLOCKED}
            and set(item.depends_on) <= succeeded
        ]

    async def claim_task(self, task_id: str) -> Task | None:
        """Atomically claim a dependency-ready task for one executor process."""
        async with self.pool.acquire() as connection, connection.transaction():
            value = await connection.fetchval(
                f"SELECT payload FROM {self.schema}.tasks WHERE id=$1 FOR UPDATE",
                task_id,
            )
            if not value:
                return None
            task = _load(Task, value)
            if task.status not in {TaskStatus.PROPOSED, TaskStatus.BLOCKED}:
                return None
            task.status = TaskStatus.RUNNING
            task.execution_phase = "claimed"
            task.attempt_count += 1
            task.started_at = task.started_at or now()
            task.heartbeat_at = now()
            task.finished_at = None
            task.error = None
            task.failure_category = None
            task.action_required = None
            task.updated_at = now()
            await connection.execute(
                f"""UPDATE {self.schema}.tasks
                SET status=$1,remote_operation_id=$2,payload=$3::jsonb WHERE id=$4""",
                task.status.value, task.remote_operation_id, _json(task), task.id,
            )
            run_value = await connection.fetchval(
                f"SELECT payload FROM {self.schema}.agent_runs WHERE id=$1 FOR UPDATE",
                task.run_id,
            )
            run = _load(AgentRun, run_value)
            run.status = RunStatus.RUNNING
            run.updated_at = now()
            await connection.execute(
                f"""UPDATE {self.schema}.agent_runs
                SET status=$1,updated_at=$2,payload=$3::jsonb WHERE id=$4""",
                run.status.value, run.updated_at, _json(run), run.id,
            )
            return task

    async def save_task(self, task: Task) -> None:
        task.updated_at = now()
        await self.pool.execute(
            f"UPDATE {self.schema}.tasks SET status=$1,remote_operation_id=$2,payload=$3::jsonb WHERE id=$4",
            task.status.value, task.remote_operation_id, _json(task), task.id,
        )

    async def add_attempt(self, attempt: TaskAttempt) -> None:
        await self.pool.execute(
            f"INSERT INTO {self.schema}.task_attempts(id,task_id,payload) VALUES($1,$2,$3::jsonb) ON CONFLICT(id) DO NOTHING",
            attempt.id, attempt.task_id, _json(attempt),
        )

    async def save_attempt(self, attempt: TaskAttempt) -> None:
        await self.pool.execute(
            f"""INSERT INTO {self.schema}.task_attempts(id,task_id,payload)
            VALUES($1,$2,$3::jsonb) ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload""",
            attempt.id, attempt.task_id, _json(attempt),
        )

    async def add_artifact(self, artifact: ArtifactVersion, edges: list[ArtifactEdge]) -> ArtifactVersion:
        async with self.pool.acquire() as connection, connection.transaction():
            artifact.version = await connection.fetchval(
                f"SELECT COALESCE(MAX(version),0)+1 FROM {self.schema}.artifact_versions WHERE artifact_id=$1",
                artifact.artifact_id,
            )
            await connection.execute(
                f"""INSERT INTO {self.schema}.artifact_versions
                (id,artifact_id,project_id,artifact_type,version,created_at,payload)
                VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)""",
                artifact.id, artifact.artifact_id, artifact.project_id, artifact.type,
                artifact.version, artifact.created_at, _json(artifact),
            )
            for edge in edges:
                await connection.execute(
                    f"INSERT INTO {self.schema}.artifact_edges(id,project_id,payload) VALUES($1,$2,$3::jsonb)",
                    edge.id, edge.project_id, _json(edge),
                )
            await self._upsert_selection(connection, ArtifactSelection(
                project_id=artifact.project_id, type=artifact.type,
                artifact_version_id=artifact.id,
            ))
        return artifact

    async def _upsert_selection(self, connection, selection: ArtifactSelection) -> None:
        await connection.execute(
            f"""INSERT INTO {self.schema}.artifact_selections
            (project_id,artifact_type,artifact_version_id,payload) VALUES($1,$2,$3,$4::jsonb)
            ON CONFLICT(project_id,artifact_type) DO UPDATE SET
            artifact_version_id=EXCLUDED.artifact_version_id,payload=EXCLUDED.payload""",
            selection.project_id, selection.type, selection.artifact_version_id, _json(selection),
        )

    async def select_artifact(self, project_id: str, version_id: str) -> ArtifactSelection:
        value = await self.pool.fetchval(
            f"SELECT payload FROM {self.schema}.artifact_versions WHERE project_id=$1 AND id=$2",
            project_id, version_id,
        )
        if not value:
            raise LookupError("artifact version not found")
        artifact = _load(ArtifactVersion, value)
        selection = ArtifactSelection(
            project_id=project_id, type=artifact.type, artifact_version_id=version_id
        )
        async with self.pool.acquire() as connection:
            await self._upsert_selection(connection, selection)
        return selection

    async def add_tool_invocation(self, value: ToolInvocation) -> None:
        await self.pool.execute(
            f"""INSERT INTO {self.schema}.tool_invocations
            (id,run_id,task_id,idempotency_key,status,payload) VALUES($1,$2,$3,$4,$5,$6::jsonb)
            ON CONFLICT(idempotency_key) DO NOTHING""",
            value.id, value.run_id, value.task_id, value.idempotency_key, value.status, _json(value),
        )

    async def save_tool_invocation(self, value: ToolInvocation) -> None:
        value.updated_at = now()
        await self.pool.execute(
            f"UPDATE {self.schema}.tool_invocations SET status=$1,payload=$2::jsonb WHERE id=$3",
            value.status, _json(value), value.id,
        )

    async def find_task_by_remote_operation(self, operation_id: str) -> tuple[AgentRun, Task] | None:
        row = await self.pool.fetchrow(
            f"SELECT run_id,payload FROM {self.schema}.tasks WHERE remote_operation_id=$1",
            operation_id,
        )
        return (await self.get_run(row["run_id"]), _load(Task, row["payload"])) if row else None

    async def list_active_runs(self) -> list[AgentRun]:
        rows = await self.pool.fetch(
            f"""SELECT DISTINCT runs.payload
            FROM {self.schema}.agent_runs AS runs
            LEFT JOIN {self.schema}.tasks AS tasks ON tasks.run_id=runs.id
            WHERE runs.status=ANY($1::text[]) OR tasks.status=ANY($2::text[])""",
            [item.value for item in ACTIVE_RUN_STATUSES],
            [item.value for item in NONTERMINAL_TASK_STATUSES],
        )
        return [_load(AgentRun, row["payload"]) for row in rows]

    async def list_waiting_tasks(self) -> list[Task]:
        rows = await self.pool.fetch(
            f"SELECT payload FROM {self.schema}.tasks WHERE status='waiting_external' AND remote_operation_id IS NOT NULL"
        )
        return [_load(Task, row["payload"]) for row in rows]

    async def append_event(self, event: DomainEvent) -> DomainEvent:
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.fetchval(
                f"SELECT id FROM {self.schema}.agent_runs WHERE id=$1 FOR UPDATE", event.run_id
            )
            event.sequence = await connection.fetchval(
                f"SELECT COALESCE(MAX(sequence),0)+1 FROM {self.schema}.events WHERE run_id=$1",
                event.run_id,
            )
            await connection.execute(
                f"INSERT INTO {self.schema}.events(id,run_id,sequence,event_type,payload) VALUES($1,$2,$3,$4,$5::jsonb)",
                event.id, event.run_id, event.sequence, event.type, _json(event),
            )
        return event

    async def get_events(self, run_id: str, after: int = 0) -> list[DomainEvent]:
        rows = await self.pool.fetch(
            f"SELECT payload FROM {self.schema}.events WHERE run_id=$1 AND sequence>$2 ORDER BY sequence",
            run_id, after,
        )
        return [_load(DomainEvent, row["payload"]) for row in rows]

    async def wait_for_events(self, run_id: str, timeout: float = 10.0) -> None:
        await asyncio.sleep(min(timeout, 0.5))

    async def mark_source_event(self, key: str) -> bool:
        result = await self.pool.execute(
            f"INSERT INTO {self.schema}.source_events(fingerprint) VALUES($1) ON CONFLICT DO NOTHING", key
        )
        return result == "INSERT 0 1"

    async def add_command(self, value: UserCommand) -> bool:
        result = await self.pool.execute(
            f"""INSERT INTO {self.schema}.commands(id,run_id,idempotency_key,payload)
            VALUES($1,$2,$3,$4::jsonb) ON CONFLICT(run_id,idempotency_key) DO NOTHING""",
            value.id, value.run_id, value.idempotency_key, _json(value),
        )
        return result == "INSERT 0 1"

    async def add_chat_message(self, value: ChatMessage) -> ChatMessage:
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.fetchval(
                f"SELECT id FROM {self.schema}.agent_runs WHERE id=$1 FOR UPDATE",
                value.run_id,
            )
            value.sequence = await connection.fetchval(
                f"SELECT COALESCE(MAX(sequence),0)+1 FROM {self.schema}.chat_messages WHERE run_id=$1",
                value.run_id,
            )
            await connection.execute(
                f"""INSERT INTO {self.schema}.chat_messages
                (id,run_id,sequence,created_at,payload) VALUES($1,$2,$3,$4,$5::jsonb)
                ON CONFLICT(id) DO NOTHING""",
                value.id, value.run_id, value.sequence, value.created_at, _json(value),
            )
        return value

    async def list_chat_messages(self, run_id: str) -> list[ChatMessage]:
        rows = await self.pool.fetch(
            f"SELECT payload FROM {self.schema}.chat_messages WHERE run_id=$1 ORDER BY sequence,id",
            run_id,
        )
        return [_load(ChatMessage, row["payload"]) for row in rows]
