"""
按请求首次生成镜视频（video_generation_node，非 regenerate）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models.user_options import UserOption
from app.services.agent.video.project_stage_context import build_project_stage_state

logger = logging.getLogger(__name__)


async def generate_shot_videos_by_request(
    *,
    thread_id: str,
    user_id: str,
    user_option: Optional[UserOption] = None,
    run_id: Optional[str] = None,
    user_input: str = "",
    keyframe_uuids: Optional[List[str]] = None,
    shot_uuids: Optional[List[str]] = None,
    shot_workflow_mode: Optional[str] = None,
) -> Dict[str, Any]:
    from app.services.agent.video.per_shot_generation_routing_service import (
        per_shot_generation_routing_node,
    )
    from app.services.agent.video.video_generation_service import video_generation_node
    from app.services.agent.video.video_segments_service import video_segments_node

    state = await build_project_stage_state(
        thread_id=thread_id,
        user_id=user_id,
        user_option=user_option,
        run_id=run_id,
        user_input=user_input,
        require_outline=True,
        require_shots=True,
        # video_generation_node owns mode-aware validation: keyframes remain
        # mandatory for I2V, but reference_t2v synthesizes shot placeholders.
        require_keyframes=False,
    )
    send_event = state.pop("_noop_send_event")
    state.pop("_outline", None)
    if shot_workflow_mode:
        state["shot_workflow_mode"] = shot_workflow_mode

    if shot_uuids:
        state["shot_uuids"] = [str(u) for u in shot_uuids if u]
    if keyframe_uuids:
        state["keyframe_uuids"] = [str(u) for u in keyframe_uuids if u]

    # Ensure generation_mode / routing exist before first-time video gen.
    await per_shot_generation_routing_node(state, None, send_event)

    node_result = await video_generation_node(state, None, send_event)
    video_generation_uuids: List[Any] = []
    if isinstance(node_result, dict):
        video_generation_uuids = list(node_result.get("video_generation_uuids") or [])
        state["video_generation_uuids"] = [
            u for u in video_generation_uuids if u
        ]

    segment_uuids: List[str] = []
    segment_error: Optional[str] = None
    if state.get("video_generation_uuids"):
        try:
            seg_result = await video_segments_node(state, None, send_event)
            if isinstance(seg_result, dict):
                segment_uuids = list(seg_result.get("video_segments_uuids") or [])
        except Exception as e:
            segment_error = str(e)
            logger.warning(
                "generate_shot_videos: video_segments sync skipped thread=%s err=%s",
                thread_id,
                e,
            )

    cleaned = [u for u in video_generation_uuids if u]
    result: Dict[str, Any] = {
        "summary": f"generated {len(cleaned)} shot videos for thread {thread_id}",
        "message": f"镜视频已生成，共 {len(cleaned)} 个",
        "video_generation_uuids": cleaned,
        "video_segments_uuids": segment_uuids,
        "thread_id": thread_id,
        "story_outline_uuid": state.get("story_outline_uuid"),
        "keyframe_count": len(state.get("keyframe_uuids") or []),
    }
    if segment_error:
        result["segment_warning"] = segment_error
    return result
