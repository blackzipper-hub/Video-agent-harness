"""
Shared helpers for Deep Agent V2 stage skills (outline/characters/scenes/shots/keyframes/videos).

Load (or create) a VideoAgent project conversation by thread_id and assemble the
UUID lists nodes expect.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.exceptions import BusinessException, BusinessExceptionCode
from app.models.user_options import UserOption
from app.models.video_state import ImageUserInput, UserInput


async def _noop_send_event(**_kwargs: Any) -> None:
    return None


def coerce_video_agent_user_option(user_option: Any) -> Optional[UserOption]:
    """Normalize chat-layer or foreign UserOption instances to VideoAgent UserOption."""
    if user_option is None:
        return None
    if isinstance(user_option, UserOption):
        return user_option
    if hasattr(user_option, "model_dump"):
        return UserOption.model_validate(user_option.model_dump(mode="json"))
    return UserOption.model_validate(user_option)


def parse_image_inputs(raw: Any) -> List[ImageUserInput]:
    images: List[ImageUserInput] = []
    if not isinstance(raw, list):
        return images
    for item in raw:
        if isinstance(item, str) and item.strip():
            images.append(ImageUserInput(url=item.strip()))
        elif isinstance(item, dict) and item.get("url"):
            images.append(
                ImageUserInput(
                    url=str(item["url"]),
                    filename=item.get("filename"),
                    is_new=bool(item.get("is_new", True)),
                )
            )
    return images


async def ensure_project_conversation(
    *,
    thread_id: str,
    user_id: str,
    user_input: str = "",
    user_option: Optional[UserOption] = None,
    create_if_missing: bool = False,
):
    from app.crud.conversation import (
        async_create_conversation,
        async_get_conversation_by_thread_id,
    )

    if not thread_id:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER,
            "project stage skill requires thread_id",
        )
    conversation = await async_get_conversation_by_thread_id(thread_id)
    if conversation:
        if conversation.user_id and conversation.user_id != user_id:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "无权限访问此项目 thread",
            )
        return conversation
    if not create_if_missing:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            f"conversation not found for thread_id={thread_id}",
        )
    return await async_create_conversation(
        user_id=user_id,
        thread_id=thread_id,
        title="Deep Agent project",
        user_input=user_input or None,
        user_option=user_option.model_dump(mode="json") if user_option else None,
        agent_type="video",
    )


async def build_project_stage_state(
    *,
    thread_id: str,
    user_id: str,
    user_option: Optional[UserOption] = None,
    run_id: Optional[str] = None,
    user_input: str = "",
    images: Optional[List[ImageUserInput]] = None,
    create_if_missing: bool = False,
    require_outline: bool = False,
    require_scenes: bool = False,
    require_shots: bool = False,
    require_keyframes: bool = False,
) -> Dict[str, Any]:
    """Build a minimal VideoAgentState-like dict for standalone stage nodes."""
    from app.crud.video.video_audio import (
        get_music_generations_by_thread_id,
        get_narrations_by_thread_id,
        get_video_audio_transcription_by_thread_id,
    )
    from app.crud.video.video_character import get_characters_by_conversation
    from app.crud.video.video_keyframe import get_keyframes_by_conversation
    from app.crud.video.video_other import get_video_analysis_by_thread_id
    from app.crud.video.video_story import (
        get_detailed_shots_by_conversation,
        get_scenes_by_conversation,
        get_video_story_outline_by_thread_id,
    )

    user_option = coerce_video_agent_user_option(user_option)
    conversation = await ensure_project_conversation(
        thread_id=thread_id,
        user_id=user_id,
        user_input=user_input,
        user_option=user_option,
        create_if_missing=create_if_missing,
    )
    conversation_id = str(conversation.id)
    effective_option = user_option or UserOption()

    outline = await get_video_story_outline_by_thread_id(thread_id)
    if require_outline and not outline:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "找不到故事梗概；请先运行 outline.generate",
        )
    if outline and outline.user_id and outline.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权限访问此数据",
        )

    scenes = await get_scenes_by_conversation(conversation_id, thread_id)
    if require_scenes and not scenes:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "缺少场景数据；请先运行 scene.generate",
        )

    shots = await get_detailed_shots_by_conversation(conversation_id, thread_id)
    if require_shots and not shots:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "缺少详细镜头；请先运行 shot.generate",
        )

    keyframes = await get_keyframes_by_conversation(conversation_id, thread_id)
    if require_keyframes and not keyframes:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "缺少关键帧；请先运行 keyframe.generate",
        )

    characters = await get_characters_by_conversation(conversation_id, thread_id)
    analysis = await get_video_analysis_by_thread_id(thread_id)
    transcription = await get_video_audio_transcription_by_thread_id(thread_id)
    music_rows = await get_music_generations_by_thread_id(thread_id)
    narration_rows = await get_narrations_by_thread_id(thread_id)

    from app.services.agent.video.music_generation_service import (
        resolve_shot_workflow_mode,
    )

    return {
        "conversation_id": conversation_id,
        "thread_id": thread_id,
        "run_id": run_id or f"v2-stage-{uuid4()}",
        "user_id": user_id,
        "story_outline_uuid": outline.uuid if outline else None,
        "scene_uuids": [s.uuid for s in scenes if getattr(s, "uuid", None)],
        "shot_uuids": [s.uuid for s in shots if getattr(s, "uuid", None)],
        "keyframe_uuids": [k.uuid for k in keyframes if getattr(k, "uuid", None)],
        "character_uuids": [c.uuid for c in characters if getattr(c, "uuid", None)],
        "analysis_uuid": analysis.uuid if analysis and getattr(analysis, "uuid", None) else None,
        "audio_transcription_uuids": (
            [transcription.uuid]
            if transcription and getattr(transcription, "uuid", None)
            else []
        ),
        "music_generation_uuids": [
            m.uuid for m in music_rows if getattr(m, "uuid", None)
        ],
        "narration_uuids": [
            n.uuid for n in narration_rows if getattr(n, "uuid", None)
        ],
        "detected_language": getattr(conversation, "language", None),
        "user_input_data": UserInput(
            user_input=user_input or "",
            images=list(images or []),
            user_option=effective_option,
        ),
        # Atomic stage execution bypasses the legacy graph node that normally
        # injects this field.  Preserve the same routing contract so
        # reference_t2v can intentionally run without keyframes.
        "shot_workflow_mode": resolve_shot_workflow_mode(effective_option),
        "_outline": outline,
        "_noop_send_event": _noop_send_event,
    }
