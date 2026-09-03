from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.video_runtime.initial_build import BuildPlanValidationError, topological_steps
from app.video_runtime.builtin_plugins.music_workflows import (
    LipsyncMusicVideoWorkflowPlugin,
    MusicVideoWorkflowPlugin,
)
from app.video_runtime.builtin_plugins.continuity_validator import ContinuityValidatorPlugin
from app.video_runtime.models import (
    MediaEditOperation,
    MediaArtifactVersion,
    RebuildPlan,
    RebuildPlanItem,
    ValidationResult,
    VideoSpec,
)
from app.video_runtime.runtime import VideoBuildRuntime
from app.video_runtime.plugins import PluginContext
from app.video_runtime.skill_workflows import load_workflow_skills


async def video_runtime(
    *, max_parallel_generation_tasks: int | None = None,
) -> VideoBuildRuntime:
    runtime = VideoBuildRuntime(
        max_parallel_generation_tasks=max_parallel_generation_tasks,
    )
    await runtime.plugins.load_directories([
        Path(__file__).resolve().parents[2] / "plugins",
    ])
    await load_workflow_skills(runtime.plugins, runtime.skills)
    return runtime


def video_spec() -> VideoSpec:
    return VideoSpec.model_validate({
        "title": "A fifteen second story",
        "language": "zh-CN",
        "target_duration_seconds": 15,
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "workflow_id": "cuti.seedance-story",
        "style_id": "cuti.cinematic",
        "workflow_parameters": {
            "scenes": [{
                "id": "station",
                "name": "Railway station",
                "description": "A quiet night railway station with a clock and blue platform lights",
            }],
        },
        "characters": [{
            "id": "hero", "name": "Hero", "appearance": "short black hair",
            "clothing": "blue coat",
        }],
        "shots": [
            {
                "id": "one", "order": 1, "duration_seconds": 5,
                "beat": "arrives", "visual_prompt": "0-5秒，主角抵达车站，镜头平稳推进。",
                "narration": "他抵达车站。", "character_ids": ["hero"],
            },
            {
                "id": "two", "order": 2, "duration_seconds": 5,
                "beat": "waits", "visual_prompt": "0-5秒，主角在时钟下等待，灯光缓慢变化。",
                "narration": "时间慢慢过去。", "character_ids": ["hero"],
            },
            {
                "id": "three", "order": 3, "duration_seconds": 5,
                "beat": "leaves", "visual_prompt": "0-5秒，主角登上列车，镜头随动作收束。",
                "narration": "列车终于到来。", "character_ids": ["hero"],
            },
        ],
        "audio": {
            "narration_voice": "Wise_Woman", "bgm_prompt": "quiet cinematic piano",
            "subtitles": True,
        },
        "providers": {"video": "seedance-2.0", "image": "gpt-image-2", "music": "suno"},
        "automation": {"mode": "automatic", "max_artifact_retries": 1},
    })


class FakePlanExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.remote_resumes: dict[str, str] = {}

    async def execute_plan_step(
        self, *, build, step, completed_artifacts, idempotency_key,
        report_remote_operation=None,
    ) -> MediaArtifactVersion:
        self.calls.append(step.step_id)
        if step.parameters.get("remote_operation_id"):
            self.remote_resumes[step.step_id] = step.parameters["remote_operation_id"]
        if step.output_artifact_type == "video_clip" and report_remote_operation:
            await report_remote_operation(f"remote-{step.step_id}", "fake-provider")
        metadata = {}
        metadata.update({
            "generation_parameters": dict(step.parameters),
            "rebuild_capability": step.capability,
            "plan_step_id": step.step_id,
        })
        if step.output_artifact_type == "video_spec":
            metadata["content"] = step.parameters["content"]
        if step.output_artifact_type == "timeline":
            cursor = 0.0
            items = []
            for shot, dependency in zip(step.parameters["shots"], step.parameters["video_steps"], strict=True):
                artifact = completed_artifacts[dependency]
                items.append({
                    "artifactVersionId": artifact.id,
                    "startSeconds": cursor,
                    "durationSeconds": shot["duration_seconds"],
                })
                cursor += shot["duration_seconds"]
            metadata["timeline"] = {"items": items, "durationSeconds": cursor}
        return MediaArtifactVersion(
            project_id=build.project_id,
            type=step.output_artifact_type,
            uri=None if step.output_artifact_type in {"video_spec", "script", "characters", "storyboard", "timeline"}
            else f"https://media.test/{step.step_id}",
            metadata=metadata,
            provenance={"idempotency_key": idempotency_key},
        )

    async def rebuild_artifact(
        self, *, build, source, completed_replacements, idempotency_key,
    ) -> MediaArtifactVersion:
        parameters = dict(source.metadata.get("rebuild_parameters") or {})
        metadata = {
            **source.metadata,
            "generation_parameters": parameters,
            "rebuild_capability": source.metadata.get("rebuild_capability"),
            "plan_step_id": source.metadata.get("plan_step_id"),
        }
        if source.type == "video_spec":
            metadata["content"] = parameters["content"]
        return MediaArtifactVersion(
            project_id=build.project_id,
            artifact_id=source.artifact_id,
            type=source.type,
            uri=source.uri,
            metadata=metadata,
            provenance={"idempotency_key": idempotency_key},
        )


class ConcurrencyProbeExecutor(FakePlanExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.active_group_steps = 0
        self.max_active_group_steps = 0

    async def execute_plan_step(
        self, *, build, step, completed_artifacts, idempotency_key,
        report_remote_operation=None,
    ) -> MediaArtifactVersion:
        if not step.execution_group:
            return await super().execute_plan_step(
                build=build,
                step=step,
                completed_artifacts=completed_artifacts,
                idempotency_key=idempotency_key,
                report_remote_operation=report_remote_operation,
            )
        self.active_group_steps += 1
        self.max_active_group_steps = max(
            self.max_active_group_steps,
            self.active_group_steps,
        )
        try:
            # Give sibling tasks a deterministic chance to enter the provider
            # call. A serial executor can never observe more than one here.
            await asyncio.sleep(0.02)
            return await super().execute_plan_step(
                build=build,
                step=step,
                completed_artifacts=completed_artifacts,
                idempotency_key=idempotency_key,
                report_remote_operation=report_remote_operation,
            )
        finally:
            self.active_group_steps -= 1


class FailOneParallelExecutor(FakePlanExecutor):
    def __init__(self, failed_step_id: str) -> None:
        super().__init__()
        self.failed_step_id = failed_step_id

    async def execute_plan_step(
        self, *, build, step, completed_artifacts, idempotency_key,
        report_remote_operation=None,
    ) -> MediaArtifactVersion:
        if step.execution_group:
            await asyncio.sleep(0.02)
        if step.step_id == self.failed_step_id:
            self.calls.append(step.step_id)
            raise RuntimeError("deterministic parallel test failure")
        return await super().execute_plan_step(
            build=build,
            step=step,
            completed_artifacts=completed_artifacts,
            idempotency_key=idempotency_key,
            report_remote_operation=report_remote_operation,
        )


class ContaminatedSceneExecutor(FakePlanExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.video_started = False

    async def execute_plan_step(
        self, *, build, step, completed_artifacts, idempotency_key,
        report_remote_operation=None,
    ) -> MediaArtifactVersion:
        if step.output_artifact_type == "video_clip":
            self.video_started = True
        artifact = await super().execute_plan_step(
            build=build,
            step=step,
            completed_artifacts=completed_artifacts,
            idempotency_key=idempotency_key,
            report_remote_operation=report_remote_operation,
        )
        if step.step_id == "scene-setting-reference":
            artifact.metadata.update({
                "final_prompt": step.parameters["prompt"] + " Add the product and cast.",
                "resolved_generation_parameters": {
                    **step.parameters,
                    "images": ["https://media.test/product.png"],
                },
                "skill_prompt_applied": True,
            })
        return artifact

class MusicWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def test_music_workflow_uses_independent_music_timeline(self):
        spec = video_spec()
        identity = MediaArtifactVersion(
            id="identity-version-1",
            artifact_id="source:identity",
            project_id="project-1",
            type="source_image",
            uri="https://media.test/identity.png",
        )
        spec = spec.model_copy(update={
            "workflow_id": "cuti.music-video",
            "providers": spec.providers.model_copy(update={"video": "minimax-h3"}),
            "source_asset_ids": [identity.artifact_id],
            "audio": spec.audio.model_copy(update={"subtitles": False}),
        })
        plan = await MusicVideoWorkflowPlugin().compile_build_plan(
            PluginContext(project_id="project-1", values={
                "base_project_version_id": "version-1",
                "source_artifacts": {identity.artifact_id: identity},
            }),
            spec,
        )
        ordered = topological_steps(plan.items)
        self.assertEqual(plan.workflow_id, "cuti.music-video")
        step_ids = [item.step_id for item in ordered]
        self.assertIn("music", step_ids)
        self.assertIn("music-analysis", step_ids)
        self.assertIn("music-cut", step_ids)
        self.assertNotIn("research", step_ids)
        self.assertIn("character-hero-reference", step_ids)
        self.assertNotIn("look", step_ids)
        self.assertNotIn("shot-one-tail", step_ids)
        music = next(item for item in plan.items if item.step_id == "music")
        self.assertEqual(music.capability, "suno.generate")
        mv_clip = next(
            item for item in plan.items
            if item.step_id.endswith("-video")
            and item.capability == "api.provider.generate"
        )
        self.assertEqual(mv_clip.parameters["model"], "minimax-h3")
        self.assertEqual(mv_clip.parameters["prompt"], spec.shots[0].visual_prompt)
        self.assertNotIn("@音频1", mv_clip.parameters["prompt"])
        self.assertFalse(mv_clip.parameters["generate_audio"])
        self.assertEqual(mv_clip.parameters["audio_reference_from_step"], "music-cut")
        self.assertEqual(
            mv_clip.parameters["reference_from_steps"],
            ["character-hero-reference", "source-1"],
        )
        mixed = next(item for item in plan.items if item.capability == "media.mix_audio")
        self.assertEqual(mixed.parameters["mode"], "replace")
        self.assertEqual(mixed.parameters["audio_step"], "music-cut")
        final = next(item for item in plan.items if item.step_id == "final-video")
        self.assertEqual(final.capability, "media.mix_audio")
        self.assertEqual(len(ordered), len(plan.items))

    async def test_lipsync_workflow_rewires_final_media_steps(self):
        spec = video_spec().model_copy(update={"workflow_id": "cuti.lipsync-music-video"})
        plan = await LipsyncMusicVideoWorkflowPlugin().compile_build_plan(
            PluginContext(project_id="project-1", values={"base_project_version_id": "version-1"}),
            spec,
        )
        ordered = topological_steps(plan.items)
        positions = {item.step_id: index for index, item in enumerate(ordered)}
        self.assertLess(positions["assembled-video"], positions["music-video"])
        self.assertLess(positions["music-video"], positions["final-video"])
        final = next(item for item in plan.items if item.step_id == "final-video")
        self.assertEqual(final.capability, "media.lipsync")
        self.assertEqual(final.parameters["video_step"], "music-video")


class FakeRebuildExecutor:
    async def rebuild_artifact(
        self, *, build, source, completed_replacements, idempotency_key,
    ) -> MediaArtifactVersion:
        parameters = dict(source.metadata.get("rebuild_parameters") or {})
        return MediaArtifactVersion(
            project_id=build.project_id,
            artifact_id=source.artifact_id,
            type=source.type,
            uri=source.uri,
            metadata={
                **source.metadata,
                "content": parameters.get("content", source.metadata.get("content")),
                "generation_parameters": parameters,
                "rebuild_capability": source.metadata.get("rebuild_capability"),
                "plan_step_id": source.metadata.get("plan_step_id"),
            },
            provenance={"idempotency_key": idempotency_key},
        )


class InitialBuildTest(unittest.IsolatedAsyncioTestCase):
    def test_video_spec_requires_explicit_workflow_selection(self) -> None:
        raw = video_spec().model_dump(mode="json")
        raw.pop("workflow_id")
        with self.assertRaisesRegex(ValueError, "workflow_id"):
            VideoSpec.model_validate(raw)

    async def test_scene_reference_isolation_fails_before_video_generation(self):
        runtime = await video_runtime()
        project, version = await runtime.create_project(
            user_id="user", title="Scene isolation",
        )
        prompt = (
            "Create exactly one unoccupied environment-only setting reference. "
            "Render a single coherent wide architectural view."
        )
        plan = RebuildPlan(
            project_id=project.id,
            kind="initial",
            base_project_version_id=version.id,
            workflow_id="cuti-scenario-product-workflow",
            items=[
                RebuildPlanItem(
                    step_id="scene-setting-reference",
                    action="create",
                    capability="atomic.image.generate",
                    output_artifact_id="scene:main:reference",
                    output_artifact_type="image",
                    parameters={
                        "prompt": prompt,
                        "artifact_role": "scene_setting_reference",
                    },
                ),
                RebuildPlanItem(
                    step_id="validate-setting-references",
                    action="validate",
                    capability="cuti.continuity.validate",
                    output_artifact_type="setting_reference_validation",
                    depends_on=["scene-setting-reference"],
                ),
                RebuildPlanItem(
                    step_id="shot-one-video",
                    action="create",
                    capability="atomic.video.generate",
                    output_artifact_id="shot:one:clip",
                    output_artifact_type="video_clip",
                    depends_on=[
                        "scene-setting-reference",
                        "validate-setting-references",
                    ],
                    execution_group="scenario-product-segments",
                    max_parallelism=2,
                ),
            ],
        )
        plan = await runtime.repo.save_plan(plan, "scene-isolation-plan")
        build = await runtime.start_build(
            project_id=project.id,
            plan_id=plan.id,
            base_project_version_id=version.id,
            idempotency_key="scene-isolation-build",
        )
        validator = ContinuityValidatorPlugin()

        async def validate(*, build_id, artifact):
            return await validator.validate_artifact(
                PluginContext(project_id=project.id, build_id=build_id), artifact,
            )

        runtime.validate_artifact = validate
        executor = ContaminatedSceneExecutor()
        with self.assertRaisesRegex(ValueError, "initial build validation failed"):
            await runtime.execute_build(
                project_id=project.id,
                build_id=build.id,
                executor=executor,
            )
        self.assertFalse(executor.video_started)
        states = {
            item.plan_step_id: item
            for item in await runtime.repo.list_build_steps(project.id, build.id)
        }
        self.assertEqual(states["validate-setting-references"].status, "failed")
        self.assertEqual(states["shot-one-video"].status, "pending")

    async def test_declared_short_drama_segments_execute_in_parallel(self):
        runtime = await video_runtime(max_parallel_generation_tasks=2)
        project, version = await runtime.create_project(user_id="user", title="Parallel drama")
        base = video_spec()
        spec = base.model_copy(deep=True, update={
            "workflow_id": "short-drama-workflow",
            "target_duration_seconds": 45,
            "shots": [
                shot.model_copy(update={"duration_seconds": 15})
                for shot in base.shots
            ],
            "audio": base.audio.model_copy(update={"subtitles": False}),
            "providers": base.providers.model_copy(update={"video": "seedance-2.5"}),
        })
        plan = await runtime.plan_project(
            project_id=project.id,
            base_project_version_id=version.id,
            video_spec=spec,
            idempotency_key="parallel-drama-plan",
        )
        clips = [item for item in plan.items if item.output_artifact_type == "video_clip"]
        self.assertEqual(len(clips), 3)
        self.assertTrue(all(item.execution_group == "short-drama-segments" for item in clips))

        build = await runtime.start_build(
            project_id=project.id,
            plan_id=plan.id,
            base_project_version_id=version.id,
            idempotency_key="parallel-drama-build",
        )

        async def validate(*, build_id, artifact):
            return [ValidationResult(
                project_id=project.id,
                build_id=build_id,
                artifact_version_id=artifact.id,
                validator_id="test",
                passed=True,
            )]

        runtime.validate_artifact = validate
        executor = ConcurrencyProbeExecutor()
        completed, committed = await runtime.execute_build(
            project_id=project.id,
            build_id=build.id,
            executor=executor,
        )
        self.assertEqual(completed.status, "completed")
        self.assertIsNotNone(committed)
        # The Workflow declares all three clips independent, while the
        # deployment-level Cuti safety cap limits live provider work to two.
        self.assertEqual(executor.max_active_group_steps, 2)

    async def test_parallel_failure_preserves_successful_segments_for_retry(self):
        runtime = await video_runtime(max_parallel_generation_tasks=2)
        project, version = await runtime.create_project(user_id="user", title="Recover drama")
        base = video_spec()
        spec = base.model_copy(deep=True, update={
            "workflow_id": "short-drama-workflow",
            "target_duration_seconds": 45,
            "shots": [
                shot.model_copy(update={"duration_seconds": 15})
                for shot in base.shots
            ],
            "audio": base.audio.model_copy(update={"subtitles": False}),
            "providers": base.providers.model_copy(update={"video": "seedance-2.5"}),
        })
        plan = await runtime.plan_project(
            project_id=project.id,
            base_project_version_id=version.id,
            video_spec=spec,
            idempotency_key="recover-parallel-plan",
        )
        build = await runtime.start_build(
            project_id=project.id,
            plan_id=plan.id,
            base_project_version_id=version.id,
            idempotency_key="recover-parallel-build",
        )
        with self.assertRaisesRegex(RuntimeError, "parallel test failure"):
            await runtime.execute_build(
                project_id=project.id,
                build_id=build.id,
                executor=FailOneParallelExecutor("shot-two-video"),
            )
        failed_states = {
            item.plan_step_id: item
            for item in await runtime.repo.list_build_steps(project.id, build.id)
        }
        self.assertEqual(failed_states["shot-one-video"].status, "completed")
        self.assertEqual(failed_states["shot-two-video"].status, "failed")
        self.assertEqual(failed_states["shot-three-video"].status, "completed")

        await runtime.retry_failed_build(project_id=project.id, build_id=build.id)

        async def validate(*, build_id, artifact):
            return [ValidationResult(
                project_id=project.id,
                build_id=build_id,
                artifact_version_id=artifact.id,
                validator_id="test",
                passed=True,
            )]

        runtime.validate_artifact = validate
        retry_executor = FakePlanExecutor()
        completed, committed = await runtime.execute_build(
            project_id=project.id,
            build_id=build.id,
            executor=retry_executor,
        )
        self.assertEqual(completed.status, "completed")
        self.assertIsNotNone(committed)
        self.assertIn("shot-two-video", retry_executor.calls)
        self.assertNotIn("shot-one-video", retry_executor.calls)
        self.assertNotIn("shot-three-video", retry_executor.calls)

    async def test_failed_build_retry_preserves_completed_steps(self):
        runtime = await video_runtime()
        project, version = await runtime.create_project(user_id="user", title="Retry film")
        plan = await runtime.plan_project(
            project_id=project.id,
            base_project_version_id=version.id,
            video_spec=video_spec(),
            idempotency_key="retry-plan",
        )
        build = await runtime.start_build(
            project_id=project.id,
            plan_id=plan.id,
            base_project_version_id=version.id,
            idempotency_key="retry-build",
        )
        steps = await runtime.repo.list_build_steps(project.id, build.id)
        steps[0].status = "completed"
        await runtime.repo.update_build_step(steps[0])
        steps[1].status = "failed"
        steps[1].attempt = 3
        steps[1].error = "temporary provider failure"
        steps[1].remote_operation_id = "terminal-provider-job"
        steps[1].remote_provider = "fake-provider"
        await runtime.repo.update_build_step(steps[1])
        steps[2].status = "running"
        steps[2].attempt = 3
        steps[2].remote_operation_id = "orphaned-provider-job"
        steps[2].remote_provider = "fake-provider"
        await runtime.repo.update_build_step(steps[2])
        build.status = "failed"
        build.error = steps[1].error
        await runtime.repo.update_build(build)

        retried = await runtime.retry_failed_build(project_id=project.id, build_id=build.id)
        states = await runtime.repo.list_build_steps(project.id, build.id)
        self.assertEqual(retried.status, "queued")
        self.assertIsNone(retried.error)
        self.assertEqual(states[0].status, "completed")
        self.assertEqual(states[1].status, "pending")
        self.assertEqual(states[1].attempt, 0)
        self.assertIsNone(states[1].error)
        self.assertIsNone(states[1].remote_operation_id)
        self.assertIsNone(states[1].remote_provider)
        self.assertEqual(states[2].status, "pending")
        self.assertEqual(states[2].attempt, 3)
        self.assertEqual(states[2].remote_operation_id, "orphaned-provider-job")
        self.assertEqual(states[2].remote_provider, "fake-provider")

    async def test_initial_plan_executes_and_commits_once(self):
        runtime = await video_runtime()
        project, version = await runtime.create_project(user_id="user", title="Film")
        plan = await runtime.plan_project(
            project_id=project.id,
            base_project_version_id=version.id,
            video_spec=video_spec(),
            idempotency_key="plan-one",
        )
        self.assertEqual(plan.kind, "initial")
        self.assertEqual(len(plan.video_spec.shots), 3)
        self.assertGreater(plan.estimated_cost, 0)
        second_keyframe = next(item for item in plan.items if item.step_id == "shot-two-keyframe")
        self.assertIn("shot-one-tail", second_keyframe.depends_on)
        self.assertIn(
            "keyframe-director",
            [item.skill_id for item in second_keyframe.resolved_skills],
        )
        self.assertIsNotNone(second_keyframe.skill_context)

        build = await runtime.start_build(
            project_id=project.id, plan_id=plan.id,
            base_project_version_id=version.id, idempotency_key="build-one",
        )
        step_states = await runtime.repo.list_build_steps(project.id, build.id)
        video_state = next(item for item in step_states if item.plan_step_id == "shot-one-video")
        self.assertIn(
            "video-director",
            [item.skill_id for item in video_state.resolved_skills],
        )
        video_state.status = "waiting_external"
        # Reconciliation must remain possible after local retry accounting has
        # been exhausted; polling an existing job is not a new paid attempt.
        video_state.attempt = 99
        video_state.remote_operation_id = "existing-provider-job"
        video_state.remote_provider = "fake-provider"
        await runtime.repo.update_build_step(video_state)

        async def validate(*, build_id, artifact):
            if artifact.type not in {"timeline", "video_clip", "final_video"}:
                return []
            return [ValidationResult(
                project_id=project.id, build_id=build_id,
                artifact_version_id=artifact.id, validator_id="test", passed=True,
            )]

        runtime.validate_artifact = validate
        executor = FakePlanExecutor()
        completed, committed = await runtime.execute_build(
            project_id=project.id, build_id=build.id, executor=executor,
        )
        self.assertEqual(completed.status, "completed")
        self.assertGreater(completed.actual_cost, 0)
        self.assertEqual(executor.remote_resumes["shot-one-video"], "existing-provider-job")
        self.assertNotEqual(committed.id, version.id)
        self.assertEqual(len(await runtime.repo.current_artifacts(project.id)), len(executor.calls))
        repeated, repeated_version = await runtime.execute_build(
            project_id=project.id, build_id=build.id, executor=executor,
        )
        self.assertEqual(repeated.id, completed.id)
        self.assertEqual(repeated_version.id, committed.id)

        edited = await runtime.preview_edits(
            project_id=project.id,
            base_project_version_id=committed.id,
            edits=[{
                "type": "patch_shot", "id": "two",
                "patch": {"visual_prompt": "Hero waits beneath a red clock"},
            }],
            idempotency_key="edit-shot-two",
        )
        rebuilt_steps = {
            item.step_id: item for item in edited.items if item.action == "rebuild"
        }
        self.assertEqual(len({item.step_id for item in edited.items}), len(edited.items))
        self.assertNotIn("shot-one-keyframe", rebuilt_steps)
        self.assertEqual(
            rebuilt_steps["shot-two-keyframe"].parameters["prompt"],
            "Hero waits beneath a red clock",
        )
        self.assertIn("storyboard", rebuilt_steps["shot-two-keyframe"].depends_on)
        self.assertIn("shot-one-tail", rebuilt_steps["shot-two-keyframe"].depends_on)
        self.assertEqual(edited.video_spec.shots[1].visual_prompt, "Hero waits beneath a red clock")
        edit_build = await runtime.start_build(
            project_id=project.id,
            plan_id=edited.id,
            base_project_version_id=committed.id,
            idempotency_key="edit-shot-two-build",
        )
        completed_edit, edited_version = await runtime.execute_build(
            project_id=project.id,
            build_id=edit_build.id,
            executor=FakeRebuildExecutor(),
        )
        self.assertEqual(completed_edit.status, "completed")
        self.assertNotEqual(edited_version.id, committed.id)
        step_states = await runtime.repo.list_build_steps(project.id, edit_build.id)
        self.assertTrue(all(item.status == "completed" for item in step_states))
        current_spec = next(
            item for item in await runtime.repo.current_artifacts(project.id)
            if item.type == "video_spec"
        )
        self.assertEqual(
            current_spec.metadata["content"]["shots"][1]["visual_prompt"],
            "Hero waits beneath a red clock",
        )

    async def test_media_edits_create_missing_music_and_rebuild_timeline_order(self):
        runtime = await video_runtime()
        project, version = await runtime.create_project(user_id="user", title="Silent film")
        spec = video_spec().model_copy(deep=True)
        spec.audio.bgm_prompt = ""
        spec.audio.subtitles = False
        for shot in spec.shots:
            shot.narration = ""
        initial = await runtime.plan_project(
            project_id=project.id,
            base_project_version_id=version.id,
            video_spec=spec,
            idempotency_key="silent-plan",
        )
        build = await runtime.start_build(
            project_id=project.id,
            plan_id=initial.id,
            base_project_version_id=version.id,
            idempotency_key="silent-build",
        )
        async def validate(*, build_id, artifact):
            if artifact.type not in {"timeline", "video_clip", "video_assembled"}:
                return []
            return [ValidationResult(
                project_id=project.id,
                build_id=build_id,
                artifact_version_id=artifact.id,
                validator_id="test",
                passed=True,
            )]

        runtime.validate_artifact = validate
        _completed, committed = await runtime.execute_build(
            project_id=project.id,
            build_id=build.id,
            executor=FakePlanExecutor(),
        )

        music = await runtime.preview_edits(
            project_id=project.id,
            base_project_version_id=committed.id,
            edits=[{"type": "replace_music", "prompt": "warm acoustic guitar"}],
            idempotency_key="add-music",
        )
        music_steps = {item.step_id: item for item in music.items}
        self.assertEqual(music_steps["bgm"].action, "create")
        self.assertEqual(music_steps["bgm"].parameters["prompt"], "warm acoustic guitar")
        self.assertEqual(music_steps["mix-bgm"].action, "create")
        self.assertIn("bgm", music_steps["mix-bgm"].depends_on)
        self.assertEqual(music_steps["validate-final"].action, "validate")
        self.assertEqual(music.estimated_cost, 0.11)
        topological_steps(music.items)
        music_build = await runtime.start_build(
            project_id=project.id,
            plan_id=music.id,
            base_project_version_id=committed.id,
            idempotency_key="add-music-build",
        )
        completed_music, music_version = await runtime.execute_build(
            project_id=project.id,
            build_id=music_build.id,
            executor=FakePlanExecutor(),
        )
        self.assertEqual(completed_music.status, "completed")
        selected_after_music = await runtime.repo.current_artifacts(project.id)
        self.assertTrue(any(item.type == "audio_bgm" for item in selected_after_music))
        self.assertTrue(any(item.type == "video_mixed" for item in selected_after_music))

        timeline = await runtime.preview_edits(
            project_id=project.id,
            base_project_version_id=music_version.id,
            edits=[{
                "type": "patch_timeline",
                "patch": {"shots": [
                    {"id": "one", "order": 3},
                    {"id": "two", "order": 1},
                    {"id": "three", "order": 2},
                ]},
            }],
            idempotency_key="reorder-timeline",
        )
        timeline_steps = {item.step_id: item for item in timeline.items}
        self.assertEqual([shot.id for shot in timeline.video_spec.shots], ["two", "three", "one"])
        self.assertEqual(timeline_steps["timeline"].action, "rebuild")
        self.assertEqual(timeline_steps["assembled-video"].action, "rebuild")
        self.assertEqual(timeline_steps["shot-one-video"].action, "reuse")
        self.assertEqual(
            timeline_steps["assembled-video"].parameters["video_steps"],
            ["shot-two-video", "shot-three-video", "shot-one-video"],
        )
        topological_steps(timeline.items)

    async def test_workflow_free_media_plan_patch_adds_subtitles_to_any_video(self):
        runtime = await video_runtime()
        project, version = await runtime.create_project(user_id="user", title="Any workflow")
        selected_video = await runtime.repo.add_artifact(MediaArtifactVersion(
            artifact_id=f"{project.id}:final-video",
            project_id=project.id,
            type="final_video",
            uri="https://media.test/original.mp4",
            title="Original final video",
            metadata={"plan_step_id": "final-video"},
        ))
        project = await runtime.repo.get_project(project.id)

        catalog = {item.capability: item for item in runtime.plan_patch_capability_catalog()}
        self.assertIn("media.transcribe", catalog)
        self.assertIn("subtitle.compose", catalog)
        self.assertIn("media.subtitle_burn", catalog)

        plan = await runtime.preview_plan_patch(
            project_id=project.id,
            base_project_version_id=project.current_version_id,
            description="Add clean source-language subtitles",
            idempotency_key="caption-preview",
            operations=[
                MediaEditOperation.model_validate({
                    "step_id": "transcribe",
                    "capability": "media.transcribe",
                    "inputs": [{
                        "role": "video", "artifact_version_id": selected_video.id,
                    }],
                }),
                MediaEditOperation.model_validate({
                    "step_id": "captions",
                    "capability": "subtitle.compose",
                    "inputs": [{
                        "role": "transcript", "operation_step_id": "transcribe",
                    }],
                    "parameters": {"format": "srt", "max_lines": 2},
                }),
                MediaEditOperation.model_validate({
                    "step_id": "burn-captions",
                    "capability": "media.subtitle_burn",
                    "inputs": [
                        {"role": "video", "artifact_version_id": selected_video.id},
                        {"role": "subtitle", "operation_step_id": "captions"},
                    ],
                    "parameters": {
                        "style_preset": "clean", "position": "bottom-safe",
                    },
                }),
            ],
        )
        by_step = {item.step_id: item for item in plan.items}
        self.assertEqual(plan.workflow_id, "")
        self.assertIsNone(plan.video_spec)
        self.assertEqual(by_step["transcribe"].depends_on, ["source-1"])
        self.assertEqual(by_step["captions"].depends_on, ["transcribe"])
        self.assertEqual(
            by_step["burn-captions"].depends_on,
            ["source-1", "captions"],
        )
        self.assertEqual(by_step["burn-captions"].action, "rebuild")
        self.assertEqual(
            by_step["burn-captions"].artifact_version_id, selected_video.id,
        )
        self.assertEqual(
            [item.skill_id for item in by_step["transcribe"].resolved_skills],
            ["subtitle-authoring"],
        )
        self.assertEqual(
            [item.skill_id for item in by_step["captions"].resolved_skills],
            ["subtitle-authoring"],
        )
        self.assertEqual(
            [item.skill_id for item in by_step["burn-captions"].resolved_skills],
            ["subtitle-authoring"],
        )
        self.assertEqual(
            [item.skill_id for item in runtime.plan_patch_capability_catalog() if item.skill_id],
            ["hyperframes-captions", "subtitle-authoring", "subtitle-authoring", "subtitle-authoring"],
        )
        topological_steps(plan.items)

        build = await runtime.start_build(
            project_id=project.id,
            plan_id=plan.id,
            base_project_version_id=project.current_version_id,
            idempotency_key="caption-build",
        )
        completed, new_version = await runtime.execute_build(
            project_id=project.id,
            build_id=build.id,
            executor=FakePlanExecutor(),
        )
        self.assertEqual(completed.status, "completed")
        self.assertIsNotNone(new_version)
        current = await runtime.repo.current_artifacts(project.id)
        captioned = next(item for item in current if item.artifact_id == selected_video.artifact_id)
        self.assertNotEqual(captioned.id, selected_video.id)
        self.assertEqual(captioned.version, selected_video.version + 1)

    async def test_dynamic_media_patch_rejects_direct_urls_and_unselected_artifacts(self):
        runtime = await video_runtime()
        project, version = await runtime.create_project(user_id="user", title="Secure edits")
        with self.assertRaisesRegex(ValueError, "direct resource references"):
            await runtime.preview_plan_patch(
                project_id=project.id,
                base_project_version_id=version.id,
                idempotency_key="bad-url",
                operations=[MediaEditOperation.model_validate({
                    "step_id": "transcribe", "capability": "media.transcribe",
                    "inputs": [{"role": "video", "artifact_version_id": "missing"}],
                    "parameters": {"video_url": "https://attacker.invalid/video.mp4"},
                })],
            )
        with self.assertRaisesRegex(LookupError, "currently selected"):
            await runtime.preview_plan_patch(
                project_id=project.id,
                base_project_version_id=version.id,
                idempotency_key="bad-artifact",
                operations=[MediaEditOperation.model_validate({
                    "step_id": "transcribe", "capability": "media.transcribe",
                    "inputs": [{"role": "video", "artifact_version_id": "missing"}],
                })],
            )

    def test_plan_rejects_cycle(self):
        with self.assertRaises(BuildPlanValidationError):
            topological_steps([
                RebuildPlanItem(step_id="a", action="create", depends_on=["b"]),
                RebuildPlanItem(step_id="b", action="create", depends_on=["a"]),
            ])

    def test_spec_rejects_unknown_character(self):
        raw = video_spec().model_dump()
        raw["shots"][0]["character_ids"] = ["missing"]
        with self.assertRaises(ValueError):
            VideoSpec.model_validate(raw)
