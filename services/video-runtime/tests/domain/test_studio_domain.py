from app.domain.artifacts import ArtifactStatus, StudioArtifact, StudioArtifactEdge, invalidate_downstream
from app.domain.project import GraphOperation, TaskGraphPatch, apply_task_graph_patch
from app.orchestration.planner import plan_generic_sample


def test_rejecting_music_only_invalidates_downstream_assets():
    artifacts = [
        StudioArtifact(id="music", artifact_id="music", project_id="p", type="music", status=ArtifactStatus.READY),
        StudioArtifact(id="beat", artifact_id="beat", project_id="p", type="beat_map"),
        StudioArtifact(id="edit", artifact_id="edit", project_id="p", type="timeline"),
        StudioArtifact(id="character", artifact_id="character", project_id="p", type="character"),
    ]
    result = invalidate_downstream(artifacts, [StudioArtifactEdge(source_version_id="music", target_version_id="beat"), StudioArtifactEdge(source_version_id="beat", target_version_id="edit")], {"music"})
    assert result.stale_ids == {"music", "beat", "edit"}
    assert result.untouched_ids == {"character"}


def test_task_graph_patch_supports_user_reordering_controls():
    tasks = apply_task_graph_patch({}, TaskGraphPatch(operation=GraphOperation.ADD_TASK, task_id="image", payload={"capability_id": "image.generate"}))
    tasks = apply_task_graph_patch(tasks, TaskGraphPatch(operation=GraphOperation.PAUSE_TASK, task_id="image"))
    assert tasks["image"]["status"] == "blocked"


def test_generic_sample_planner_accepts_visual_first_entry():
    plan = plan_generic_sample("先生成女主角图片")
    assert plan[0].payload["capability_id"] == "image.generate"
