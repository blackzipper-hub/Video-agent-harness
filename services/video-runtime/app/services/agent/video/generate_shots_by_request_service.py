"""
按请求生成详细分镜（shots / detailed_shots）。

复用 master 管线中的 storyboard_detail_generation_node：
前置需已有故事梗概 + 场景（scenes）；产出 shot_uuids。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models.user_options import UserOption
from app.services.agent.video.project_stage_context import build_project_stage_state

logger = logging.getLogger(__name__)


async def generate_shots_by_request(
    *,
    thread_id: str,
    user_id: str,
    user_option: Optional[UserOption] = None,
    run_id: Optional[str] = None,
    user_input: str = "",
) -> Dict[str, Any]:
    """为已有 project thread 生成详细分镜（shots）。"""
    from app.services.agent.video.storyboard_detail_generation_service import (
        storyboard_detail_generation_node,
    )
    from app.services.agent.video.visual_elements_matching_service import (
        visual_elements_matching_node,
    )

    state = await build_project_stage_state(
        thread_id=thread_id,
        user_id=user_id,
        user_option=user_option,
        run_id=run_id,
        user_input=user_input,
        require_outline=True,
        require_scenes=True,
    )
    send_event = state.pop("_noop_send_event")
    state.pop("_outline", None)

    if state.get("character_uuids"):
        await visual_elements_matching_node(state, None, send_event)

    node_result = await storyboard_detail_generation_node(state, None, send_event)
    shot_uuids: List[str] = []
    if isinstance(node_result, dict):
        shot_uuids = list(node_result.get("shot_uuids") or [])

    return {
        "summary": f"generated {len(shot_uuids)} detailed shots for thread {thread_id}",
        "message": f"详细分镜已生成，共 {len(shot_uuids)} 个镜头",
        "shot_uuids": shot_uuids,
        "thread_id": thread_id,
        "story_outline_uuid": state.get("story_outline_uuid"),
        "scene_count": len(state.get("scene_uuids") or []),
    }
