"""
关键帧 regenerate 补槽：无 version 行时解析/创建父记录并单次图生落库；
已有 version 行时经 agent 走 `_regenerate_keyframe_version` / reflection。

与 `regenerate_by_request_service` 编排配合。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from app.exceptions import BusinessException, BusinessExceptionCode
from app.models.user_options import UserOption
from app.models.video_state import KeyframeVersion, UserInput

logger = logging.getLogger(__name__)


def regenerate_pack_keyframe_result(
    kf_obj: Optional[KeyframeVersion],
    new_version_uuid: Optional[str],
) -> Dict[str, Any]:
    ok = bool(kf_obj and kf_obj.success and new_version_uuid)
    err = None if ok else ((getattr(kf_obj, "error_msg", None) if kf_obj else None) or "关键帧生成失败")
    return {
        "success": ok,
        "keyframe_version": kf_obj,
        "new_version_uuid": new_version_uuid if ok else None,
        "storyboard_edit_record_id": None,
        "error_msg": err,
    }


async def regenerate_append_keyframe_version_resolving_parent(
    *,
    user_id: str,
    user_option: Optional[UserOption],
    custom_prompt: Optional[str],
    run_id: str,
    keyframe_uuid: str,
    thread_id: Optional[str],
    shot_number: Optional[int],
    frame_index: int,
) -> Tuple[str, Dict[str, Any]]:
    """
    在已有父记录上追加关键帧版本；若 keyframe_uuid 为空则按 thread + shot_number + frame_index 解析/创建父记录。
    """
    from app.crud.conversation import async_get_conversation_by_thread_id
    from app.crud.video.video_keyframe import (
        create_keyframe,
        get_keyframe_by_uuid,
        get_keyframes_by_conversation,
    )
    from app.crud.video.video_story import (
        get_detailed_shot_by_id,
        get_detailed_shots_by_conversation,
        get_video_story_outline_by_run_id,
    )
    from app.services.agent.utils.database_utils import get_detailed_shots_from_db
    from app.services.agent.video.keyframe_reflection_service import regenerate_single_keyframe

    kfu = (keyframe_uuid or "").strip()
    shot_row = None
    conv_id: str
    tid: str
    keyframe_id: str

    if kfu:
        kdb = await get_keyframe_by_uuid(kfu)
        if not kdb:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                f"关键帧 {kfu} 不存在",
            )
        if getattr(kdb, "user_id", None) != user_id:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                f"无权限访问关键帧 {kfu}",
            )
        if not kdb.detailed_shot_id:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "找不到对应的详细镜头信息",
            )
        shot_row = await get_detailed_shot_by_id(kdb.detailed_shot_id)
        if not shot_row:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "找不到对应的详细镜头信息",
            )
        conv_id = str(kdb.conversation_id)
        tid = kdb.thread_id
        keyframe_id = kfu
    else:
        if not thread_id:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "空槽补全关键帧需要 thread_id",
            )
        if shot_number is None:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "空槽补全关键帧需要 shot_number",
            )
        conv = await async_get_conversation_by_thread_id(thread_id)
        if not conv or conv.user_id != user_id:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "无权访问此 thread 或对话不存在",
            )
        conv_id = str(conv.id)
        tid = thread_id
        shots_db = await get_detailed_shots_by_conversation(conv_id, thread_id)
        match = [s for s in shots_db if s.shot_number == shot_number]
        if not match:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                f"未找到镜头 {shot_number} 的详细分镜数据",
            )
        shot_row = match[0]
        kfs = await get_keyframes_by_conversation(conv_id, thread_id)
        slot_kfs = [k for k in kfs if k.shot_number == shot_number and getattr(k, "frame_index", 0) == frame_index]
        if slot_kfs:
            keyframe_id = slot_kfs[0].uuid
        else:
            outline = await get_video_story_outline_by_run_id(shot_row.run_id)
            if not outline:
                raise BusinessException(
                    BusinessExceptionCode.RESOURCE_NOT_FOUND,
                    "无法解析故事大纲，无法创建关键帧记录",
                )
            keyframe_id = await create_keyframe(
                shot_number=shot_row.shot_number,
                is_bridge=shot_row.is_bridge,
                reference_image_urls=[],
                conversation_id=conv_id,
                thread_id=tid,
                run_id=run_id,
                user_id=user_id,
                story_outline_id=outline.uuid,
                scene_id=shot_row.scene_id or "",
                storyboard_detail_id=shot_row.storyboard_detail_id or "",
                detailed_shot_id=shot_row.uuid or "",
                character_ids=shot_row.character_ids,
                frame_index=frame_index,
            )

    shots = await get_detailed_shots_from_db([shot_row.uuid])
    shot = shots[0]
    if custom_prompt and str(custom_prompt).strip():
        shot = shot.model_copy(update={"scene_description": str(custom_prompt).strip()})

    detected_lang = await _get_detected_language_from_db(tid)
    state: Dict[str, Any] = {
        "user_id": user_id,
        "conversation_id": conv_id,
        "thread_id": tid,
        "run_id": run_id,
        "user_input_data": UserInput(user_input="", user_option=user_option or UserOption()),
        "detected_language": detected_lang,
    }
    kf_obj, new_ver = await regenerate_single_keyframe(shot, state, keyframe_id)
    return keyframe_id, regenerate_pack_keyframe_result(kf_obj, new_ver)


async def regenerate_keyframe_from_version_uuid(
    service: Any,
    version_uuid: str,
    *,
    custom_prompt: Optional[str],
    instruction: Optional[str] = None,
    use_reflection: bool,
    user_id: str,
    user_option: Optional[UserOption],
    fallback_keyframe_uuid: str,
    regenerate_strategy: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """已有 keyframe_version 行时走 agent 的 `_regenerate_keyframe_version` / reflection（与历史行为一致）。"""
    from app.crud.video.video_keyframe import get_keyframe_version_by_uuid
    from app.services.agent.utils.user_option_rebuild import rebuild_user_option_from_keyframe_version

    version_db_obj = await get_keyframe_version_by_uuid(version_uuid)
    if not version_db_obj:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            f"关键帧版本 {version_uuid} 不存在"
        )
    version_user_id = version_db_obj.get('user_id') if isinstance(version_db_obj, dict) else getattr(version_db_obj, 'user_id', None)
    if version_user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            f"无权限访问关键帧版本 {version_uuid}"
        )
    effective_user_option = rebuild_user_option_from_keyframe_version(version_db_obj, fallback=user_option)
    if use_reflection:
        if instruction:
            logger.warning("regenerate_keyframe: use_reflection=True 时忽略 instruction")
        result = await service._regenerate_keyframe_with_reflection(
            version_db_obj, user_id, effective_user_option
        )
    else:
        result = await service._regenerate_keyframe_version(
            version_db_obj,
            custom_prompt,
            user_id,
            effective_user_option,
            instruction=instruction,
            regenerate_strategy=regenerate_strategy,
        )
    kid = version_db_obj.get("keyframe_id") if isinstance(version_db_obj, dict) else getattr(version_db_obj, "keyframe_id", None)
    keyframe_uuid_for_result = str(kid) if kid else fallback_keyframe_uuid
    return keyframe_uuid_for_result, result


async def _get_detected_language_from_db(thread_id: str) -> Optional[str]:
    """从 conversation_runs.additional_data.language 回退取得 detected_language。"""
    if not thread_id:
        return None
    try:
        from app.models.database import get_asyncpg_pool
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT additional_data FROM conversation_runs WHERE thread_id = $1 AND run_type = 'main' ORDER BY created_at LIMIT 1",
                thread_id,
            )
        if row and isinstance(row, (dict, str)):
            import json
            data = json.loads(row) if isinstance(row, str) else row
            lang = data.get("language")
            if lang:
                return lang
    except Exception as e:
        logger.warning("_get_detected_language_from_db failed: %s", e)
    return None
