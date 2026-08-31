"""Tests for max concurrent video-generation tasks (platform harness policy)."""
from __future__ import annotations

import asyncio

import pytest

from app.chat.v2.capabilities import CapabilityRegistry
from app.chat.v2.context import ContextAssembler
from app.chat.v2.executors import CapabilityExecutor
from app.chat.v2.generation_limits import is_video_generation_capability
from app.chat.v2.harness import DynamicHarness
from app.chat.v2.models import (
    AgentRun,
    PlanPatch,
    PlannedTask,
    RunSnapshot,
    Task,
    TaskStatus,
)
from app.chat.v2.plan_validator import PlanValidator
from app.chat.v2.repository import InMemoryV2Repository


def test_assemble_and_edit_are_not_video_generation():
    capabilities = CapabilityRegistry()
    assert is_video_generation_capability(capabilities.get("video_gen.generate"))
    assert is_video_generation_capability(capabilities.get("video.generate"))
    assert not is_video_generation_capability(capabilities.get("video.assemble"))
    assert not is_video_generation_capability(capabilities.get("video.edit"))


def test_context_includes_standing_parallel_generation_policy():
    run = AgentRun(
        thread_id="thread-1",
        project_id="project-1",
        user_id="user-1",
        objective="video",
        idempotency_key="request-1",
    )
    context = ContextAssembler(
        recent_message_limit=5,
        max_parallel_generation_tasks=3,
    ).assemble(RunSnapshot(run=run))
    assert "Platform policy (standing)" in context
    assert "At most 3 concurrent video-generation" in context


def test_plan_rejects_more_than_three_video_generation_tasks():
    capabilities = CapabilityRegistry()
    validator = PlanValidator(
        capabilities,
        max_revisions=20,
        max_tasks=50,
        max_parallel_generation_tasks=3,
    )
    snapshot = RunSnapshot(run=AgentRun(
        thread_id="thread-1",
        project_id="project-1",
        user_id="user-1",
        objective="Generate a 3-minute comedy video in twelve 15-second segments.",
        idempotency_key="request-1",
    ))
    with pytest.raises(ValueError, match="too many concurrent video-generation"):
        validator.validate(snapshot, PlanPatch(
            base_revision=0,
            add_tasks=[
                PlannedTask(
                    capability_id="video_gen.generate",
                    objective=f"segment {index}",
                    client_key=f"seg-{index}",
                    parameters={"duration": 15, "segment_index": index},
                )
                for index in range(1, 5)
            ],
        ))


def test_plan_allows_remaining_generation_slots_only():
    capabilities = CapabilityRegistry()
    validator = PlanValidator(
        capabilities,
        max_revisions=20,
        max_tasks=50,
        max_parallel_generation_tasks=3,
    )
    run = AgentRun(
        thread_id="thread-1",
        project_id="project-1",
        user_id="user-1",
        objective="Generate a short comedy video in multiple segments.",
        idempotency_key="request-1",
    )
    snapshot = RunSnapshot(
        run=run,
        tasks=[
            Task(
                id="t1",
                run_id=run.id,
                revision=1,
                client_key="seg-1",
                capability_id="video_gen.generate",
                objective="segment 1",
                status=TaskStatus.RUNNING,
            ),
            Task(
                id="t2",
                run_id=run.id,
                revision=1,
                client_key="seg-2",
                capability_id="video_gen.generate",
                objective="segment 2",
                status=TaskStatus.WAITING_EXTERNAL,
            ),
        ],
    )
    with pytest.raises(ValueError, match="1 slot\\(s\\) remaining"):
        validator.validate(snapshot, PlanPatch(
            base_revision=0,
            add_tasks=[
                PlannedTask(
                    capability_id="video_gen.generate",
                    objective="segment 3",
                    client_key="seg-3",
                    parameters={"duration": 15, "segment_index": 3},
                ),
                PlannedTask(
                    capability_id="video_gen.generate",
                    objective="segment 4",
                    client_key="seg-4",
                    parameters={"duration": 15, "segment_index": 4},
                ),
            ],
        ))
    validator.validate(snapshot, PlanPatch(
        base_revision=0,
        add_tasks=[
            PlannedTask(
                capability_id="video_gen.generate",
                objective="segment 3",
                client_key="seg-3",
                parameters={"duration": 15, "segment_index": 3},
            ),
        ],
    ))


def test_assemble_does_not_consume_generation_slots():
    capabilities = CapabilityRegistry()
    validator = PlanValidator(
        capabilities,
        max_revisions=20,
        max_tasks=50,
        max_parallel_generation_tasks=3,
    )
    run = AgentRun(
        thread_id="thread-1",
        project_id="project-1",
        user_id="user-1",
        objective="Assemble existing clips into one video.",
        idempotency_key="request-1",
    )
    snapshot = RunSnapshot(
        run=run,
        tasks=[
            Task(
                id=f"t{index}",
                run_id=run.id,
                revision=1,
                client_key=f"seg-{index}",
                capability_id="video_gen.generate",
                objective=f"segment {index}",
                status=TaskStatus.RUNNING,
            )
            for index in range(1, 4)
        ],
    )
    # Slots full for generation, but assemble is allowed.
    validator.validate(snapshot, PlanPatch(
        base_revision=0,
        add_tasks=[
            PlannedTask(
                capability_id="video.assemble",
                objective="concat",
                client_key="assemble-1",
            ),
        ],
    ))


@pytest.mark.asyncio
async def test_schedule_ready_throttles_video_generation_to_limit():
    class Client:
        async def delegate_submit(self, **_kwargs):
            await asyncio.sleep(3600)
            raise AssertionError("should not finish")

    repository = InMemoryV2Repository()
    capabilities = CapabilityRegistry()
    validator = PlanValidator(
        capabilities,
        max_revisions=20,
        max_tasks=50,
        max_parallel_generation_tasks=3,
    )
    harness = DynamicHarness(
        repository=repository,
        capabilities=capabilities,
        validator=validator,
        executor=CapabilityExecutor(Client(), capabilities),
        context_assembler=ContextAssembler(max_parallel_generation_tasks=3),
        max_parallel_generation_tasks=3,
    )
    run = AgentRun(
        thread_id="thread-1",
        project_id="project-1",
        user_id="user-1",
        objective="Generate comedy video segments.",
        idempotency_key="request-1",
    )
    stored, _ = await repository.create_run(run)
    # Bypass validator so we can inject 5 ready generation tasks.
    for index in range(1, 6):
        task = Task(
            id=f"task-{index}",
            run_id=stored.id,
            revision=1,
            client_key=f"seg-{index}",
            capability_id="video_gen.generate",
            objective=f"segment {index}",
            status=TaskStatus.PROPOSED,
            parameters={"duration": 15},
        )
        repository.tasks[stored.id].append(task)

    await harness._schedule_ready(stored.id)
    statuses = {
        item.id: item.status
        for item in repository.tasks[stored.id]
    }
    running = [task_id for task_id, status in statuses.items() if status == TaskStatus.RUNNING]
    proposed = [task_id for task_id, status in statuses.items() if status == TaskStatus.PROPOSED]
    assert len(running) == 3
    assert len(proposed) == 2

    for task in list(harness.tasks):
        task.cancel()
    if harness.tasks:
        await asyncio.gather(*harness.tasks, return_exceptions=True)
