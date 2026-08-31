from __future__ import annotations

from app.domain.project.task_graph import GraphOperation, TaskGraphPatch


def plan_generic_sample(intent: str, *, has_image: bool = False, has_audio: bool = False) -> list[TaskGraphPatch]:
    """Return a minimal non-linear plan for the first Studio workflow."""
    text = intent.lower()
    if has_image or any(word in text for word in ("image", "picture", "图片", "角色图")):
        return [TaskGraphPatch(operation=GraphOperation.ADD_TASK, task_id="visual-anchor", payload={"capability_id": "image.generate"}, reason="visual-first")]
    if has_audio or any(word in text for word in ("music", "song", "音乐", "歌曲")):
        return [TaskGraphPatch(operation=GraphOperation.ADD_TASK, task_id="audio-analysis", payload={"capability_id": "audio.analyze"}, reason="audio-first")]
    return [TaskGraphPatch(operation=GraphOperation.ADD_TASK, task_id="story", payload={"capability_id": "story.generate"}, reason="story-first")]
