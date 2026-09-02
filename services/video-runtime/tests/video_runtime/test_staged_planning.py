from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.video_runtime.models import (
    CheckpointResolution, MediaArtifactVersion, ProjectIntent, RebuildPlan,
    RebuildPlanItem, ValidationResult,
)
from app.video_runtime.repository import PlanRevisionConflict
from app.video_runtime.checkpoint_coordinator import CheckpointCoordinator, checkpoint_prompt
from app.video_runtime.models import PlanCheckpoint
from app.video_runtime.runtime import _checkpoint_artifact_summary
from app.video_runtime.staged_planning import append_phase

from test_initial_build import FakePlanExecutor, video_runtime, video_spec


class _CheckpointRepo:
    def __init__(self, checkpoint: PlanCheckpoint) -> None:
        self.checkpoint = checkpoint
        self.failed: list[tuple[str, str]] = []

    async def claim_pending_checkpoint(self, _lease_seconds: int):
        value, self.checkpoint = self.checkpoint, None
        return value

    async def fail_checkpoint_delivery(self, checkpoint_id: str, error: str):
        self.failed.append((checkpoint_id, error))


class _CheckpointRuntime:
    def __init__(self, repo) -> None:
        self.repo = repo


class _DeepSeek:
    def __init__(self) -> None:
        self.prompts: list[tuple[str, str, str]] = []

    async def prompt(self, session_id: str, prompt: str, *, mode: str):
        self.prompts.append((session_id, prompt, mode))


class CheckpointCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_summary_keeps_bounded_generated_text(self) -> None:
        artifact = MediaArtifactVersion(
            project_id="project-1",
            artifact_id="story:main",
            type="story_draft",
            metadata={"content": "x" * 20_000, "generation_parameters": {"secret": True}},
        )
        summary = _checkpoint_artifact_summary(artifact)
        self.assertTrue(summary["metadata"]["content"]["truncated"])
        self.assertNotIn("generation_parameters", summary["metadata"])

    async def test_delivers_auditable_prompt_to_same_session(self) -> None:
        checkpoint = PlanCheckpoint(
            project_id="project-1", build_id="build-1", plan_id="plan-1",
            workflow_id="mv", session_id="session-1", user_id="user-1",
            phase="music_analysis", next_phase="visual_production",
            artifact_summaries=[{"id": "audio-1", "type": "audiomap", "duration": 58}],
            unresolved_sections=["shots", "captions"],
            base_plan_revision=1, base_spec_revision=1,
            delivery_attempts=1, delivery_id="delivery-1",
        )
        repo = _CheckpointRepo(checkpoint)
        deepseek = _DeepSeek()
        coordinator = CheckpointCoordinator(
            _CheckpointRuntime(repo), deepseek, poll_seconds=0.01,
        )
        self.assertTrue(await coordinator.run_once())
        self.assertEqual(deepseek.prompts[0][0], "session-1")
        self.assertEqual(deepseek.prompts[0][2], "queue")
        prompt = deepseek.prompts[0][1]
        self.assertIn("video_checkpoint_inspect", prompt)
        self.assertIn("video_checkpoint_resolve", prompt)
        self.assertIn('"duration": 58', prompt)
        self.assertNotIn("chain-of-thought", checkpoint_prompt(checkpoint).lower())

    async def test_semantic_repair_appends_one_new_media_branch(self) -> None:
        document = RebuildPlanItem(
            step_id="spec", action="create", capability="runtime.artifact.persist",
            output_artifact_type="video_spec",
        )
        clip = RebuildPlanItem(
            step_id="clip", action="create", capability="atomic.video.generate",
            output_artifact_type="video_clip", depends_on=["spec"],
        )
        validation = RebuildPlanItem(
            step_id="validate", action="validate", capability="cuti.continuity.validate",
            output_artifact_type="validation", depends_on=["clip"],
        )
        existing = RebuildPlan(
            project_id="project-1", base_project_version_id="version-1",
            workflow_id="seedance2", items=[document, clip, validation], schema_version=2,
        )
        full = existing.model_copy(deep=True)
        added, next_checkpoint = append_phase(
            existing_plan=existing, full_plan=full, workflow=None,
            checkpoint_id="semantic_validation",
        )
        self.assertIsNone(next_checkpoint)
        self.assertEqual(
            [item.step_id for item in added],
            ["clip-repair-1", "validate-repair-1"],
        )
        self.assertEqual(added[1].depends_on, ["clip-repair-1"])


class StagedPlanningRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.runtime = await video_runtime()
        self.runtime.staged_planning_enabled = True
        self.project, self.version = await self.runtime.create_project(
            user_id="user-1", title="Staged MV",
        )
        await self.runtime.bind_session(
            project_id=self.project.id,
            session_id="session-1",
            user_id="user-1",
        )

    async def asyncTearDown(self) -> None:
        await self.runtime.close()

    async def test_mv_waits_for_real_music_before_visual_plan(self) -> None:
        intent = ProjectIntent(
            title="Staged MV",
            brief="Create a city-night music video",
            target_duration_seconds=15,
            workflow_id="mv",
            workflow_parameters={"music_prompt": "quiet electronic pop"},
        )
        plan = await self.runtime.plan_project(
            project_id=self.project.id,
            base_project_version_id=self.version.id,
            project_intent=intent,
            idempotency_key="plan-1",
        )
        self.assertEqual(plan.schema_version, 2)
        self.assertEqual(
            [item.step_id for item in plan.items],
            ["intent", "music", "music-analysis", "music-cut"],
        )
        self.assertNotIn("shot-one-video", {item.step_id for item in plan.items})

        build = await self.runtime.start_build(
            project_id=self.project.id,
            plan_id=plan.id,
            base_project_version_id=self.version.id,
            idempotency_key="build-1",
            session_id="session-1",
            user_id="user-1",
        )
        executor = FakePlanExecutor()
        waiting, committed = await self.runtime.execute_build(
            project_id=self.project.id,
            build_id=build.id,
            executor=executor,
        )
        self.assertIsNone(committed)
        self.assertEqual(waiting.status, "waiting_agent")
        checkpoint = (await self.runtime.repo.list_build_checkpoints(
            self.project.id, build.id,
        ))[0]
        self.assertEqual(checkpoint.phase, "music_analysis")
        self.assertEqual(checkpoint.session_id, "session-1")
        self.assertEqual(
            {item["type"] for item in checkpoint.artifact_summaries},
            {"audiomap", "audio_cut"},
        )

        complete_spec = video_spec().model_copy(update={
            "workflow_id": "mv",
            "audio": video_spec().audio.model_copy(update={
                "bgm_prompt": "quiet electronic pop", "subtitles": False,
            }),
        })
        updated = await self.runtime.resolve_checkpoint(
            project_id=self.project.id,
            build_id=build.id,
            checkpoint_id=checkpoint.id,
            resolution=CheckpointResolution(
                base_plan_revision=1,
                base_spec_revision=1,
                idempotency_key="resolve-1",
                video_spec_patch={
                    "characters": [
                        item.model_dump(mode="json") for item in complete_spec.characters
                    ],
                    "shots": [item.model_dump(mode="json") for item in complete_spec.shots],
                    "audio": complete_spec.audio.model_dump(mode="json"),
                },
                reason="Plan shots from the measured track",
            ),
            session_id="session-1",
            user_id="user-1",
        )
        self.assertEqual(updated.current_revision, 2)
        self.assertIn("shot-one-video", {item.step_id for item in updated.items})
        resumed = await self.runtime.repo.get_build(self.project.id, build.id)
        self.assertEqual(resumed.status, "queued")

    async def test_checkpoint_rejects_stale_plan_revision(self) -> None:
        intent = ProjectIntent(
            title="Direct", brief="One cinematic shot", workflow_id="seedance2",
        )
        plan = await self.runtime.plan_project(
            project_id=self.project.id,
            base_project_version_id=self.version.id,
            project_intent=intent,
            idempotency_key="plan-direct",
        )
        build = await self.runtime.start_build(
            project_id=self.project.id,
            plan_id=plan.id,
            base_project_version_id=self.version.id,
            idempotency_key="build-direct",
            session_id="session-1",
            user_id="user-1",
        )
        await self.runtime.execute_build(
            project_id=self.project.id,
            build_id=build.id,
            executor=FakePlanExecutor(),
        )
        checkpoint = (await self.runtime.repo.list_build_checkpoints(
            self.project.id, build.id,
        ))[0]
        with self.assertRaises(PlanRevisionConflict):
            await self.runtime.resolve_checkpoint(
                project_id=self.project.id,
                build_id=build.id,
                checkpoint_id=checkpoint.id,
                resolution=CheckpointResolution(
                    base_plan_revision=2,
                    base_spec_revision=1,
                    idempotency_key="stale",
                    video_spec=video_spec().model_copy(update={"workflow_id": "seedance2"}),
                ),
                session_id="session-1",
                user_id="user-1",
            )

    async def test_keyframe_workflow_appends_references_then_keyframes_then_video(self) -> None:
        intent = ProjectIntent(
            title="Layered story",
            brief="A traveler catches the last train",
            target_duration_seconds=15,
            workflow_id="workflow-keyframe-pipeline",
        )
        plan = await self.runtime.plan_project(
            project_id=self.project.id,
            base_project_version_id=self.version.id,
            project_intent=intent,
            idempotency_key="plan-keyframe-phases",
        )
        build = await self.runtime.start_build(
            project_id=self.project.id,
            plan_id=plan.id,
            base_project_version_id=self.version.id,
            idempotency_key="build-keyframe-phases",
            session_id="session-1",
            user_id="user-1",
        )
        executor = FakePlanExecutor()

        waiting, committed = await self.runtime.execute_build(
            project_id=self.project.id, build_id=build.id, executor=executor,
        )
        self.assertIsNone(committed)
        checkpoint = (await self.runtime.repo.list_build_checkpoints(
            self.project.id, build.id,
        ))[-1]
        self.assertEqual(checkpoint.phase, "story_intent")

        complete_spec = video_spec().model_copy(update={
            "workflow_id": "workflow-keyframe-pipeline",
        })
        for expected_phase, expected_type, forbidden_type in (
            ("reference_production", "character_reference", "keyframe"),
            ("keyframe_production", "keyframe", "video_clip"),
        ):
            current_plan = await self.runtime.repo.get_plan(plan.id)
            current_spec = await self.runtime.repo.get_video_spec_revision(
                current_plan.video_spec_revision_id,
            )
            await self.runtime.resolve_checkpoint(
                project_id=self.project.id,
                build_id=build.id,
                checkpoint_id=checkpoint.id,
                resolution=CheckpointResolution(
                    base_plan_revision=current_plan.current_revision,
                    base_spec_revision=current_spec.revision,
                    idempotency_key=f"resolve-{expected_phase}",
                    video_spec=complete_spec,
                ),
                session_id="session-1",
                user_id="user-1",
            )
            updated = await self.runtime.repo.get_plan(plan.id)
            new_items = [
                item for item in updated.items
                if item.step_id not in {prior.step_id for prior in current_plan.items}
            ]
            self.assertIn(expected_type, {item.output_artifact_type for item in new_items})
            self.assertNotIn(forbidden_type, {item.output_artifact_type for item in new_items})
            waiting, committed = await self.runtime.execute_build(
                project_id=self.project.id, build_id=build.id, executor=executor,
            )
            self.assertIsNone(committed)
            checkpoint = (await self.runtime.repo.list_build_checkpoints(
                self.project.id, build.id,
            ))[-1]
            self.assertEqual(checkpoint.phase, expected_phase)

        current_plan = await self.runtime.repo.get_plan(plan.id)
        current_spec = await self.runtime.repo.get_video_spec_revision(
            current_plan.video_spec_revision_id,
        )
        final_plan = await self.runtime.resolve_checkpoint(
            project_id=self.project.id,
            build_id=build.id,
            checkpoint_id=checkpoint.id,
            resolution=CheckpointResolution(
                base_plan_revision=current_plan.current_revision,
                base_spec_revision=current_spec.revision,
                idempotency_key="resolve-video-production",
                video_spec=complete_spec,
            ),
            session_id="session-1",
            user_id="user-1",
        )
        self.assertIsNone(final_plan.next_checkpoint)
        self.assertIn(
            "video_clip", {item.output_artifact_type for item in final_plan.items},
        )

    async def test_semantic_failure_pauses_once_for_agent_repair(self) -> None:
        intent = ProjectIntent(
            title="Continuity repair",
            brief="A traveler catches the last train",
            target_duration_seconds=15,
            workflow_id="seedance2",
        )
        plan = await self.runtime.plan_project(
            project_id=self.project.id,
            base_project_version_id=self.version.id,
            project_intent=intent,
            idempotency_key="plan-semantic-repair",
        )
        build = await self.runtime.start_build(
            project_id=self.project.id,
            plan_id=plan.id,
            base_project_version_id=self.version.id,
            idempotency_key="build-semantic-repair",
            session_id="session-1",
            user_id="user-1",
        )
        executor = FakePlanExecutor()
        await self.runtime.execute_build(
            project_id=self.project.id, build_id=build.id, executor=executor,
        )
        creative = (await self.runtime.repo.list_build_checkpoints(
            self.project.id, build.id,
        ))[-1]
        complete_spec = video_spec().model_copy(update={"workflow_id": "seedance2"})
        await self.runtime.resolve_checkpoint(
            project_id=self.project.id,
            build_id=build.id,
            checkpoint_id=creative.id,
            resolution=CheckpointResolution(
                base_plan_revision=1,
                base_spec_revision=1,
                idempotency_key="resolve-semantic-production",
                video_spec=complete_spec,
            ),
            session_id="session-1",
            user_id="user-1",
        )

        should_fail = True

        async def validate(*, build_id, artifact):
            return [ValidationResult(
                project_id=self.project.id,
                build_id=build_id,
                artifact_version_id=artifact.id,
                validator_id="cuti.continuity-validator",
                passed=not should_fail,
                issues=["character clothing drift"] if should_fail else [],
            )]

        self.runtime.validate_artifact = validate
        waiting, committed = await self.runtime.execute_build(
            project_id=self.project.id, build_id=build.id, executor=executor,
        )
        self.assertIsNone(committed)
        self.assertEqual(waiting.status, "waiting_agent")
        repair = (await self.runtime.repo.list_build_checkpoints(
            self.project.id, build.id,
        ))[-1]
        self.assertEqual(repair.phase, "semantic_validation")
        self.assertEqual(repair.semantic_repair_attempts, 1)

        should_fail = False
        current_plan = await self.runtime.repo.get_plan(plan.id)
        current_spec = await self.runtime.repo.get_video_spec_revision(
            current_plan.video_spec_revision_id,
        )
        repaired_plan = await self.runtime.resolve_checkpoint(
            project_id=self.project.id,
            build_id=build.id,
            checkpoint_id=repair.id,
            resolution=CheckpointResolution(
                base_plan_revision=current_plan.current_revision,
                base_spec_revision=current_spec.revision,
                idempotency_key="resolve-semantic-repair",
                video_spec=complete_spec,
                reason="Keep the same identity and correct the clothing prompt",
            ),
            session_id="session-1",
            user_id="user-1",
        )
        self.assertTrue(any(
            item.step_id.endswith("-repair-1") for item in repaired_plan.items
        ))
        completed, version = await self.runtime.execute_build(
            project_id=self.project.id, build_id=build.id, executor=executor,
        )
        self.assertEqual(completed.status, "completed")
        self.assertIsNotNone(version)
