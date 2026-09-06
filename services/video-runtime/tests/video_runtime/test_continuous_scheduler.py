from __future__ import annotations

import asyncio
import json
import unittest
import tempfile
from pathlib import Path
import httpx
from fastapi import FastAPI
from app.video_runtime.api import router, get_runtime

from app.video_runtime.models import CheckpointResolution, ProjectIntent, RebuildPlanItem
from app.video_runtime.checkpoint_coordinator import CheckpointCoordinator, delivered_turn_ended
from app.video_runtime.local_repository import LocalJsonVideoProjectRepository
from test_initial_build import FakePlanExecutor, video_runtime


class ControlledExecutor(FakePlanExecutor):
    def __init__(self):
        super().__init__()
        self.started = {name: asyncio.Event() for name in ("fast", "slow", "new", "blocked")}
        self.release = asyncio.Event()
        self.fail_fast = False

    async def execute_plan_step(self, **kwargs):
        name = kwargs["step"].step_id
        if name in self.started:
            self.started[name].set()
        if name == "slow":
            if kwargs.get("report_remote_operation"):
                await kwargs["report_remote_operation"]("remote-slow", "provider-test")
            await self.release.wait()
        if name == "fast" and self.fail_fast:
            raise ValueError("controlled Provider failure")
        return await super().execute_plan_step(**kwargs)


class ContinuousSchedulerTest(unittest.IsolatedAsyncioTestCase):
    def test_legacy_repair_step_with_null_idempotency_key_loads_as_unassigned(self):
        item = RebuildPlanItem.model_validate({
            "step_id": "legacy-repair", "action": "create", "idempotency_key": None,
        })
        self.assertEqual(item.idempotency_key, "")

    async def asyncSetUp(self):
        self.runtime = await video_runtime(max_parallel_generation_tasks=2)
        self.runtime.staged_planning_enabled = True
        self.runtime.continuous_plan_patch_enabled = True
        self.project, self.version = await self.runtime.create_project(user_id="u", title="Dynamic")
        await self.runtime.bind_session(project_id=self.project.id, session_id="s", user_id="u")
        self.plan = await self.runtime.plan_project(
            project_id=self.project.id, base_project_version_id=self.version.id,
            project_intent=ProjectIntent(title="Dynamic", brief="Make a video", workflow_id="seedance2"),
            idempotency_key="plan",
        )
        self.build = await self.runtime.start_build(
            project_id=self.project.id, plan_id=self.plan.id, base_project_version_id=self.version.id,
            idempotency_key="build", session_id="s", user_id="u",
        )
        self.executor = ControlledExecutor()
        await self.run_build()
        self.worker = None

    async def asyncTearDown(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        await self.runtime.close()

    async def run_build(self):
        return await self.runtime.execute_build(
            project_id=self.project.id, build_id=self.build.id, executor=self.executor,
        )

    async def test_text_only_turn_recovers_with_fresh_completed_artifacts_after_restart(self):
        await self.resolve(await self.checkpoint(), proposed_steps=[self.task("fast"), self.task("slow")])
        self.worker = asyncio.create_task(self.run_build())
        await asyncio.wait_for(self.executor.started["slow"].wait(), 3)
        checkpoint = await self.checkpoint()

        class Session:
            calls = []
            events = []
            async def prompt(self, session_id, prompt, *, mode):
                self.calls.append(prompt)
            async def history(self, session_id, **kwargs):
                return {"events": self.events}

        session = Session()
        await CheckpointCoordinator(self.runtime, session).run_once()
        claimed = await self.runtime.repo.get_checkpoint(self.project.id, self.build.id, checkpoint.id)
        timestamp = claimed.updated_at.timestamp() * 1000 + 1
        session.events = [
            {"event": {"type": "user/message", "time": timestamp,
                       "data": {"content": [{"type": "text", "text": session.calls[-1]}]}}},
            {"event": {"type": "turn/end", "time": timestamp + 1, "data": {}}},
        ]
        self.executor.release.set()
        await asyncio.wait_for(self.worker, 3)
        coordinator = CheckpointCoordinator(self.runtime, session)
        await coordinator.reconcile_finished_turns()
        self.assertTrue(await coordinator.run_once())
        payload = json.loads(session.calls[-1].split("Checkpoint payload:\n", 1)[1].split("\nACTION REQUIRED", 1)[0])
        self.assertIn('"step_id": "slow", "status": "completed"', payload["planner_instruction"])
        self.assertFalse(await coordinator.run_once())
        refreshed = await self.runtime.inspect_checkpoint(
            project_id=self.project.id, build_id=self.build.id, checkpoint_id=checkpoint.id,
        )
        self.assertIn('"step_id": "slow", "status": "completed"', refreshed.planner_instruction)
        # A replayed observer of delivery 1 cannot release delivery 2.
        result = await self.runtime.repo.fail_checkpoint_delivery(checkpoint.id, "stale", expected_attempt=1)
        self.assertEqual(result.status, "planning")
        self.assertEqual(result.delivery_attempts, 2)
        await self.resolve(refreshed, proposed_steps=[self.task("new", ["slow"])])
        await self.run_build()
        self.assertTrue(self.executor.started["new"].is_set())

    async def test_turn_recovery_requires_consumed_message_and_no_later_active_turn(self):
        checkpoint = await self.checkpoint()
        timestamp = checkpoint.updated_at.timestamp() * 1000 + 1
        unrelated = {"event": {"type": "turn/end", "time": timestamp}}
        self.assertFalse(delivered_turn_ended(checkpoint, {"events": [unrelated]}))
        consumed = {"event": {"type": "user/message", "time": timestamp,
            "data": {"content": [{"type": "text", "text": "CUTI_VIDEO_CHECKPOINT_V1 " + checkpoint.id}]}}}
        self.assertTrue(delivered_turn_ended(checkpoint, {"events": [consumed, unrelated]}))
        started = {"event": {"type": "turn/start", "time": timestamp + 2}}
        self.assertFalse(delivered_turn_ended(checkpoint, {"events": [consumed, unrelated, started]}))

    async def test_plan_patch_resolves_concat_inputs_from_new_dependency_outputs(self):
        video = self.task("clip")
        video.output_artifact_type = "video"
        concat = RebuildPlanItem(
            step_id="concat", action="create", capability="media.concat",
            output_artifact_type="video", parameters={"video_urls": []}, depends_on=["clip"],
        )
        plan = await self.resolve(await self.checkpoint(), proposed_steps=[video, concat])
        stored = next(item for item in plan.items if item.step_id == "concat")
        self.assertEqual(stored.parameters["video_steps"], ["clip"])
        self.assertNotIn("video_urls", stored.parameters)

    async def checkpoint(self):
        async def wait():
            while True:
                checkpoints = await self.runtime.repo.list_build_checkpoints(self.project.id, self.build.id)
                for checkpoint in reversed(checkpoints):
                    if checkpoint.status in {"pending", "planning"}:
                        return checkpoint
                await asyncio.sleep(0.001)
        return await asyncio.wait_for(wait(), 3)

    def task(self, name, dependencies=None):
        return RebuildPlanItem(
            step_id=name, action="create", capability="atomic.video.generate",
            parameters={"prompt": name, "model": "seedance-2.0", "generate_audio": True},
            depends_on=dependencies or ["intent"],
        )

    async def resolve(self, checkpoint, **kwargs):
        return await self.runtime.resolve_checkpoint(
            project_id=self.project.id, build_id=self.build.id, checkpoint_id=checkpoint.id,
            session_id="s", user_id="u",
            resolution=CheckpointResolution(
                base_plan_revision=checkpoint.base_plan_revision,
                base_spec_revision=checkpoint.base_spec_revision,
                idempotency_key=f"patch-{checkpoint.id}", video_spec_patch={}, **kwargs,
            ),
        )

    async def test_completion_wakes_agent_and_patch_runs_while_another_task_is_waiting(self):
        await self.resolve(await self.checkpoint(), proposed_steps=[
            self.task("fast"), self.task("slow"), self.task("blocked", ["slow"]),
        ])
        self.worker = asyncio.create_task(self.run_build())
        await asyncio.wait_for(self.executor.started["slow"].wait(), 3)
        checkpoint = await self.checkpoint()
        self.assertFalse(self.executor.release.is_set())
        self.assertIn('"step_id": "slow"', checkpoint.planner_instruction)

        class Session:
            calls = []
            async def prompt(self, session_id, prompt, *, mode):
                self.calls.append((session_id, prompt, mode))
        session = Session()
        self.assertTrue(await CheckpointCoordinator(self.runtime, session).run_once())
        self.assertEqual(session.calls[0][0::2], ("s", "queue"))

        patch = dict(proposed_steps=[self.task("new", ["fast"])], cancel_step_ids=["blocked"])
        first = await self.resolve(checkpoint, **patch)
        replay = await self.resolve(checkpoint, **patch)
        self.assertEqual(first.current_revision, replay.current_revision)
        await asyncio.wait_for(self.executor.started["new"].wait(), 3)
        self.assertFalse(self.executor.started["blocked"].is_set())
        self.assertFalse(self.executor.release.is_set())
        self.executor.release.set()
        await asyncio.wait_for(self.worker, 3)
        states = {s.plan_step_id: s for s in await self.runtime.repo.list_build_steps(self.project.id, self.build.id)}
        self.assertEqual(states["blocked"].status, "cancelled")
        self.assertEqual(states["new"].status, "completed")
        self.assertEqual(self.executor.calls.count("new"), 1)

    async def test_failure_notifies_before_slow_task_finishes_and_accepts_replacement(self):
        self.executor.fail_fast = True
        await self.resolve(await self.checkpoint(), proposed_steps=[self.task("fast"), self.task("slow")])
        self.worker = asyncio.create_task(self.run_build())
        checkpoint = await self.checkpoint()
        self.assertIn("controlled Provider failure", checkpoint.planner_instruction)
        self.assertFalse(self.executor.release.is_set())
        self.assertNotEqual((await self.runtime.repo.get_build(self.project.id, self.build.id)).status, "failed")
        await self.resolve(checkpoint, proposed_steps=[self.task("new")])
        await asyncio.wait_for(self.executor.started["new"].wait(), 3)
        self.executor.release.set()
        await asyncio.wait_for(self.worker, 3)

    async def test_repair_changes_parameters_and_rewires_pending_descendants(self):
        self.executor.fail_fast = True
        await self.resolve(await self.checkpoint(), proposed_steps=[
            self.task("fast"), self.task("blocked", ["fast"]),
        ])
        await self.run_build()
        build = await self.runtime.repo.get_build(self.project.id, self.build.id)
        build.status = "failed"
        await self.runtime.repo.update_build(build)
        checkpoint = await self.runtime.inspect_checkpoint(
            project_id=self.project.id, build_id=self.build.id, checkpoint_id="live", session_id="s", user_id="u",
        )
        self.assertEqual((await self.runtime.repo.get_build(self.project.id, self.build.id)).status, "failed")
        replacement = self.task("new")
        replacement.parameters["prompt"] = "corrected prompt"
        patch = dict(proposed_steps=[replacement], replace_failed_step_ids={"fast": "new"})
        plan = await self.resolve(checkpoint, **patch)
        replay = await self.resolve(checkpoint, **patch)
        self.assertEqual(plan.current_revision, replay.current_revision)
        new = next(item for item in plan.items if item.step_id == "new")
        self.assertEqual(new.parameters["prompt"], "corrected prompt")
        child = next(item for item in plan.items if item.step_id.startswith("blocked:repair:"))
        self.assertEqual(child.depends_on, ["new"])
        await self.run_build()
        states = {item.plan_step_id: item for item in await self.runtime.repo.list_build_steps(self.project.id, self.build.id)}
        self.assertEqual(states["blocked"].status, "cancelled")
        self.assertEqual(states["fast"].status, "failed")
        self.assertEqual(states[child.step_id].status, "completed")
        self.assertEqual(self.executor.calls.count("new"), 1)
        with self.assertRaisesRegex(ValueError, "failed task"):
            cp = await self.checkpoint()
            await self.resolve(cp, proposed_steps=[self.task("another")], replace_failed_step_ids={"new": "another"})
        build = await self.runtime.repo.get_build(self.project.id, self.build.id)
        build.status = "failed"
        await self.runtime.repo.update_build(build)
        await self.runtime.repo.requeue_failed_build(self.project.id, self.build.id)
        states = {item.plan_step_id: item for item in await self.runtime.repo.list_build_steps(self.project.id, self.build.id)}
        self.assertEqual(states["fast"].status, "failed")
        self.assertEqual(states["blocked"].status, "cancelled")

    async def test_repair_rejects_cancelled_build_and_cycle_without_mutation(self):
        self.executor.fail_fast = True
        await self.resolve(await self.checkpoint(), proposed_steps=[self.task("fast"), self.task("blocked", ["fast"])])
        await self.run_build()
        checkpoint = await self.checkpoint()
        before = (await self.runtime.repo.get_plan(self.plan.id)).current_revision
        with self.assertRaises(ValueError):
            await self.resolve(checkpoint, proposed_steps=[self.task("new", ["blocked"])], replace_failed_step_ids={"fast": "new"})
        self.assertEqual((await self.runtime.repo.get_plan(self.plan.id)).current_revision, before)
        await self.runtime.cancel_build(project_id=self.project.id, build_id=self.build.id)
        with self.assertRaises(ValueError):
            await self.runtime.inspect_checkpoint(project_id=self.project.id, build_id=self.build.id, checkpoint_id="live", session_id="s", user_id="u")

    async def test_user_stop_preserves_remote_work_and_confirmed_resume_reuses_drafts(self):
        await self.resolve(await self.checkpoint(), proposed_steps=[
            self.task("fast"), self.task("slow"), self.task("blocked", ["slow"]),
        ])
        self.runtime._default_executor = self.executor
        self.runtime._schedule_build(await self.runtime.repo.get_build(self.project.id, self.build.id))
        await asyncio.wait_for(self.executor.started["slow"].wait(), 3)
        await self.checkpoint()
        await self.runtime.cancel_build(project_id=self.project.id, build_id=self.build.id)
        steps = {s.plan_step_id: s for s in await self.runtime.repo.list_build_steps(self.project.id, self.build.id)}
        self.assertEqual(steps["fast"].status, "completed")
        self.assertEqual(steps["slow"].remote_operation_id, "remote-slow")
        self.assertEqual(steps["blocked"].status, "pending")
        self.assertFalse(self.executor.started["blocked"].is_set())
        class Session:
            async def prompt(self, *args, **kwargs):
                raise AssertionError("stopped task must not wake Agent")
        self.assertFalse(await CheckpointCoordinator(self.runtime, Session()).run_once())
        self.executor.release.set()
        await self.runtime.resume_cancelled_build(project_id=self.project.id, build_id=self.build.id)
        await asyncio.wait_for(self.executor.started["blocked"].wait(), 3)
        await asyncio.wait_for(self.runtime._build_tasks[self.build.id], 3)
        self.assertEqual(self.executor.calls.count("fast"), 1)
        self.assertEqual(self.executor.calls.count("slow"), 1)

    async def test_running_task_cannot_be_cancelled_or_goal_committed(self):
        await self.resolve(await self.checkpoint(), proposed_steps=[self.task("fast"), self.task("slow")])
        self.worker = asyncio.create_task(self.run_build())
        checkpoint = await self.checkpoint()
        with self.assertRaisesRegex(ValueError, "only pending"):
            await self.resolve(checkpoint, cancel_step_ids=["slow"])
        with self.assertRaisesRegex(ValueError, "active tasks"):
            await self.resolve(checkpoint, goal_satisfied=True)
        await self.resolve(checkpoint)  # Acknowledge while the provider continues.
        self.executor.release.set()
        await asyncio.wait_for(self.worker, 3)
        final = await self.checkpoint()
        await self.resolve(final, goal_satisfied=True)
        completed, version = await self.run_build()
        self.assertEqual(completed.status, "completed")
        self.assertIsNotNone(version)

    async def test_shutdown_preserves_remote_operation_for_resume(self):
        await self.resolve(await self.checkpoint(), proposed_steps=[self.task("fast"), self.task("slow")])
        self.worker = asyncio.create_task(self.run_build())
        await self.checkpoint()
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
        states = {s.plan_step_id: s for s in await self.runtime.repo.list_build_steps(self.project.id, self.build.id)}
        self.assertEqual(states["slow"].remote_operation_id, "remote-slow")
        self.assertEqual(states["slow"].status, "waiting_external")
        self.executor.release.set()
        await self.run_build()
        self.assertEqual(self.executor.remote_resumes["slow"], "remote-slow")

    async def test_user_can_open_live_patch_without_a_new_completion(self):
        await self.resolve(await self.checkpoint(), proposed_steps=[self.task("slow"), self.task("blocked", ["slow"])])
        self.worker = asyncio.create_task(self.run_build())
        await asyncio.wait_for(self.executor.started["slow"].wait(), 3)
        checkpoint = await self.runtime.inspect_checkpoint(
            project_id=self.project.id, build_id=self.build.id, checkpoint_id="live",
            session_id="s", user_id="u",
        )
        self.assertTrue(checkpoint.phase.startswith("live:"))
        await self.resolve(checkpoint, proposed_steps=[self.task("new")], cancel_step_ids=["blocked"])
        await asyncio.wait_for(self.executor.started["new"].wait(), 3)
        self.assertFalse(self.executor.started["blocked"].is_set())
        self.executor.release.set()
        await asyncio.wait_for(self.worker, 3)

    async def test_unknown_and_cyclic_dependencies_are_rejected(self):
        checkpoint = await self.checkpoint()
        for tasks in ([self.task("fast", ["absent"])], [self.task("fast", ["slow"]), self.task("slow", ["fast"])]):
            with self.assertRaises(ValueError):
                await self.resolve(checkpoint, proposed_steps=tasks)

    async def test_live_snapshot_rejects_wrong_session(self):
        with self.assertRaises(PermissionError):
            await self.runtime.inspect_checkpoint(
                project_id=self.project.id, build_id=self.build.id,
                checkpoint_id="live", session_id="someone-else", user_id="u",
            )

    async def test_late_delivery_failure_cannot_reopen_a_resolved_checkpoint(self):
        checkpoint = await self.checkpoint()
        await self.resolve(checkpoint, proposed_steps=[self.task("fast")])
        result = await self.runtime.repo.fail_checkpoint_delivery(checkpoint.id, "late transport error")
        self.assertEqual(result.status, "resolved")
        self.assertIsNone(result.error)

    async def test_http_live_edit_reuses_snapshot_and_cancels_pending_work(self):
        await self.resolve(await self.checkpoint(), proposed_steps=[self.task("slow"), self.task("blocked", ["slow"])])
        self.worker = asyncio.create_task(self.run_build())
        await asyncio.wait_for(self.executor.started["slow"].wait(), 3)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_runtime] = lambda: self.runtime
        path = f"/api/video/projects/{self.project.id}/builds/{self.build.id}/checkpoints"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"X-Video-User-Id": "u", "X-Video-Session-Id": "s"},
        ) as client:
            self.assertEqual((await client.get(path + "/live")).status_code, 405)
            opened = await client.post(path + "/live")
            self.assertEqual(opened.status_code, 200, opened.text)
            checkpoint = opened.json()["data"]
            again = await client.post(path + "/live")
            self.assertEqual(again.json()["data"]["id"], checkpoint["id"])
            body = {
                "basePlanRevision": checkpoint["base_plan_revision"],
                "baseSpecRevision": checkpoint["base_spec_revision"],
                "idempotencyKey": "http-edit", "videoSpecPatch": {},
                "cancelStepIds": ["blocked"],
                "proposedSteps": [self.task("new").model_dump(mode="json")],
            }
            submitted = await client.post(path + f"/{checkpoint['id']}/resolve", json=body)
            self.assertEqual(submitted.status_code, 200, submitted.text)
            repeated = await client.post(path + f"/{checkpoint['id']}/resolve", json=body)
            self.assertEqual(repeated.json(), submitted.json())
        await asyncio.wait_for(self.executor.started["new"].wait(), 3)
        self.assertFalse(self.executor.started["blocked"].is_set())
        self.executor.release.set()
        await asyncio.wait_for(self.worker, 3)

    async def test_cancellation_racing_admission_is_atomic(self):
        await self.resolve(await self.checkpoint(), proposed_steps=[self.task("fast"), self.task("slow")])
        checkpoint = await self.runtime.inspect_checkpoint(
            project_id=self.project.id, build_id=self.build.id,
            checkpoint_id="live", session_id="s", user_id="u",
        )
        # Force admission after Runtime validation but before repository commit.
        original = self.runtime.repo.resolve_checkpoint
        async def raced(**kwargs):
            await self.runtime.repo.claim_build_step(self.project.id, self.build.id, "slow")
            return await original(**kwargs)
        self.runtime.repo.resolve_checkpoint = raced
        with self.assertRaisesRegex(ValueError, "non-pending"):
            await self.resolve(checkpoint, proposed_steps=[self.task("new")], cancel_step_ids=["fast", "slow"])
        states = {s.plan_step_id: s for s in await self.runtime.repo.list_build_steps(self.project.id, self.build.id)}
        self.assertEqual(states["fast"].status, "pending")
        self.assertEqual(states["slow"].status, "running")
        self.assertNotIn("new", states)

    async def test_restart_recovers_waiting_agent_and_remote_sibling_without_replaying_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            local = LocalJsonVideoProjectRepository(path)
            # Transfer this isolated project's persisted state to the disk adapter.
            for name, value in vars(self.runtime.repo).items():
                if name != "lock":
                    setattr(local, name, value)
            self.runtime.repo = local
            await self.resolve(await self.checkpoint(), proposed_steps=[self.task("fast"), self.task("slow")])
            self.worker = asyncio.create_task(self.run_build())
            checkpoint = await self.checkpoint()
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            await local.close()
            self.runtime.repo = LocalJsonVideoProjectRepository(path)
            self.runtime._default_executor = self.executor
            self.executor.release.set()
            self.assertEqual(await self.runtime.recover_active_builds(), 1)
            workers = list(self.runtime._build_tasks.values())
            await asyncio.wait_for(asyncio.gather(*workers), 3)
            self.assertEqual(self.executor.calls.count("fast"), 1)
            self.assertEqual(self.executor.remote_resumes["slow"], "remote-slow")
            checkpoints = await self.runtime.repo.list_build_checkpoints(self.project.id, self.build.id)
            self.assertEqual(sum(c.id == checkpoint.id for c in checkpoints), 1)


if __name__ == "__main__":
    unittest.main()
