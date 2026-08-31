from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.video_runtime.api import router, set_runtime
from app.video_runtime.models import VideoSpec
from app.video_runtime.plugins import PluginContext, VideoPluginRegistry
from app.video_runtime.runtime import VideoBuildRuntime
from app.video_runtime.skill_workflows import load_workflow_skills
from app.video_runtime.skills import VideoSkillRuntime


def _video_spec(workflow_id: str) -> VideoSpec:
    return VideoSpec.model_validate({
        "title": "Workflow adapter",
        "target_duration_seconds": 10,
        "workflow_id": workflow_id,
        "characters": [{
            "id": "hero",
            "name": "Hero",
            "appearance": "black hair",
        }],
        "shots": [
            {
                "id": "1",
                "order": 1,
                "duration_seconds": 5,
                "beat": "arrives",
                "visual_prompt": "Hero arrives",
                "character_ids": ["hero"],
            },
            {
                "id": "2",
                "order": 2,
                "duration_seconds": 5,
                "beat": "leaves",
                "visual_prompt": "Hero leaves",
                "character_ids": ["hero"],
            },
        ],
    })


class SkillWorkflowPluginTest(unittest.IsolatedAsyncioTestCase):
    async def test_imported_cuti_workflows_compile_with_declared_modes(self):
        plugins = VideoPluginRegistry()
        skills = VideoSkillRuntime()
        count = await load_workflow_skills(plugins, skills)
        self.assertGreaterEqual(count, 3)
        loaded = plugins.get("cuti.skill-workflows")
        self.assertIn(
            "workflow-keyframe-pipeline",
            loaded.manifest.contributions.workflows,
        )

        keyframe = await loaded.implementation.compile_build_plan(
            PluginContext(
                project_id="project-1",
                values={"base_project_version_id": "version-1"},
            ),
            _video_spec("workflow-keyframe-pipeline"),
        )
        self.assertTrue(any(
            item.output_artifact_type == "keyframe" for item in keyframe.items
        ))
        self.assertEqual(
            next(item for item in keyframe.items if item.capability == "atomic.video.generate")
            .parameters["workflow_mode"],
            "keyframe_pipeline",
        )

        short_drama = await loaded.implementation.compile_build_plan(
            PluginContext(
                project_id="project-1",
                values={"base_project_version_id": "version-1"},
            ),
            _video_spec("workflow-short-drama"),
        )
        self.assertFalse(any(
            item.output_artifact_type == "keyframe" for item in short_drama.items
        ))
        second_clip = next(
            item for item in short_drama.items if item.step_id == "shot-2-video"
        )
        self.assertEqual(
            second_clip.parameters["start_image_from_step"],
            "shot-1-tail",
        )
        self.assertEqual(second_clip.parameters["workflow_mode"], "short_drama")

        seedance2 = await loaded.implementation.compile_build_plan(
            PluginContext(
                project_id="project-1",
                values={"base_project_version_id": "version-1"},
            ),
            _video_spec("seedance2"),
        )
        capabilities = {item.capability for item in seedance2.items}
        self.assertNotIn("media.tts", capabilities)
        self.assertNotIn("atomic.music.generate", capabilities)
        self.assertNotIn("atomic.image.generate", capabilities)
        self.assertNotIn("media.subtitle.compose", capabilities)
        self.assertNotIn("media.subtitle.burn", capabilities)
        self.assertFalse(any(
            item.output_artifact_type == "keyframe" for item in seedance2.items
        ))
        clips = [
            item for item in seedance2.items
            if item.capability == "atomic.video.generate"
        ]
        self.assertEqual(len(clips), 2)
        self.assertTrue(all(item.parameters["generate_audio"] for item in clips))
        self.assertTrue(all(not item.skill_ids for item in clips))
        self.assertNotIn("character_reference_from_steps", clips[0].parameters)
        self.assertEqual(clips[0].depends_on, ["storyboard"])
        self.assertEqual(clips[1].depends_on, ["storyboard", "shot-1-tail"])
        self.assertEqual(clips[1].parameters["start_image_from_step"], "shot-1-tail")
        final = next(item for item in seedance2.items if item.step_id == "final-video")
        self.assertEqual(final.output_artifact_type, "final_video")
        self.assertEqual(final.parameters["transition_duration"], 0.125)

    async def test_seedance2_does_not_inherit_generic_cuti_director_skills(self):
        skills = VideoSkillRuntime()
        runtime = VideoBuildRuntime(skill_runtime=skills)
        await runtime.plugins.load_directories([
            Path(__file__).resolve().parents[2] / "plugins",
        ])
        await load_workflow_skills(runtime.plugins, skills)
        project, version = await runtime.create_project(
            user_id="user-1",
            title="Original Seedance workflow",
        )

        plan = await runtime.plan_project(
            project_id=project.id,
            base_project_version_id=version.id,
            video_spec=_video_spec("seedance2"),
            idempotency_key="seedance2-plan",
        )

        clips = [
            item for item in plan.items
            if item.capability == "atomic.video.generate"
        ]
        self.assertTrue(clips)
        self.assertTrue(all(not item.resolved_skills for item in clips))
        self.assertTrue(all(item.skill_context is None for item in clips))

    async def test_missing_skill_dependency_and_unknown_capability_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing-dependency"
            missing.mkdir()
            (missing / "SKILL.md").write_text("""---
name: missing-dependency
description: missing dependency
metadata:
  kind: workflow
  workflow:
    mode: test
    pipeline: [atomic.video.generate]
    allowed_capabilities: [atomic.video.generate]
    dependencies:
      skills: [not-installed]
---
instructions
""", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requires missing Skills"):
                VideoSkillRuntime([root])

            (missing / "SKILL.md").unlink()
            missing.rmdir()
            unknown = root / "unknown-capability"
            unknown.mkdir()
            (unknown / "SKILL.md").write_text("""---
name: unknown-capability
description: unknown capability
metadata:
  kind: workflow
  workflow:
    mode: test
    pipeline: [unsafe.unknown]
    allowed_capabilities: [unsafe.unknown]
---
instructions
""", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown capability"):
                VideoSkillRuntime([root])

    async def test_runtime_freezes_workflow_supervisor_and_director_skills(self):
        skills = VideoSkillRuntime()
        runtime = VideoBuildRuntime(skill_runtime=skills)
        await runtime.plugins.load_directories([
            Path(__file__).resolve().parents[2] / "plugins",
        ])
        await load_workflow_skills(runtime.plugins, skills)
        project, version = await runtime.create_project(
            user_id="user-1",
            title="Product film",
        )
        plan = await runtime.plan_project(
            project_id=project.id,
            base_project_version_id=version.id,
            video_spec=_video_spec("product-ad-video"),
            idempotency_key="product-plan",
        )
        video = next(
            item for item in plan.items
            if item.capability == "atomic.video.generate"
        )
        skill_ids = [item.skill_id for item in video.resolved_skills]
        self.assertIn("product-ad-video", skill_ids)
        self.assertIn("video-director", skill_ids)
        self.assertIn("product identity", video.skill_context.instructions)

    async def test_project_skill_lock_is_runtime_owned_and_frozen_into_steps(self):
        skills = VideoSkillRuntime()
        runtime = VideoBuildRuntime(skill_runtime=skills)
        await runtime.plugins.load_directories([
            Path(__file__).resolve().parents[2] / "plugins",
        ])
        await load_workflow_skills(runtime.plugins, skills)
        project, version = await runtime.create_project(
            user_id="user-1",
            title="Locked direction",
        )
        lock = await runtime.set_project_skill_enabled(
            project_id=project.id,
            skill_id="character-director",
            enabled=True,
        )
        self.assertEqual(lock.project_id, project.id)
        self.assertTrue(lock.digest)

        plan = await runtime.plan_project(
            project_id=project.id,
            base_project_version_id=version.id,
            video_spec=_video_spec("product-ad-video"),
            idempotency_key="locked-skill-plan",
        )
        persisted = await runtime.list_project_skill_locks(project.id)
        self.assertEqual([item.skill_id for item in persisted], ["character-director"])
        character_step = next(item for item in plan.items if item.step_id == "characters")
        resolved = {item.skill_id: item for item in character_step.resolved_skills}
        self.assertEqual(resolved["character-director"].source, "project_lock")


class SkillWorkflowApiTest(unittest.TestCase):
    def test_workflow_api_exposes_skill_metadata(self):
        plugins = VideoPluginRegistry()
        skills = VideoSkillRuntime()
        asyncio.run(load_workflow_skills(plugins, skills))
        set_runtime(VideoBuildRuntime(plugins=plugins, skill_runtime=skills))
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            response = client.get(
                "/api/video/workflows",
                headers={"X-Video-User-Id": "user-1"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        workflows = {item["id"]: item for item in response.json()["data"]}
        self.assertEqual(
            workflows["workflow-short-drama"]["mode"],
            "short_drama",
        )
        self.assertEqual(
            workflows["workflow-keyframe-pipeline"]["source"],
            "skill",
        )
