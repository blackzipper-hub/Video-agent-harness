"""
按 HTTP 请求编排关键帧 / 视频 regenerate：任务记录、逐条路由。

不同 keyframe / video 请求之间并行（asyncio.gather）；同一请求内多版本仍串行，避免版本号竞态。
由 VideoAgentService.regenerate_keyframes_by_request / regenerate_videos_by_request 委托调用。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.exceptions import BusinessException, BusinessExceptionCode

from app.services.agent.video.regenerate.regenerate_keyframe_service import (
    regenerate_append_keyframe_version_resolving_parent,
    regenerate_keyframe_from_version_uuid,
)
from app.services.agent.video.regenerate.regenerate_video_service import (
    regenerate_append_video_generation_version_for_row,
    regenerate_ensure_video_generation_row_for_shot,
)

logger = logging.getLogger(__name__)


def _coerce_version_regenerate_strategy(raw: Any) -> Any:
    """Pydantic 传入 StrEnum 时原样传给 parse_*；仅对 str 做 strip。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw.strip() or None
    return raw


async def regenerate_keyframes_by_request(
    service: Any,
    keyframes: List[Any],
    user_id: str,
    user_option: Optional[Any] = None,
    langsmith_extra: Optional[Dict[str, Any]] = None,
    request_thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    """根据请求重新生成关键帧。

    分支说明（为何不能「先补 DB 再无条件走 `_regenerate_keyframe_version`」）：
    - 历史路径：请求带 keyframe_version.uuid → 读库得到 version 行 → `_regenerate_keyframe_version`
      依赖该行做权限、从版本列重建 user_option、编辑记录、以及 version_number=len(已有)+1。
    - 空槽：没有 version 行（甚至无父行）。若在同一次 HTTP 请求里先跑一遍「补图」再调
      `_regenerate_keyframe_version`，会 **再跑一轮** 图生/LLM（双倍成本且多出一个无意义版本）。
      因此「首版补全」与 node 一致，用 `regenerate_single_keyframe` 一次写入（见
      `regenerate_append_keyframe_version_resolving_parent`）。
    - **补全之后**：前端再点再生会带上 uuid，自然走上面历史路径，无需额外分支。

    results 汇总对两种分支共用同一段逻辑。
    """
    from app.crud.error_tracking import create_task_record
    from langsmith import get_current_run_tree

    # 1. 创建任务记录（在方法开始时）
    run_tree = get_current_run_tree()
    run_id = str(run_tree.id) if run_tree and run_tree.id else str(uuid.uuid4())

    # 从第一个keyframe获取conversation_id和thread_id（如果存在）
    conversation_id = None  # 默认值
    thread_id = None  # 默认值

    if keyframes:
        first_kf = keyframes[0]
        fk = (getattr(first_kf, "uuid", None) or "").strip()
        if fk and first_kf.versions:
            from app.crud.video.video_keyframe import get_keyframe_by_uuid
            keyframe_db = await get_keyframe_by_uuid(fk)
            if keyframe_db:
                conversation_id = str(keyframe_db.conversation_id) if keyframe_db.conversation_id else None
                thread_id = keyframe_db.thread_id if keyframe_db.thread_id else None
    if (not conversation_id or not thread_id) and request_thread_id:
        from app.crud.conversation import async_get_conversation_by_thread_id
        conv = await async_get_conversation_by_thread_id(request_thread_id)
        if conv:
            conversation_id = conversation_id or str(conv.id)
            thread_id = thread_id or request_thread_id

    # 创建任务记录（使用asyncpg CRUD）
    from app.models.task_status import TaskStatus
    state_for_record = {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "task_input": f"Regenerate keyframes: {len(keyframes)} keyframes",
        "task_status": TaskStatus.RUNNING.value,
        "task_start_time": datetime.utcnow()
    }
    task_record_uuid = await create_task_record(state=state_for_record)

    async def _regenerate_one_keyframe_req(keyframe_req: Any) -> List[Dict[str, Any]]:
        local: List[Dict[str, Any]] = []
        kf_req_uuid = (getattr(keyframe_req, "uuid", None) or "").strip()
        frame_index = int(getattr(keyframe_req, "frame_index", 0) or 0)
        for version_req in keyframe_req.versions:
            version_uuid = (getattr(version_req, "uuid", None) or "").strip()
            custom_prompt = version_req.custom_prompt
            instruction = getattr(version_req, "instruction", None)
            if isinstance(instruction, str):
                instruction = instruction.strip() or None
            else:
                instruction = None
            use_reflection = getattr(version_req, 'use_reflection', False)  # AI improve 功能
            _strategy = _coerce_version_regenerate_strategy(getattr(version_req, "regenerate_strategy", None))
            new_video_version_uuid = None
            keyframe_uuid_for_result = kf_req_uuid
            keyframe_version = None
            result: Dict[str, Any] = {}

            if version_uuid:
                keyframe_uuid_for_result, result = await regenerate_keyframe_from_version_uuid(
                    service,
                    version_uuid,
                    custom_prompt=custom_prompt,
                    instruction=instruction,
                    use_reflection=use_reflection,
                    user_id=user_id,
                    user_option=user_option,
                    fallback_keyframe_uuid=kf_req_uuid,
                    regenerate_strategy=_strategy,
                )
            else:
                # 无 version 行：一次图生落库（与 pipeline 的 regenerate_single_keyframe 一致）
                if use_reflection:
                    logger.warning("无 keyframe_version UUID 时不支持 reflection，已按普通生成处理")
                if not kf_req_uuid and not request_thread_id:
                    raise BusinessException(
                        BusinessExceptionCode.BUSINESS_ERROR,
                        "空槽补全关键帧需要请求携带 thread_id",
                    )
                shot_n = getattr(keyframe_req, "shot_number", None)
                if not kf_req_uuid and shot_n is None:
                    raise BusinessException(
                        BusinessExceptionCode.BUSINESS_ERROR,
                        "空槽补全关键帧需要 shot_number",
                    )
                keyframe_uuid_for_result, result = await regenerate_append_keyframe_version_resolving_parent(
                    user_id=user_id,
                    user_option=user_option,
                    custom_prompt=custom_prompt,
                    run_id=run_id,
                    keyframe_uuid=kf_req_uuid,
                    thread_id=request_thread_id,
                    shot_number=int(shot_n) if shot_n is not None else None,
                    frame_index=frame_index,
                )

            new_version_uuid = result.get("new_version_uuid")
            keyframe_version = result.get("keyframe_version")
            storyboard_edit_record_id = result.get("storyboard_edit_record_id")

            _kf_url = getattr(keyframe_version, "keyframe_url", None) if keyframe_version else None
            _t2i = getattr(keyframe_version, "t2i_prompt", None) if keyframe_version else None
            local.append({
                "keyframe_uuid": keyframe_uuid_for_result,
                "original_version_uuid": version_uuid or "",
                "new_version_uuid": new_version_uuid,
                "new_video_version_uuid": new_video_version_uuid,
                "success": result.get("success", False),
                "keyframe_url": _kf_url if result.get("success") else None,
                "t2i_prompt": _t2i if result.get("success") else None,
                "error": result.get("error_msg") if not result.get("success") else None
            })
        return local

    total_versions = sum(len(getattr(kr, "versions", ()) or ()) for kr in keyframes)
    if keyframes:
        groups = await asyncio.gather(*[_regenerate_one_keyframe_req(kr) for kr in keyframes])
        results: List[Dict[str, Any]] = [row for grp in groups for row in grp]
    else:
        results = []

    successful_count = len([r for r in results if r["success"]])
    final_status = "completed" if successful_count > 0 else "failed"

    # 3. task_record：终态（billing 已迁至 conversation_run，由 worker 扫描 conversation_runs 扣费）
    try:
        from app.crud.error_tracking import update_task_record

        await update_task_record(
            run_id=run_id,
            updates={
                "task_status": final_status,
                "task_finish_time": datetime.utcnow(),
            }
        )
    except Exception as e:
        logger.error(f"Regenerate keyframes 任务记录更新失败: error={e}")

    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "thread_id": thread_id,
        "total_versions": total_versions,
        "successful_count": successful_count,
        "failed_count": total_versions - successful_count,
        "results": results
    }


async def regenerate_videos_by_request(
    service: Any,
    videos: List[Any],
    user_id: str,
    user_option: Optional[Any] = None,
    langsmith_extra: Optional[Dict[str, Any]] = None,
    request_thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    """根据请求重新生成视频"""
    from app.crud.video.video_generation import get_video_generation_version_by_uuid, get_video_generation_by_uuid
    from app.crud.error_tracking import create_task_record
    from langsmith import get_current_run_tree

    # 1. 创建任务记录（在方法开始时）
    run_tree = get_current_run_tree()
    run_id = str(run_tree.id) if run_tree and run_tree.id else str(uuid.uuid4())

    # 从第一个video获取conversation_id和thread_id（如果存在）
    conversation_id = None  # 默认值
    thread_id = None  # 默认值

    if videos:
        first_v = videos[0]
        fv = (getattr(first_v, "uuid", None) or "").strip()
        if fv and first_v.versions:
            video_db = await get_video_generation_by_uuid(fv)
            if video_db:
                _conv = video_db.get('conversation_id') if isinstance(video_db, dict) else getattr(video_db, 'conversation_id', None)
                _tid = video_db.get('thread_id') if isinstance(video_db, dict) else getattr(video_db, 'thread_id', None)
                conversation_id = str(_conv) if _conv else None
                thread_id = _tid
    if (not conversation_id or not thread_id) and request_thread_id:
        from app.crud.conversation import async_get_conversation_by_thread_id
        conv = await async_get_conversation_by_thread_id(request_thread_id)
        if conv:
            conversation_id = conversation_id or str(conv.id)
            thread_id = thread_id or request_thread_id

    # 创建任务记录（使用asyncpg CRUD）
    from app.models.task_status import TaskStatus
    state_for_record = {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "task_input": f"Regenerate videos: {len(videos)} videos",
        "task_status": TaskStatus.RUNNING.value,
        "task_start_time": datetime.utcnow()
    }
    task_record_uuid = await create_task_record(state=state_for_record)

    async def _regenerate_one_video_req(video_req: Any) -> List[Dict[str, Any]]:
        local: List[Dict[str, Any]] = []
        vid_req_uuid = (getattr(video_req, "uuid", None) or "").strip()
        for version_req in video_req.versions:
            version_uuid = (getattr(version_req, "uuid", None) or "").strip()
            custom_prompt = version_req.custom_prompt
            instruction = getattr(version_req, "instruction", None)
            if isinstance(instruction, str):
                instruction = instruction.strip() or None
            else:
                instruction = None
            _v_strategy = _coerce_version_regenerate_strategy(getattr(version_req, "regenerate_strategy", None))
            _forced_kf = (getattr(version_req, "forced_keyframe_version_uuid", None) or "").strip() or None
            video_uuid_for_result = vid_req_uuid
            result: Dict[str, Any] = {}
            video_generation_version = None

            if version_uuid:
                version_db_obj = await get_video_generation_version_by_uuid(version_uuid)
                if not version_db_obj:
                    raise BusinessException(
                        BusinessExceptionCode.RESOURCE_NOT_FOUND,
                        f"视频版本 {version_uuid} 不存在"
                    )
                version_user_id = version_db_obj.get('user_id') if isinstance(version_db_obj, dict) else getattr(version_db_obj, 'user_id', None)
                if version_user_id != user_id:
                    raise BusinessException(
                        BusinessExceptionCode.PERMISSION_DENIED,
                        f"无权限访问视频版本 {version_uuid}"
                    )
                from app.services.agent.utils.user_option_rebuild import rebuild_user_option_from_video_version
                effective_user_option = rebuild_user_option_from_video_version(version_db_obj, fallback=user_option)
                result = await service._regenerate_video_version(
                    version_db_obj,
                    custom_prompt,
                    user_id,
                    effective_user_option,
                    instruction=instruction,
                    regenerate_strategy=_v_strategy,
                    forced_keyframe_version_uuid=_forced_kf,
                )
                vgid = version_db_obj.get("video_generation_id") if isinstance(version_db_obj, dict) else getattr(version_db_obj, "video_generation_id", None)
                video_uuid_for_result = str(vgid) if vgid else vid_req_uuid
            elif vid_req_uuid:
                vg_row = await get_video_generation_by_uuid(vid_req_uuid)
                if not vg_row:
                    raise BusinessException(
                        BusinessExceptionCode.RESOURCE_NOT_FOUND,
                        f"视频生成记录 {vid_req_uuid} 不存在",
                    )
                vg_uid = vg_row.get("user_id") if isinstance(vg_row, dict) else getattr(vg_row, "user_id", None)
                if vg_uid != user_id:
                    raise BusinessException(
                        BusinessExceptionCode.PERMISSION_DENIED,
                        f"无权限访问视频生成记录 {vid_req_uuid}",
                    )
                result = await regenerate_append_video_generation_version_for_row(
                    vg_row, user_id, user_option, custom_prompt, run_id
                )
                video_uuid_for_result = vid_req_uuid
            else:
                if not request_thread_id:
                    raise BusinessException(
                        BusinessExceptionCode.BUSINESS_ERROR,
                        "空槽补全视频需要请求携带 thread_id",
                    )
                shot_n = getattr(video_req, "shot_number", None)
                if shot_n is None:
                    raise BusinessException(
                        BusinessExceptionCode.BUSINESS_ERROR,
                        "空槽补全视频需要 shot_number",
                    )
                from app.crud.conversation import async_get_conversation_by_thread_id
                conv = await async_get_conversation_by_thread_id(request_thread_id)
                if not conv or conv.user_id != user_id:
                    raise BusinessException(
                        BusinessExceptionCode.PERMISSION_DENIED,
                        "无权访问此 thread 或对话不存在",
                    )
                conv_id = str(conv.id)
                vg_row = await regenerate_ensure_video_generation_row_for_shot(
                    conv_id, request_thread_id, int(shot_n), user_id, run_id
                )
                result = await regenerate_append_video_generation_version_for_row(
                    vg_row, user_id, user_option, custom_prompt, run_id
                )
                video_uuid_for_result = getattr(vg_row, "uuid", None) or ""

            new_version_uuid = result.get("new_version_uuid")
            video_generation_version = result.get("video_generation_version")
            _vurl = getattr(video_generation_version, "video_url", None) if video_generation_version else None
            _mot = None
            if video_generation_version:
                _mot = getattr(video_generation_version, "i2v_prompt", None) or getattr(
                    video_generation_version, "motion_prompt", None
                )

            local.append({
                "video_uuid": video_uuid_for_result,
                "original_version_uuid": version_uuid or "",
                "new_version_uuid": new_version_uuid,
                "success": result.get("success", False),
                "video_url": _vurl if result.get("success") else None,
                "motion_prompt": _mot if result.get("success") else None,
                "error": result.get("error_msg") if not result.get("success") else None
            })
        return local

    total_versions = sum(len(getattr(vr, "versions", ()) or ()) for vr in videos)
    if videos:
        groups = await asyncio.gather(*[_regenerate_one_video_req(vr) for vr in videos])
        results: List[Dict[str, Any]] = [row for grp in groups for row in grp]
    else:
        results = []

    successful_count = len([r for r in results if r["success"]])
    final_status = "completed" if successful_count > 0 else "failed"

    # 3. task_record：终态（billing 已迁至 conversation_run）
    try:
        from app.crud.error_tracking import update_task_record

        await update_task_record(
            run_id=run_id,
            updates={
                "task_status": final_status,
                "task_finish_time": datetime.utcnow(),
            }
        )
    except Exception as e:
        logger.error(f"Regenerate videos 任务记录更新失败: error={e}")

    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "thread_id": thread_id,
        "total_versions": total_versions,
        "successful_count": successful_count,
        "failed_count": total_versions - successful_count,
        "results": results
    }
