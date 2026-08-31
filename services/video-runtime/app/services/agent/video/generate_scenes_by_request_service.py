"""
按请求从故事梗概生成 scenes（scene_generation_node）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models.user_options import UserOption
from app.models.video_state import ImageUserInput
from app.services.agent.video.project_stage_context import (
    build_project_stage_state,
    parse_image_inputs,
)

logger = logging.getLogger(__name__)


async def generate_scenes_by_request(
    *,
    thread_id: str,
    user_id: str,
    user_option: Optional[UserOption] = None,
    run_id: Optional[str] = None,
    user_input: str = "",
    images: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    from app.services.agent.video.scene_generation_service import scene_generation_node
    from app.services.agent.video.visual_elements_matching_service import (
        visual_elements_matching_node,
    )

    image_inputs: List[ImageUserInput] = parse_image_inputs(images)
    state = await build_project_stage_state(
        thread_id=thread_id,
        user_id=user_id,
        user_option=user_option,
        run_id=run_id,
        user_input=user_input,
        images=image_inputs,
        require_outline=True,
    )
    send_event = state.pop("_noop_send_event")
    state.pop("_outline", None)

    node_result = await scene_generation_node(state, None, send_event)
    scene_uuids: List[str] = []
    if isinstance(node_result, dict):
        scene_uuids = list(node_result.get("scene_uuids") or [])
        state["scene_uuids"] = scene_uuids

    # Soft quality step: bind characters onto scenes when both exist.
    if scene_uuids and state.get("character_uuids"):
        await visual_elements_matching_node(state, None, send_event)

    return {
        "summary": f"generated {len(scene_uuids)} scenes for thread {thread_id}",
        "message": f"场景已生成，共 {len(scene_uuids)} 个",
        "scene_uuids": scene_uuids,
        "thread_id": thread_id,
        "story_outline_uuid": state.get("story_outline_uuid"),
        "character_count": len(state.get("character_uuids") or []),
    }
