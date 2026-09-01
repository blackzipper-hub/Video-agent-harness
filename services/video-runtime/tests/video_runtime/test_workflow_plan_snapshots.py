from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.video_runtime.initial_build import BuildPlanValidationError, topological_steps
from app.video_runtime.models import MediaArtifactVersion, ValidationResult, VideoSpec
from app.video_runtime.plugins import PluginContext, VideoPluginRegistry
from app.video_runtime.runtime import VideoBuildRuntime
from app.video_runtime.skill_workflows import load_workflow_skills
from app.video_runtime.skills import VideoSkillRuntime


def _spec(
    workflow_id: str,
    *,
    segment_seconds: float = 5,
    source_asset_ids: list[str] | None = None,
    bgm_prompt: str = "",
) -> VideoSpec:
    return VideoSpec.model_validate({
        "title": f"{workflow_id} snapshot",
        "target_duration_seconds": segment_seconds * 2,
        "workflow_id": workflow_id,
        "source_asset_ids": source_asset_ids or [],
        "characters": [{
            "id": "hero", "name": "Hero", "appearance": "black hair",
        }],
        "shots": [{
            "id": str(index), "order": index,
            "duration_seconds": segment_seconds,
            "beat": f"beat {index}", "visual_prompt": f"shot {index}",
            "character_ids": ["hero"],
        } for index in (1, 2)],
        "audio": {"bgm_prompt": bgm_prompt, "subtitles": False},
    })


def _signature(plan) -> list[str]:
    return [
        f"{item.step_id}:{item.action}:{item.capability}"
        for item in topological_steps(plan.items)
    ]


class ProviderMockExecutor:
    async def execute_plan_step(
        self, *, build, step, completed_artifacts, idempotency_key,
        report_remote_operation=None,
    ) -> MediaArtifactVersion:
        metadata = {
            "generation_parameters": dict(step.parameters),
            "plan_step_id": step.step_id,
        }
        if step.output_artifact_type == "video_spec":
            metadata["content"] = step.parameters["content"]
        if step.output_artifact_type == "timeline":
            cursor = 0.0
            items = []
            for shot, source_step in zip(
                step.parameters["shots"], step.parameters["video_steps"], strict=True,
            ):
                source = completed_artifacts[source_step]
                items.append({
                    "artifactVersionId": source.id,
                    "startSeconds": cursor,
                    "durationSeconds": shot["duration_seconds"],
                })
                cursor += shot["duration_seconds"]
            metadata["timeline"] = {"items": items, "durationSeconds": cursor}
        document_types = {
            "video_spec", "script", "characters", "storyboard",
            "outline", "scenes", "shots", "timeline",
        }
        return MediaArtifactVersion(
            project_id=build.project_id,
            type=step.output_artifact_type,
            uri=(
                None if step.output_artifact_type in document_types
                else f"https://provider-mock.test/{step.step_id}"
            ),
            metadata=metadata,
            provenance={"idempotency_key": idempotency_key},
        )


class WorkflowPlanSnapshotTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.skills = VideoSkillRuntime()
        plugins = VideoPluginRegistry()
        await load_workflow_skills(plugins, self.skills)
        self.plugin = plugins.get("cuti.skill-workflows").implementation
        self.source = MediaArtifactVersion(
            id="source-version-1", artifact_id="source:product",
            project_id="project-1", type="source_image",
            uri="https://example.test/product.png",
        )

    async def _compile(self, workflow_id: str, spec: VideoSpec | None = None):
        return await self.plugin.compile_build_plan(
            PluginContext(project_id="project-1", values={
                "base_project_version_id": "version-1",
                "source_artifacts": {self.source.artifact_id: self.source},
            }),
            spec or _spec(workflow_id),
        )

    async def test_every_available_workflow_has_its_own_plan_contract(self) -> None:
        plans = {
            "workflow-keyframe-pipeline": await self._compile("workflow-keyframe-pipeline"),
            "workflow-direct-video": await self._compile("workflow-direct-video"),
            "workflow-short-drama": await self._compile("workflow-short-drama"),
            "seedance2": await self._compile("seedance2"),
            "seedance-mv": await self._compile(
                "seedance-mv", _spec("seedance-mv", bgm_prompt="test song"),
            ),
            "short-drama-workflow": await self._compile(
                "short-drama-workflow",
                _spec("short-drama-workflow", segment_seconds=15),
            ),
        }
        for workflow_id in (
            "product-ad-video", "cuti-product-workflow",
            "cuti-scenario-product-workflow", "libtv-product-workflow",
        ):
            plans[workflow_id] = await self._compile(
                workflow_id,
                _spec(workflow_id, source_asset_ids=[self.source.artifact_id]),
            )

        snapshots = {name: _signature(plan) for name, plan in plans.items()}
        self.assertIn("shot-1-keyframe:create:atomic.image.generate", snapshots["workflow-keyframe-pipeline"])
        self.assertNotIn("shot-1-keyframe:create:atomic.image.generate", snapshots["workflow-direct-video"])
        self.assertIn("outline:create:runtime.artifact.persist", snapshots["workflow-short-drama"])
        self.assertIn("scenes:create:runtime.artifact.persist", snapshots["workflow-short-drama"])
        self.assertIn("shots:create:runtime.artifact.persist", snapshots["workflow-short-drama"])
        self.assertNotIn("shot-1-tail:create:media.extract_frame", snapshots["short-drama-workflow"])
        self.assertIn("music-analysis:create:media.audio.analyze", snapshots["seedance-mv"])
        self.assertIn("shot-1-audio:create:media.audio.trim", snapshots["seedance-mv"])
        self.assertIn("final-video:create:media.mix_audio", snapshots["seedance-mv"])
        for workflow_id in (
            "product-ad-video", "cuti-product-workflow",
            "cuti-scenario-product-workflow", "libtv-product-workflow",
        ):
            self.assertIn(
                "shot-1-product-validation:validate:cuti.continuity.validate",
                snapshots[workflow_id],
            )

        seedance = plans["seedance2"]
        forbidden = {
            "media.tts", "atomic.music.generate", "media.subtitle.compose",
            "media.subtitle.burn",
        }
        self.assertFalse(forbidden & {item.capability for item in seedance.items})
        self.assertFalse(any(item.output_artifact_type == "keyframe" for item in seedance.items))
        clips = [item for item in seedance.items if item.capability == "atomic.video.generate"]
        self.assertTrue(all(item.parameters["model"] == "doubao-seedance-2-0" for item in clips))
        self.assertTrue(all(not item.skill_ids for item in clips))

        parallel_clips = [
            item for item in plans["short-drama-workflow"].items
            if item.capability == "atomic.video.generate"
        ]
        self.assertTrue(all(item.parameters["model"] == "seedance-2.5" for item in parallel_clips))
        self.assertNotIn("shot-1-video", parallel_clips[1].depends_on)
        self.assertNotIn("shot-1-tail", parallel_clips[1].depends_on)

    async def test_unavailable_and_unknown_workflows_fail_closed(self) -> None:
        for workflow_id in ("open-montage", "ink-press-product-workflow"):
            with self.assertRaises(BuildPlanValidationError):
                await self._compile(workflow_id)

        views = {item["id"]: item for item in self.plugin.describe_workflows()}
        self.assertFalse(views["open-montage"]["available"])
        self.assertEqual(
            views["open-montage"]["missingCapabilities"],
            ["open_montage.tool.invoke"],
        )
        self.assertFalse(views["ink-press-product-workflow"]["available"])

    async def test_every_available_workflow_completes_with_provider_mock(self) -> None:
        runtime = VideoBuildRuntime(skill_runtime=self.skills)
        await runtime.plugins.load_directories([
            Path(__file__).resolve().parents[2] / "plugins",
        ])
        await load_workflow_skills(runtime.plugins, self.skills)

        async def validate(*, build_id, artifact):
            return [ValidationResult(
                project_id=artifact.project_id,
                build_id=build_id,
                artifact_version_id=artifact.id,
                validator_id="provider-mock",
                passed=True,
            )]

        runtime.validate_artifact = validate
        workflow_specs = {
            "workflow-keyframe-pipeline": _spec("workflow-keyframe-pipeline"),
            "workflow-direct-video": _spec("workflow-direct-video"),
            "workflow-short-drama": _spec("workflow-short-drama"),
            "seedance2": _spec("seedance2"),
            "seedance-mv": _spec("seedance-mv", bgm_prompt="test song"),
            "short-drama-workflow": _spec("short-drama-workflow", segment_seconds=15),
        }
        for workflow_id in (
            "product-ad-video", "cuti-product-workflow",
            "cuti-scenario-product-workflow", "libtv-product-workflow",
        ):
            workflow_specs[workflow_id] = _spec(
                workflow_id, source_asset_ids=["source:product"],
            )

        for index, (workflow_id, spec) in enumerate(workflow_specs.items(), start=1):
            project, version = await runtime.create_project(
                user_id="user-1", title=f"Mock {workflow_id}",
            )
            retained_source = await runtime.add_artifact(MediaArtifactVersion(
                artifact_id="source:retained",
                project_id=project.id,
                type="source_image",
                uri="https://provider-mock.test/retained.png",
            ))
            if spec.source_asset_ids:
                await runtime.add_artifact(MediaArtifactVersion(
                    artifact_id="source:product",
                    project_id=project.id,
                    type="source_image",
                    uri="https://provider-mock.test/product.png",
                ))
            project = await runtime.repo.get_project(project.id)
            plan = await runtime.plan_project(
                project_id=project.id,
                base_project_version_id=project.current_version_id,
                video_spec=spec,
                idempotency_key=f"mock-plan-{index}",
            )
            build = await runtime.start_build(
                project_id=project.id,
                plan_id=plan.id,
                base_project_version_id=project.current_version_id,
                idempotency_key=f"mock-build-{index}",
            )
            completed, committed = await runtime.execute_build(
                project_id=project.id,
                build_id=build.id,
                executor=ProviderMockExecutor(),
            )
            self.assertEqual(completed.status, "completed", workflow_id)
            self.assertIn(
                retained_source.artifact_id,
                committed.selections,
                f"{workflow_id} dropped an existing source artifact",
            )
