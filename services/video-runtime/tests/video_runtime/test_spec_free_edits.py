"""Editing persisted production output does not require a monolithic VideoSpec."""
import unittest

from app.video_runtime.models import MediaArtifactVersion, ProjectIntent
from app.video_runtime.repository import ProjectVersionConflict
from test_initial_build import FakePlanExecutor, video_runtime


class SpecFreeEditsTest(unittest.IsolatedAsyncioTestCase):
    async def test_followup_intent_retains_selected_generated_media(self):
        runtime = await video_runtime()
        runtime.staged_planning_enabled = True
        runtime.continuous_plan_patch_enabled = True
        project, _ = await runtime.create_project(user_id="user", title="Existing film")
        clip = await runtime.repo.add_artifact(MediaArtifactVersion(
            project_id=project.id, artifact_id="clip", type="video", uri="https://media.test/clip",
        ))
        project = await runtime.repo.get_project(project.id)
        plan = await runtime.plan_project(project_id=project.id,
            base_project_version_id=project.current_version_id, idempotency_key="followup",
            project_intent=ProjectIntent(title="Edit film", brief="Add another scene", workflow_id="seedance2"))
        self.assertEqual([item.artifact_version_id for item in plan.items if item.action == "reuse"], [clip.id])
        self.assertEqual(plan.next_checkpoint.planning_mode, "agentic")

    async def test_parameter_patch_rebuilds_descendants_and_preserves_other_media(self):
        runtime = await video_runtime()
        project, _ = await runtime.create_project(user_id="user", title="Dynamic film")
        outputs = []
        for name, capability, params in [
            ("reference", "atomic.image.generate", {"prompt": "character"}),
            ("clip", "atomic.video.generate", {"prompt": "original detailed prompt", "duration": 15, "generation_mode": "i2v"}),
            ("final", "media.concat", {"video_steps": ["clip"]}),
        ]:
            outputs.append(await runtime.repo.add_artifact(MediaArtifactVersion(
                project_id=project.id, artifact_id=name, type="image" if name == "reference" else "video",
                uri=f"https://media.test/{name}",
                metadata={"plan_step_id": name, "rebuild_capability": capability, "generation_parameters": params},
            )))
        # Public watermark versions may retain recipes but omit graph edges.
        # The final's saved video_steps must still invalidate it when clip changes.
        project = await runtime.repo.get_project(project.id)
        plan = await runtime.preview_edits(
            project_id=project.id, base_project_version_id=project.current_version_id,
            edits=[{"type": "regenerate_artifact", "artifactVersionId": outputs[1].id,
                    "patch": {"generation_mode": "reference", "start_image_url": None}}],
            idempotency_key="edit",
        )
        clip = next(item for item in plan.items if item.step_id == "clip")
        self.assertEqual(clip.parameters["prompt"], "original detailed prompt")
        self.assertEqual(clip.parameters["generation_mode"], "reference")
        self.assertEqual(set(plan.ids_for("rebuild")), {outputs[1].id, outputs[2].id})
        build = await runtime.start_build(project_id=project.id, plan_id=plan.id,
            base_project_version_id=project.current_version_id, idempotency_key="build")
        result, version = await runtime.execute_build(project_id=project.id, build_id=build.id, executor=FakePlanExecutor())
        self.assertEqual(result.status, "completed")
        self.assertEqual(version.selections["reference"], outputs[0].id)
        self.assertNotEqual(version.selections["clip"], outputs[1].id)
        with self.assertRaises(ProjectVersionConflict):
            await runtime.preview_edits(project_id=project.id, base_project_version_id=project.current_version_id,
                edits=[{"type": "regenerate_artifact", "artifactVersionId": outputs[1].id}])
