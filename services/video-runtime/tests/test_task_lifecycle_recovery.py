import asyncio
import sys
from types import SimpleNamespace

import pytest

from app.capabilities.models import CapabilityInputs, CapabilityManifest
from app.chat.v2.capabilities import CapabilityRegistry
from app.chat.v2.executors import CapabilityExecutor, DelegateResult
from app.chat.v2.models import AgentRun, InputFile, PlanPatch, PlannedTask, RunStatus, TaskStatus
from app.chat.v2.repository import (
    ActiveRunDeletionError,
    ActiveRunLimitError,
    InMemoryV2Repository,
)
from app.orchestration.policy import PlanValidator
from app.orchestration.task_runtime.harness import DynamicHarness
from app.integrations.providers.provider_bridge import _wavespeed_generate


def _capabilities() -> CapabilityRegistry:
    return CapabilityRegistry([
        CapabilityManifest(
            id="test.generate",
            description="test generator",
            executor="local.service",
            service_target="test_generate",
            inputs=CapabilityInputs(),
            output_type="video",
        )
    ])


async def _run_with_task(repository: InMemoryV2Repository):
    run, _ = await repository.create_run(AgentRun(
        thread_id="thread-lifecycle",
        project_id="project-lifecycle",
        user_id="user-1",
        objective="generate",
        idempotency_key="request-1",
        status=RunStatus.RUNNING,
    ))
    tasks = await repository.apply_patch(run, PlanPatch(
        base_revision=0,
        add_tasks=[PlannedTask(
            client_key="generation-1",
            capability_id="test.generate",
            objective="generate one video",
        )],
    ))
    return run, tasks[0]


@pytest.mark.asyncio
async def test_delete_terminal_run_removes_run_and_tasks():
    repository = InMemoryV2Repository()
    run, _task = await _run_with_task(repository)
    run.status = RunStatus.FAILED
    await repository.save_run(run)

    counts = await repository.delete_run(run.id, "user-1")

    assert counts is not None
    assert counts["runs"] == 1
    assert counts["tasks"] == 1
    assert await repository.snapshot(run.id) is None
    assert await repository.list_runs("user-1") == []


@pytest.mark.asyncio
async def test_resume_with_new_attachment_persists_file_and_recoordinates():
    repository = InMemoryV2Repository()
    capabilities = _capabilities()
    run, _ = await repository.create_run(AgentRun(
        thread_id="thread-attachment",
        project_id="project-attachment",
        user_id="user-1",
        objective="old objective",
        idempotency_key="request-attachment",
        status=RunStatus.WAITING_INPUT,
    ))
    harness = DynamicHarness(
        repository=repository,
        capabilities=capabilities,
        executor=object(),
        validator=PlanValidator(capabilities, max_revisions=5, max_tasks=5),
    )
    harness.set_coordinator(_NoopCoordinator())
    image = InputFile(type="image", url="https://example.test/product.png")

    await harness.resume_interrupt(
        run.id,
        "user-1",
        "use this product image",
        idempotency_key="resume-with-image",
        input_files=[image],
        thread_id=run.thread_id,
    )

    snapshot = await repository.snapshot(run.id)
    assert snapshot.run.input_files == [image]
    assert snapshot.run.objective == "use this product image"
    user_message = snapshot.messages[-1]
    assert user_message.metadata["input_files"][0]["url"] == image.url


@pytest.mark.asyncio
async def test_delete_executing_run_requires_cancellation():
    repository = InMemoryV2Repository()
    run, _task = await _run_with_task(repository)

    with pytest.raises(ActiveRunDeletionError):
        await repository.delete_run(run.id, "user-1")

    assert await repository.snapshot(run.id) is not None


@pytest.mark.asyncio
async def test_task_claim_is_atomic_and_records_start_metadata():
    repository = InMemoryV2Repository()
    _, task = await _run_with_task(repository)

    first, second = await asyncio.gather(
        repository.claim_task(task.id),
        repository.claim_task(task.id),
    )

    claimed = [item for item in (first, second) if item is not None]
    assert len(claimed) == 1
    assert claimed[0].status == TaskStatus.RUNNING
    assert claimed[0].execution_phase == "claimed"
    assert claimed[0].attempt_count == 1
    assert claimed[0].started_at is not None
    assert claimed[0].heartbeat_at is not None


@pytest.mark.asyncio
async def test_run_cannot_complete_while_child_task_is_unfinished():
    repository = InMemoryV2Repository()
    run, _ = await repository.create_run(AgentRun(
        thread_id="thread-parent",
        project_id="project-parent",
        user_id="user-1",
        objective="generate",
        idempotency_key="request-parent",
    ))
    tasks = await repository.apply_patch(run, PlanPatch(
        base_revision=0,
        goal_satisfied=True,
        add_tasks=[PlannedTask(
            client_key="child",
            capability_id="test.generate",
            objective="unfinished child",
        )],
    ))
    stored = await repository.get_run(run.id)
    assert stored.status == RunStatus.RUNNING
    assert [item.id for item in await repository.list_active_runs()] == [run.id]

    child = tasks[0]
    child.status = TaskStatus.SUCCEEDED
    await repository.save_task(child)
    await repository.apply_patch(stored, PlanPatch(
        base_revision=stored.current_revision,
        goal_satisfied=True,
    ))
    assert (await repository.get_run(run.id)).status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_harness_records_claim_and_provider_submission_as_distinct_phases():
    repository = InMemoryV2Repository()
    run, task = await _run_with_task(repository)
    entered = asyncio.Event()
    release = asyncio.Event()

    class Executor:
        async def submit(self, *_args, **kwargs):
            await kwargs["on_remote_submitted"]("remote-1", "test-provider")
            entered.set()
            await release.wait()
            return DelegateResult(remote_run_id="remote-1", status="submitted")

    capabilities = _capabilities()
    harness = DynamicHarness(
        repository=repository,
        capabilities=capabilities,
        executor=Executor(),
        validator=PlanValidator(capabilities, max_revisions=5, max_tasks=5),
    )

    await harness._schedule_ready(run.id)
    await asyncio.wait_for(entered.wait(), timeout=1)
    snapshot = await repository.snapshot(run.id)
    active = next(item for item in snapshot.tasks if item.id == task.id)
    assert active.status == TaskStatus.RUNNING
    assert active.execution_phase == "provider_polling"
    assert active.remote_operation_id == "remote-1"
    assert active.started_at is not None
    assert active.heartbeat_at is not None

    events = await repository.get_events(run.id)
    assert [event.type for event in events] == [
        "task.started",
        "capability.started",
        "task.remote_submitted",
    ]

    release.set()
    await asyncio.wait_for(harness.execution_tasks[task.id], timeout=1)
    snapshot = await repository.snapshot(run.id)
    waiting = next(item for item in snapshot.tasks if item.id == task.id)
    assert waiting.status == TaskStatus.WAITING_EXTERNAL
    assert waiting.execution_phase == "waiting_external"
    assert waiting.remote_operation_id == "remote-1"


@pytest.mark.asyncio
async def test_restart_fails_all_unfinished_work_without_resuming_coordinator():
    repository = InMemoryV2Repository()
    run, local_task = await _run_with_task(repository)
    local_task = await repository.claim_task(local_task.id)
    run = await repository.get_run(run.id)

    remote_tasks = await repository.apply_patch(run, PlanPatch(
        base_revision=run.current_revision,
        add_tasks=[
            PlannedTask(
                client_key="generation-provider",
                capability_id="test.generate",
                objective="resume provider polling",
            ),
            PlannedTask(
                client_key="generation-remote",
                capability_id="test.generate",
                objective="track delegated remote video",
            ),
        ],
    ))
    provider_task = await repository.claim_task(remote_tasks[0].id)
    provider_task.remote_operation_id = "provider-existing"
    provider_task.execution_phase = "provider_polling"
    await repository.save_task(provider_task)
    remote_task = await repository.claim_task(remote_tasks[1].id)
    remote_task.remote_operation_id = "remote-existing"
    remote_task.execution_phase = "submitting"
    await repository.save_task(remote_task)

    capabilities = _capabilities()
    harness = DynamicHarness(
        repository=repository,
        capabilities=capabilities,
        executor=object(),
        validator=PlanValidator(capabilities, max_revisions=5, max_tasks=5),
    )

    class Coordinator:
        calls = 0

        async def run(self, *_args, **_kwargs):
            self.calls += 1

        async def close(self):
            return None

    coordinator = Coordinator()
    harness.set_coordinator(coordinator)
    assert await harness.resume_active_runs() == 1
    snapshot = await repository.snapshot(run.id)
    by_id = {item.id: item for item in snapshot.tasks}
    assert snapshot.run.status == RunStatus.FAILED
    assert by_id[local_task.id].status == TaskStatus.FAILED
    assert by_id[local_task.id].execution_phase == "failed_on_service_interrupt"
    assert by_id[provider_task.id].status == TaskStatus.FAILED
    assert by_id[provider_task.id].execution_phase == "failed_on_service_interrupt"
    assert by_id[provider_task.id].remote_operation_id == "provider-existing"
    assert by_id[remote_task.id].status == TaskStatus.FAILED
    assert by_id[remote_task.id].execution_phase == "failed_on_service_interrupt"
    assert coordinator.calls == 0


@pytest.mark.asyncio
async def test_one_active_project_per_user_is_enforced_atomically():
    repository = InMemoryV2Repository()
    candidates = [
        AgentRun(
            thread_id=f"thread-{index}", project_id=f"project-{index}",
            user_id="user-limited", objective="generate",
            idempotency_key=f"request-{index}",
        )
        for index in range(2)
    ]

    results = await asyncio.gather(
        *(repository.create_run(run, max_active_runs=1) for run in candidates),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, BaseException) for item in results) == 1
    errors = [item for item in results if isinstance(item, BaseException)]
    assert len(errors) == 1
    assert isinstance(errors[0], ActiveRunLimitError)


@pytest.mark.asyncio
async def test_active_project_limit_can_be_configured_to_x():
    repository = InMemoryV2Repository()
    for index in range(2):
        _, created = await repository.create_run(AgentRun(
            thread_id=f"thread-x-{index}", project_id=f"project-x-{index}",
            user_id="user-x", objective="generate",
            idempotency_key=f"request-x-{index}",
        ), max_active_runs=2)
        assert created

    with pytest.raises(ActiveRunLimitError):
        await repository.create_run(AgentRun(
            thread_id="thread-x-3", project_id="project-x-3",
            user_id="user-x", objective="generate", idempotency_key="request-x-3",
        ), max_active_runs=2)


@pytest.mark.asyncio
async def test_provider_persists_remote_id_before_polling_and_resumes_without_resubmit(
    monkeypatch,
):
    order = []

    class Service:
        async def create_seedance_2_i2v_task(self, **_kwargs):
            order.append("create")
            return "provider-task-1"

        async def poll_seedance_video_task_until_complete(self, request_id, *_args, **_kwargs):
            order.append(f"poll:{request_id}")
            return SimpleNamespace(video_url="https://cdn.example/video.mp4")

    fake_module = SimpleNamespace(get_wavespeed_service=lambda: Service())
    monkeypatch.setitem(sys.modules, "app.llm.wavespeed_service", fake_module)

    async def submitted(remote_id, provider):
        order.append(f"persist:{provider}:{remote_id}")

    profile = {
        "model": "seedance-2.0",
        "prompt": "video",
        "images": ["https://cdn.example/reference.png"],
        "mode": "i2v",
    }
    result = await _wavespeed_generate(profile, submitted)
    assert order == [
        "create",
        "persist:wavespeed:provider-task-1",
        "poll:provider-task-1",
    ]
    assert result["raw_task_id"] == "provider-task-1"

    order.clear()
    resumed = await _wavespeed_generate(
        {**profile, "remote_operation_id": "provider-task-1"},
        submitted,
    )
    assert order == ["poll:provider-task-1"]
    assert resumed["raw_task_id"] == "provider-task-1"


@pytest.mark.asyncio
async def test_capability_executor_forwards_remote_submission_callback(monkeypatch):
    callbacks = []

    async def generate_video(_profile, **kwargs):
        await kwargs["on_remote_submitted"]("provider-task-2", "wavespeed")
        return {
            "raw_task_id": "provider-task-2",
            "video_url": "https://cdn.example/video.mp4",
            "provider_used": "wavespeed",
        }

    monkeypatch.setattr(
        "app.integrations.providers.provider_bridge.generate_video",
        generate_video,
    )
    capabilities = CapabilityRegistry([
        CapabilityManifest(
            id="api.provider.generate",
            description="provider",
            executor="local.service",
            service_target="api_provider_generate",
            inputs=CapabilityInputs(),
            output_type="video",
        )
    ])
    executor = CapabilityExecutor(object(), capabilities)
    run = AgentRun(
        thread_id="thread-provider",
        project_id="project-provider",
        user_id="user-1",
        objective="generate",
        idempotency_key="request-provider",
    )
    task = PlannedTask(
        client_key="provider",
        capability_id="api.provider.generate",
        objective="generate",
        parameters={"prompt": "video"},
    )
    from app.chat.v2.models import Task

    stored_task = Task(run_id=run.id, revision=1, **task.model_dump())

    async def submitted(remote_id, provider):
        callbacks.append((remote_id, provider))

    result = await executor.submit(
        run,
        stored_task,
        [],
        "stable-key",
        on_remote_submitted=submitted,
    )
    assert callbacks == [("provider-task-2", "wavespeed")]
    assert result.remote_run_id == "provider-task-2"


class _NoopCoordinator:
    async def run(self, *_args, **_kwargs):
        return None

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_retryable_failure_is_reported_and_retried_automatically():
    repository = InMemoryV2Repository()
    run, task = await _run_with_task(repository)

    class FlakyExecutor:
        calls = 0

        async def submit(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise asyncio.TimeoutError()
            return DelegateResult(
                remote_run_id="local-success",
                status="succeeded",
                artifact={
                    "uri": "https://cdn.example/result.mp4",
                    "title": "Recovered result",
                    "summary": "ok",
                },
            )

    executor = FlakyExecutor()
    capabilities = _capabilities()
    harness = DynamicHarness(
        repository=repository,
        capabilities=capabilities,
        executor=executor,
        validator=PlanValidator(capabilities, max_revisions=5, max_tasks=5),
        task_max_attempts=2,
        retry_base_delay_seconds=0,
        progress_initial_seconds=999,
    )
    harness.set_coordinator(_NoopCoordinator())

    first = await repository.claim_task(task.id)
    await harness._execute(first)
    snapshot = await repository.snapshot(run.id)
    retrying = next(item for item in snapshot.tasks if item.id == task.id)
    assert retrying.status == TaskStatus.PROPOSED
    assert retrying.execution_phase == "retry_scheduled"
    assert retrying.failure_category == "transient_service_error"
    assert any("自动进行第 2/2 次尝试" in item.content for item in snapshot.messages)

    second = await repository.claim_task(task.id)
    await harness._execute(second)
    snapshot = await repository.snapshot(run.id)
    completed = next(item for item in snapshot.tasks if item.id == task.id)
    assert completed.status == TaskStatus.SUCCEEDED
    assert completed.attempt_count == 2
    assert completed.failure_category is None
    assert executor.calls == 2
    await harness.close()


@pytest.mark.asyncio
async def test_wrapped_provider_credit_reservation_error_retries_only_failed_task(monkeypatch):
    from app.integrations.providers.provider_bridge import ProviderGenerateError

    repository = InMemoryV2Repository()
    run, task = await _run_with_task(repository)

    class CreditReservationExecutor:
        calls = 0

        async def submit(self, *_args, **_kwargs):
            self.calls += 1
            provider_error = ProviderGenerateError(
                "Insufficient credits. Please top up your account to continue.",
                category="insufficient_credits",
                retryable=True,
                remote_task_id=None,
                provider="wavespeed",
                model="seedance-2.5",
            )
            raise RuntimeError(str(provider_error)) from provider_error

    executor = CreditReservationExecutor()
    capabilities = _capabilities()
    harness = DynamicHarness(
        repository=repository,
        capabilities=capabilities,
        executor=executor,
        validator=PlanValidator(capabilities, max_revisions=5, max_tasks=5),
        task_max_attempts=2,
        retry_base_delay_seconds=0,
        progress_initial_seconds=999,
    )

    # Avoid waiting for the production backoff while retaining the retry policy
    # assertion via the persisted event payload.
    async def no_wait(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    claimed = await repository.claim_task(task.id)
    await harness._execute(claimed)

    snapshot = await repository.snapshot(run.id)
    retrying = next(item for item in snapshot.tasks if item.id == task.id)
    assert retrying.status == TaskStatus.PROPOSED
    assert retrying.failure_category == "insufficient_credits"
    assert retrying.attempt_count == 1
    assert retrying.remote_operation_id is None
    events = await repository.get_events(run.id)
    retry_event = next(event for event in events if event.type == "task.retry_scheduled")
    assert retry_event.payload["retry_delay_seconds"] == 15.0
    assert executor.calls == 1


@pytest.mark.asyncio
async def test_nonretryable_configuration_failure_exposes_user_action():
    repository = InMemoryV2Repository()
    run, task = await _run_with_task(repository)

    class MissingConfigExecutor:
        async def submit(self, *_args, **_kwargs):
            raise RuntimeError(
                "media.transcribe requires OPENAI_API_KEY or "
                "DEEP_AGENT_V2_OPENAI_API_KEY"
            )

    capabilities = _capabilities()
    harness = DynamicHarness(
        repository=repository,
        capabilities=capabilities,
        executor=MissingConfigExecutor(),
        validator=PlanValidator(capabilities, max_revisions=5, max_tasks=5),
        task_max_attempts=3,
        retry_base_delay_seconds=0,
        progress_initial_seconds=999,
    )

    claimed = await repository.claim_task(task.id)
    await harness._execute(claimed)
    snapshot = await repository.snapshot(run.id)
    failed = next(item for item in snapshot.tasks if item.id == task.id)
    assert failed.status == TaskStatus.FAILED
    assert failed.failure_category == "missing_configuration"
    assert "OPENAI_API_KEY" in failed.action_required
    assert snapshot.run.status == RunStatus.WAITING_INPUT
    assert any(
        item.metadata.get("kind") == "task_user_action_required"
        and "OPENAI_API_KEY" in item.content
        for item in snapshot.messages
    )


@pytest.mark.asyncio
async def test_long_running_task_pushes_progress_into_chat():
    repository = InMemoryV2Repository()
    capabilities = CapabilityRegistry([
        CapabilityManifest(
            id="media.transcribe",
            description="transcribe",
            executor="local.service",
            service_target="media_transcribe",
            inputs=CapabilityInputs(),
            output_type="transcript",
        )
    ])
    run, _ = await repository.create_run(AgentRun(
        thread_id="thread-progress",
        project_id="project-progress",
        user_id="user-1",
        objective="subtitle",
        idempotency_key="request-progress",
        status=RunStatus.RUNNING,
    ))
    tasks = await repository.apply_patch(run, PlanPatch(
        base_revision=0,
        add_tasks=[PlannedTask(
            client_key="transcribe",
            capability_id="media.transcribe",
            objective="转写英文音轨",
        )],
    ))
    release = asyncio.Event()

    class SlowExecutor:
        async def submit(self, *_args, **_kwargs):
            await release.wait()
            return DelegateResult(
                remote_run_id="local-transcript",
                status="succeeded",
                artifact={
                    "title": "Transcript",
                    "summary": "done",
                    "segments": [{"start": 0, "end": 1, "text": "Hello"}],
                },
            )

    harness = DynamicHarness(
        repository=repository,
        capabilities=capabilities,
        executor=SlowExecutor(),
        validator=PlanValidator(capabilities, max_revisions=5, max_tasks=5),
        progress_initial_seconds=0.01,
        progress_interval_seconds=0.02,
    )
    harness.set_coordinator(_NoopCoordinator())
    claimed = await repository.claim_task(tasks[0].id)
    execution = asyncio.create_task(harness._execute(claimed))
    await asyncio.sleep(0.04)
    snapshot = await repository.snapshot(run.id)
    assert any(
        item.metadata.get("kind") == "task_progress"
        and "仍在处理" in item.content
        for item in snapshot.messages
    )
    release.set()
    await execution
    await harness.close()
