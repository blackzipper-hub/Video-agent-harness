"""
按请求生成关键帧（首次生成，非 regenerate）。

复用 master 管线中的 keyframe_generation_node：
前置需已有故事梗概 + detailed shots；产出 keyframe_uuids。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models.user_options import UserOption
from app.services.agent.video.project_stage_context import build_project_stage_state

logger = logging.getLogger(__name__)


async def generate_keyframes_by_request(
    *,
    thread_id: str,
    user_id: str,
    user_option: Optional[UserOption] = None,
    run_id: Optional[str] = None,
    user_input: str = "",
    shot_uuids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """为已有 project thread 首次生成关键帧。"""
    from app.services.agent.video.keyframe_generation_service import (
        keyframe_generation_node,
    )
    from app.services.agent.video.per_shot_generation_routing_service import (
        per_shot_generation_routing_node,
    )

    state = await build_project_stage_state(
        thread_id=thread_id,
        user_id=user_id,
        user_option=user_option,
        run_id=run_id,
        user_input=user_input,
        require_outline=True,
        require_shots=True,
    )
    send_event = state.pop("_noop_send_event")
    state.pop("_outline", None)

    if shot_uuids:
        state["shot_uuids"] = [str(u) for u in shot_uuids if u]

    # Write generation_mode / routing before keyframes (matches master ordering soft deps).
    await per_shot_generation_routing_node(state, None, send_event)

    node_result = await keyframe_generation_node(state, None, send_event)
    keyframe_uuids: List[str] = []
    if isinstance(node_result, dict):
        keyframe_uuids = list(node_result.get("keyframe_uuids") or [])

    return {
        "summary": f"generated {len(keyframe_uuids)} keyframes for thread {thread_id}",
        "message": f"关键帧已生成，共 {len(keyframe_uuids)} 个",
        "keyframe_uuids": keyframe_uuids,
        "shot_uuids": list(state.get("shot_uuids") or []),
        "thread_id": thread_id,
        "story_outline_uuid": state.get("story_outline_uuid"),
        "shot_count": len(state.get("shot_uuids") or []),
    }
