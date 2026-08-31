"""
按请求生成 VideoAgent 故事梗概（DB outline，非 story agent 散文）。
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


async def generate_outline_by_request(
    *,
    thread_id: str,
    user_id: str,
    user_option: Optional[UserOption] = None,
    run_id: Optional[str] = None,
    user_input: str = "",
    images: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    from app.services.agent.video.outline_generation_service import outline_generation_node

    image_inputs: List[ImageUserInput] = parse_image_inputs(images)
    state = await build_project_stage_state(
        thread_id=thread_id,
        user_id=user_id,
        user_option=user_option,
        run_id=run_id,
        user_input=user_input,
        images=image_inputs,
        create_if_missing=True,
    )
    send_event = state.pop("_noop_send_event")
    state.pop("_outline", None)

    node_result = await outline_generation_node(state, None, send_event)
    story_outline_uuid = None
    if isinstance(node_result, dict):
        story_outline_uuid = node_result.get("story_outline_uuid")

    payload: Dict[str, Any] = {
        "summary": f"outline ready for thread {thread_id}",
        "message": "故事梗概已生成",
        "story_outline_uuid": story_outline_uuid,
        "thread_id": thread_id,
    }

    # Enrich artifact metadata so Deep Agent UI can render outline text
    # without waiting on a separate VideoResultsPanel fetch.
    if story_outline_uuid:
        try:
            from app.crud.video.video_story import (
                get_chapters_by_story_outline_id,
                get_video_story_outline_by_uuid,
            )
            from app.utils.chapter_order import sort_chapters_by_order

            outline = await get_video_story_outline_by_uuid(story_outline_uuid)
            if outline is not None:
                title = getattr(outline, "title", None) or ""
                description = getattr(outline, "description", None) or ""
                theme = getattr(outline, "theme", None) or ""
                key_message = getattr(outline, "key_message", None) or ""
                style_guide = getattr(outline, "style_guide", None) or ""
                total_duration = getattr(outline, "total_duration", None)
                chapters = sort_chapters_by_order(
                    await get_chapters_by_story_outline_id(story_outline_uuid)
                )
                chapter_rows = []
                lines = [f"# {title}" if title else "# Story outline"]
                if theme:
                    lines.append(f"**Theme:** {theme}")
                if key_message:
                    lines.append(f"**Key message:** {key_message}")
                if description:
                    lines.extend(["", description])
                if style_guide:
                    lines.extend(["", f"**Style:** {style_guide}"])
                if total_duration is not None:
                    lines.append(f"**Duration:** {total_duration}s")
                if chapters:
                    lines.append("")
                    lines.append("## Chapters")
                    for i, chapter in enumerate(chapters):
                        ch_title = getattr(chapter, "title", "") or f"Chapter {i + 1}"
                        ch_desc = getattr(chapter, "description", "") or ""
                        ch_dur = getattr(chapter, "duration", None)
                        dur_suffix = f" ({ch_dur}s)" if ch_dur is not None else ""
                        lines.append(f"### {i + 1}. {ch_title}{dur_suffix}")
                        if ch_desc:
                            lines.append(ch_desc)
                        chapter_rows.append({
                            "title": ch_title,
                            "description": ch_desc,
                            "duration": ch_dur,
                            "order": i,
                        })
                content = "\n".join(lines).strip()
                payload.update({
                    "title": title,
                    "theme": theme,
                    "description": description,
                    "key_message": key_message,
                    "style_guide": style_guide,
                    "total_duration": total_duration,
                    "chapters": chapter_rows,
                    "content": content,
                    "outline": content,
                    "summary": title or payload["summary"],
                })
        except Exception:
            logger.exception(
                "Failed to enrich outline artifact for %s", story_outline_uuid
            )

    return payload
