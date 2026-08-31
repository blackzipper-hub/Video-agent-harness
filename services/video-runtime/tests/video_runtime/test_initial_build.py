from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.video_runtime.initial_build import BuildPlanValidationError, topological_steps
from app.video_runtime.builtin_plugins.music_workflows import (
    LipsyncMusicVideoWorkflowPlugin,
    MusicVideoWorkflowPlugin,
)
from app.video_runtime.models import (
    MediaArtifactVersion,
    RebuildPlanItem,
    ValidationResult,
    VideoSpec,
)
from app.video_runtime.runtime import VideoBuildRuntime
from app.video_runtime.plugins import PluginContext
from app.video_runtime.skill_workflows import load_workflow_skills


async def video_runtime() -> VideoBuildRuntime:
    runtime = VideoBuildRuntime()
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
        "characters": [{
            "id": "hero", "name": "Hero", "appearance": "short black hair",
            "clothing": "blue coat",
        }],
        "shots": [
            {
                "id": "one", "order": 1, "duration_seconds": 5,
                "beat": "arrives", "visual_prompt": "Hero arrives at a station",
                "narration": "他抵达车站。", "character_ids": ["hero"],
            },
            {
                "id": "two", "order": 2, "duration_seconds": 5,
                "beat": "waits", "visual_prompt": "Hero waits under the clock",
                "narration": "时间慢慢过去。", "character_ids": ["hero"],
            },
            {
                "id": "three", "order": 3, "duration_seconds": 5,
                "beat": "leaves", "visual_prompt": "Hero boards the train",
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


class MusicWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def test_music_workflow_reuses_seedance_graph(self):
        spec = video_spec().model_copy(update={"workflow_id": "cuti.music-video"})
        plan = await MusicVideoWorkflowPlugin().compile_build_plan(
            PluginContext(project_id="project-1", values={"base_project_version_id": "version-1"}),
            spec,
        )
        ordered = topological_steps(plan.items)
        self.assertEqual(plan.workflow_id, "cuti.music-video")
        self.assertIn("bgm", [item.step_id for item in ordered])
        self.assertEqual(len(ordered), len(plan.items))

    async def test_lipsync_workflow_rewires_final_media_steps(self):
        spec = video_spec().model_copy(update={"workflow_id": "cuti.lipsync-music-video"})
        plan = await LipsyncMusicVideoWorkflowPlugin().compile_build_plan(
            PluginContext(project_id="project-1", values={"base_project_version_id": "version-1"}),
            spec,
        )
        ordered = topological_steps(plan.items)
        positions = {item.step_id: index for index, item in enumerate(ordered)}
        self.assertLess(positions["assembled-video"], positions["lipsync-video"])
        self.assertLess(positions["lipsync-video"], positions["final-video"])
        mix_narration = next(item for item in plan.items if item.step_id == "mix-narration")
        self.assertEqual(mix_narration.parameters["video_step"], "lipsync-video")


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
        await runtime.repo.update_build_step(steps[1])
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
