"""
按请求首次生成角色（main_character_design，非 regenerate）。
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


async def generate_characters_by_request(
    *,
    thread_id: str,
    user_id: str,
    user_option: Optional[UserOption] = None,
    run_id: Optional[str] = None,
    user_input: str = "",
    images: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    from app.services.agent.video.main_character_design_service import (
        main_character_design_node,
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

    node_result = await main_character_design_node(state, None, send_event)
    character_uuids: List[str] = []
    if isinstance(node_result, dict):
        character_uuids = list(node_result.get("character_uuids") or [])

    payload: Dict[str, Any] = {
        "summary": f"generated {len(character_uuids)} characters for thread {thread_id}",
        "message": f"角色已生成，共 {len(character_uuids)} 个",
        "character_uuids": character_uuids,
        "thread_id": thread_id,
        "story_outline_uuid": state.get("story_outline_uuid"),
    }

    # Attach preview image URLs so Deep Agent artifact cards can render immediately.
    if character_uuids:
        try:
            from app.crud.video.video_character import (
                get_character_versions_batch,
                get_characters_by_uuids,
                pick_selected_character_version,
            )

            characters = await get_characters_by_uuids(character_uuids)
            versions_by_id = await get_character_versions_batch(
                character_uuids, user_id
            )
            images: List[Dict[str, Any]] = []
            for character in characters:
                versions = versions_by_id.get(character.uuid) or []
                selected = pick_selected_character_version(versions, character)
                image_url = (
                    (selected.character_image_url if selected else None)
                    or getattr(character, "image_url", None)
                    or ""
                )
                images.append({
                    "uuid": character.uuid,
                    "name": character.name,
                    "description": character.description,
                    "appearance": character.appearance,
                    "image_url": image_url,
                    "uri": image_url,
                    "url": image_url,
                })
            payload["images"] = images
            payload["characters"] = images
            first_url = next(
                (item["image_url"] for item in images if item.get("image_url")),
                "",
            )
            if first_url:
                payload["image_url"] = first_url
                payload["uri"] = first_url
                names = ", ".join(
                    item["name"] for item in images if item.get("name")
                )
                payload["summary"] = (
                    f"generated {len(images)} characters"
                    + (f" ({names})" if names else "")
                    + f" for thread {thread_id}"
                )
        except Exception:
            logger.exception(
                "Failed to enrich character artifact images for thread %s",
                thread_id,
            )

    return payload
