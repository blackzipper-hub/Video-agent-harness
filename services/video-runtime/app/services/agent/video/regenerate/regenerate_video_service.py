"""
视频 regenerate 补槽：确保 video_generations 行存在、追加新版本（与 `_regenerate_video_version` 共用生成路径）。

由 `regenerate_by_request_service.regenerate_videos_by_request` 编排。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.exceptions import BusinessException, BusinessExceptionCode
from app.models.user_options import UserOption
from app.models.video_state import KeyframeVersion

logger = logging.getLogger(__name__)


async def regenerate_ensure_video_generation_row_for_shot(
    conv_id: str,
    thread_id: str,
    shot_number: int,
    user_id: str,
    run_id: str,
) -> Any:
    from app.crud.video.video_generation import (
        create_video_generation,
        get_video_generation_by_uuid,
        get_video_generations_by_conversation,
    )
    from app.crud.video.video_keyframe import (
        get_keyframe_versions_by_keyframe_ids,
        get_keyframes_by_conversation,
    )

    gens = await get_video_generations_by_conversation(conv_id, thread_id)
    for g in gens:
        if g.shot_number == shot_number:
            return g
    kfs = await get_keyframes_by_conversation(conv_id, thread_id)
    same = sorted(
        [k for k in kfs if k.shot_number == shot_number],
        key=lambda x: getattr(x, "frame_index", 0),
    )
    if not same:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            f"镜头 {shot_number} 尚无关键帧，请先生成关键帧",
        )
    kf_uuids: List[str] = [k.uuid for k in same]
    versions = await get_keyframe_versions_by_keyframe_ids(kf_uuids)
    if not any(v.success and (v.keyframe_url or "").strip() for v in versions):
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            f"镜头 {shot_number} 无可用关键帧图，请先生成关键帧",
        )
    primary = same[0]
    shot_id = primary.detailed_shot_id or ""
    new_id = await create_video_generation(
        shot_number=primary.shot_number,
        is_bridge=primary.is_bridge,
        conversation_id=conv_id,
        thread_id=thread_id,
        run_id=run_id,
        user_id=user_id,
        story_outline_id=primary.story_outline_id,
        scene_id=primary.scene_id or "",
        storyboard_detail_id=primary.storyboard_detail_id or "",
        detailed_shot_id=shot_id,
        keyframe_id=primary.uuid,
        keyframe_ids=kf_uuids,
    )
    row = await get_video_generation_by_uuid(new_id)
    if not row:
        raise BusinessException(BusinessExceptionCode.INTERNAL_SERVER_ERROR, "创建视频记录后读取失败")
    return row


def _regenerate_keyframe_version_from_db_rows(keyframe_db: Any, selected_kv: Any) -> KeyframeVersion:
    return KeyframeVersion(
        shot_number=keyframe_db.shot_number,
        is_bridge=keyframe_db.is_bridge,
        reference_image_urls=keyframe_db.reference_image_urls or [],
        keyframe_url=selected_kv.keyframe_url,
        t2i_prompt=selected_kv.t2i_prompt,
        provider=selected_kv.provider,
        success=selected_kv.success,
        error_msg=selected_kv.error_msg,
        audio_segment_ids=selected_kv.audio_segment_ids,
        version_id=selected_kv.uuid,
        keyframe_uuid=keyframe_db.uuid,
        scene_id=keyframe_db.scene_id,
        storyboard_detail_id=keyframe_db.storyboard_detail_id,
        detailed_shot_id=keyframe_db.detailed_shot_id,
    )


async def regenerate_append_video_generation_version_for_row(
    video_generation_db: Any,
    user_id: str,
    user_option: Optional[UserOption],
    custom_prompt: Optional[str],
    run_id: str,
) -> Dict[str, Any]:
    """在 video_generations 行上追加新版本（与 `_regenerate_video_version` 共用 generate / execute 路径）。"""
    from app.crud.video.video_generation import (
        create_video_generation_version,
        get_video_generation_versions_by_video_generation_ids,
    )
    from app.crud.video.video_keyframe import get_keyframe_by_uuid, get_keyframe_versions_by_keyframe_ids
    from app.crud.video.video_story import get_detailed_shot_by_id
    from app.schemas.video import VideoGenerationPrompt
    from app.services.agent.utils.database_utils import _str_attr, get_detailed_shots_from_db
    from app.services.agent.video.video_generation_service import (
        execute_single_video,
        generate_batch_videos,
        init_execution_context,
    )

    vg_user = getattr(video_generation_db, "user_id", None)
    if vg_user != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权限访问该视频生成记录",
        )
    keyframe_db = await get_keyframe_by_uuid(video_generation_db.keyframe_id) if video_generation_db.keyframe_id else None
    if not keyframe_db:
        raise BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "无法找到对应的关键帧信息")
    kf_ids = list(video_generation_db.keyframe_ids) if video_generation_db.keyframe_ids else [keyframe_db.uuid]
    kf_versions_db = await get_keyframe_versions_by_keyframe_ids(kf_ids)
    if not kf_versions_db:
        raise BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "关键帧没有版本数据")
    sorted_kv = sorted(kf_versions_db, key=lambda v: v.version_number)
    current_idx = getattr(keyframe_db, 'current_version_index', None)
    if current_idx is not None and 0 <= current_idx < len(sorted_kv):
        candidate = sorted_kv[current_idx]
        if candidate.success and (candidate.keyframe_url or "").strip():
            selected_kv = candidate
        else:
            selected_kv = next((v for v in reversed(sorted_kv) if v.success and (v.keyframe_url or "").strip()), None)
    else:
        selected_kv = next((v for v in reversed(sorted_kv) if v.success and (v.keyframe_url or "").strip()), None)
    if not selected_kv:
        selected_kv = sorted_kv[-1]
    logger.info("regenerate_video: keyframe=%s current_version_index=%s selected_kv=v%s (uuid=%s)",
                keyframe_db.uuid, current_idx, selected_kv.version_number, selected_kv.uuid)
    shot_row = await get_detailed_shot_by_id(keyframe_db.detailed_shot_id) if keyframe_db.detailed_shot_id else None
    if not shot_row:
        raise BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "无法找到对应的详细镜头信息")
    shots = await get_detailed_shots_from_db([shot_row.uuid])
    shot = shots[0]
    keyframe_version = _regenerate_keyframe_version_from_db_rows(keyframe_db, selected_kv)

    # 从 DB 获取 detected_language
    from .regenerate_keyframe_service import _get_detected_language_from_db
    detected_lang = await _get_detected_language_from_db(getattr(video_generation_db, "thread_id", None))

    # 从最新成功版本继承 audio_url，避免 lipsync 镜头重生成时丢失 audio
    lipsync_audio_url_map: Optional[Dict[int, str]] = None
    existing_vv = await get_video_generation_versions_by_video_generation_ids([video_generation_db.uuid])
    if existing_vv:
        sorted_vv = sorted(existing_vv, key=lambda v: v.version_number, reverse=True)
        prev_audio = next(
            (getattr(v, "audio_url", None) for v in sorted_vv if getattr(v, "audio_url", None)),
            None,
        )
        if prev_audio:
            lipsync_audio_url_map = {shot.shot_number: prev_audio}
            logger.info("regenerate_video: 继承 audio_url for shot_%s: %s", shot.shot_number, prev_audio[:60])

    cp = (custom_prompt or "").strip()
    if cp:
        custom_prompt_result = VideoGenerationPrompt(
            shot_number=shot.shot_number,
            i2v_prompt=cp,
            start_image_url=selected_kv.keyframe_url,
            end_image_url=None,
            needs_end_image=False,
            consistency_reason=None,
            generation_mode=getattr(shot_row, "generation_mode", None),
        )
        exec_ctx = await init_execution_context(
            user_option=user_option,
            lipsync_audio_url_map=lipsync_audio_url_map,
            detected_language=detected_lang,
            skip_consistency_check=True,
        )
        video_generation_version, _messages = await execute_single_video(
            keyframe_version, shot, custom_prompt_result, exec_ctx,
        )
    else:
        user_input = shot.scene_description or "生成视频片段"
        _ctx = {
            "run_id": run_id,
            "thread_id": getattr(video_generation_db, "thread_id", None),
            "conversation_id": getattr(video_generation_db, "conversation_id", None),
            "caller": "video_backfill_append",
        }
        _vm, video_generation_versions = await generate_batch_videos(
            keyframes_batch=[keyframe_version],
            shots_batch=[shot],
            user_input=user_input,
            prev_shot=None,
            next_shot=None,
            user_option=user_option,
            story_outline=None,
            lipsync_audio_url_map=lipsync_audio_url_map,
            detected_language=detected_lang,
            skip_consistency_check=True,
            log_context=_ctx,
        )
        video_generation_version = video_generation_versions[0]

    video_generation_version.video_generation_id = video_generation_db.uuid
    existing_versions = await get_video_generation_versions_by_video_generation_ids([video_generation_db.uuid])
    version_number = len(existing_versions) + 1

    new_version_uuid = None
    if video_generation_version.success:
        additional_data: Dict[str, Any] = {}
        if video_generation_version.seed is not None:
            additional_data["seed"] = video_generation_version.seed
        if video_generation_version.resolution:
            additional_data["resolution"] = video_generation_version.resolution
        _ref_urls = getattr(video_generation_version, "reference_image_urls", None) or []
        if _ref_urls:
            additional_data["reference_image_urls"] = list(_ref_urls)
        if getattr(video_generation_version, "preview_video_url", None):
            additional_data["preview_video_url"] = video_generation_version.preview_video_url
        new_version_uuid = await create_video_generation_version(
            video_generation_id=video_generation_db.uuid,
            version_number=version_number,
            shot_number=video_generation_version.shot_number,
            video_url=video_generation_version.video_url,
            provider=getattr(video_generation_version, "provider", None) or "",
            is_bridge=video_generation_version.is_bridge,
            success=video_generation_version.success,
            conversation_id=str(video_generation_db.conversation_id),
            thread_id=video_generation_db.thread_id,
            run_id=run_id,
            user_id=user_id,
            error_msg=video_generation_version.error_msg,
            keyframe_url=video_generation_version.keyframe_url,
            keyframe_version_ids=video_generation_version.keyframe_version_ids,
            motion_prompt=video_generation_version.i2v_prompt,
            duration=video_generation_version.duration,
            ai_messages=getattr(video_generation_version, "ai_messages_json", None),
            additional_data=additional_data if additional_data else None,
            audio_segment_ids=video_generation_version.audio_segment_ids,
            aspect_ratio=_str_attr(video_generation_version, "aspect_ratio"),
            resolution=_str_attr(video_generation_version, "resolution"),
            video_generation_tool=getattr(video_generation_db, "video_generation_tool", None),
            model=getattr(video_generation_version, "model", None),
            generation_mode=video_generation_version.generation_mode,
            audio_url=video_generation_version.audio_url,
            video_tool_metrics=getattr(video_generation_version, "video_tool_metrics", None),
            tool_duration_sec=getattr(video_generation_version, "tool_duration_sec", None),
            tool_cost=getattr(video_generation_version, "tool_cost", None),
        )
    return {
        "success": video_generation_version.success,
        "video_generation_version": video_generation_version,
        "new_version_uuid": new_version_uuid,
        "error_msg": video_generation_version.error_msg,
    }
