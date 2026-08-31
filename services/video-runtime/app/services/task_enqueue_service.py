"""
任务相关服务：入队（创建 run、SQS、Redis）与 Regenerate 执行（建 run、计费、调 service、回写 billing）。
- enqueue_video_task：供 agent_router（Form 提交）与 smart_testing（全自动/批量）复用。
- execute_regenerate_*：与前端 /video-editing/regenerate-* 共用同一套逻辑，供 HTTP 端点与 Smart Testing 调用。
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from langchain_core.runnables.config import RunnableConfig, var_child_runnable_config

from ..callbacks.credit_check_callback import create_credit_check_callback
from ..crud.conversation import (
    async_create_conversation_run,
    async_create_conversation_run_for_regenerate,
    async_update_conversation_run_billing,
    async_update_conversation_run_after_regenerate,
    async_get_conversation_run_by_run_id,
    async_get_task_status_cached,
    async_get_message_by_id,
    async_update_message_event_data_partial,
)
from ..exceptions import BusinessException, BusinessExceptionCode
from ..models.task_status import RunType, TaskStatus, BillingStatus
from ..services.redis.connection import get_redis_stream_service
from ..services.aws.sqs_service import SQSTaskService
from ..services.queue import create_task_queue
from ..utils.asyncpg_utils import utc_isoformat

logger = logging.getLogger(__name__)


def _collect_shot_numbers_from_keyframe_requests(keyframes: List[Any]) -> List[int]:
    out: List[int] = []
    for item in keyframes or []:
        sn = getattr(item, "shot_number", None)
        if sn is None and isinstance(item, dict):
            sn = item.get("shot_number")
        if sn is None:
            continue
        try:
            out.append(int(sn))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _collect_shot_numbers_from_video_requests(videos: List[Any]) -> List[int]:
    out: List[int] = []
    for item in videos or []:
        sn = getattr(item, "shot_number", None)
        if sn is None and isinstance(item, dict):
            sn = item.get("shot_number")
        if sn is None:
            continue
        try:
            out.append(int(sn))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _collect_character_uuids_from_character_requests(characters: List[Any]) -> List[str]:
    seen: List[str] = []
    for item in characters or []:
        cu = getattr(item, "uuid", None)
        if cu is None and isinstance(item, dict):
            cu = item.get("uuid") or item.get("character_uuid")
        u = str(cu or "").strip()
        if u and u not in seen:
            seen.append(u)
    return seen


async def enqueue_video_task(
    conversation_id: Union[int, str],
    conversation_uuid: str,
    thread_id: str,
    user_id: str,
    user_input: str,
    user_option: Optional[Dict[str, Any]] = None,
    user_input_files: Optional[Dict[str, Any]] = None,
    agent_type: str = "video",
    *,
    full_auto: bool = False,
    language: Optional[str] = None,
    skip_agent_router: bool = False,
) -> str:
    """
    创建 run、写入 DB、入队 SQS、更新 Redis。返回 run_id。
    供 agent_router 的 _submit_new_task 与 smart_testing 的 full_auto/批量 复用。
    skip_agent_router=True 时 Worker 跳过路由 LLM，使用 agent_type 与可选 language。
    不包含：conversation 获取/创建、user_option 解析、积分校验、文件上传解析。
    """
    run_id = str(uuid.uuid4())
    task_data = {
        "run_id": run_id,
        "thread_id": thread_id,
        "conversation_id": conversation_id,
        "conversation_uuid": conversation_uuid,
        "user_id": user_id,
        "agent_type": agent_type,
        "user_input": user_input,
        "user_option": user_option,
        "user_input_files": user_input_files,
        "resume_data": None,
        "created_at": utc_isoformat(datetime.utcnow()),
    }
    if full_auto:
        task_data["full_auto"] = True
    if full_auto and language:
        task_data["language"] = language
    if skip_agent_router:
        task_data["skip_agent_router"] = True
    if skip_agent_router and language:
        task_data["language"] = language

    await async_create_conversation_run(
        conversation_id=conversation_id,
        thread_id=thread_id,
        run_id=run_id,
        user_id=user_id,
        agent_type=agent_type,
        run_type=RunType.MAIN.value,
        user_option=user_option,
        user_input=user_input,
        user_input_files=user_input_files,
        status=TaskStatus.QUEUED.value,
        conversation_uuid=conversation_uuid,
        billing_status=None,
        additional_data=None,
    )
    redis_service = await get_redis_stream_service()
    sqs_service = create_task_queue()
    await sqs_service.add_task_to_queue(task_data)
    await redis_service.update_task_status(
        run_id,
        status=TaskStatus.QUEUED.value,
        user_id=user_id,
        thread_id=thread_id,
        conversation_id=str(conversation_id),
        conversation_uuid=conversation_uuid,
        created_at=utc_isoformat(datetime.utcnow()),
    )
    await redis_service.add_task_index(thread_id, run_id)
    return run_id


async def prepare_resume_task(resume_payload: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Resume 准备：校验老 run、创建新 run、更新 Redis，返回可入队的 task_data。供 API _submit_resume 与 worker 15s 自动 continue 复用。"""
    import json as _json
    old_run_id = str(resume_payload["run_id"])
    interrupt_msgid = resume_payload["interrupt_msgid"]
    resume_data_str = _json.dumps(resume_payload)

    run = await async_get_conversation_run_by_run_id(old_run_id)
    if not run:
        raise BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "未找到该 run，无法 resume")
    if run.user_id != user_id:
        raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "该 run 不属于当前用户")
    cached_status = await async_get_task_status_cached(old_run_id)
    if not cached_status or cached_status.get("status") != TaskStatus.INTERRUPTED.value:
        raise BusinessException(BusinessExceptionCode.RESUME_ALREADY_CONTINUED)
    try:
        msg_id_int = int(interrupt_msgid)
    except (TypeError, ValueError):
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "interrupt_msgid 必须为数字")
    msg = await async_get_message_by_id(msg_id_int)
    if not msg:
        raise BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "未找到该 interrupt 消息")
    if (msg.event_data or {}).get("continued"):
        raise BusinessException(BusinessExceptionCode.RESUME_ALREADY_CONTINUED)
    await async_update_message_event_data_partial(
        msg_id_int,
        {"continued": True, "continued_at": utc_isoformat(datetime.utcnow())}
    )

    new_run_id = str(uuid.uuid4())
    user_option_dict = None
    if run.user_option is not None:
        user_option_dict = _json.loads(run.user_option) if isinstance(run.user_option, str) else run.user_option
    user_input_files_dict = None
    if run.user_input_files is not None:
        user_input_files_dict = _json.loads(run.user_input_files) if isinstance(run.user_input_files, str) else run.user_input_files
    additional_data_dict = None
    if getattr(run, "additional_data", None) is not None:
        ad = run.additional_data
        additional_data_dict = _json.loads(ad) if isinstance(ad, str) else ad
    await async_create_conversation_run(
        conversation_id=run.conversation_id,
        thread_id=run.thread_id,
        run_id=new_run_id,
        user_id=run.user_id,
        agent_type=run.agent_type,
        run_type=RunType.RESUME.value,
        user_option=user_option_dict,
        user_input=run.user_input,
        user_input_files=user_input_files_dict,
        status=TaskStatus.RESUME_QUEUED.value,
        conversation_uuid=run.conversation_uuid,
        billing_status=None,
        additional_data=additional_data_dict,
    )
    redis_service = await get_redis_stream_service()
    await redis_service.update_task_status(
        new_run_id,
        status=TaskStatus.RESUME_QUEUED.value,
        user_id=run.user_id,
        thread_id=run.thread_id,
        conversation_id=run.conversation_id,
        conversation_uuid=run.conversation_uuid or "",
        updated_at=utc_isoformat(datetime.utcnow()),
    )
    return {
        "run_id": new_run_id,
        "thread_id": run.thread_id,
        "conversation_id": int(run.conversation_id),
        "conversation_uuid": run.conversation_uuid or "",
        "user_id": run.user_id,
        "agent_type": run.agent_type or "video",
        "user_input": run.user_input or "",
        "user_option": run.user_option,
        "user_input_files": run.user_input_files,
        "resume_data": resume_data_str,
        "interrupt_msgid": str(interrupt_msgid),
        "created_at": utc_isoformat(datetime.utcnow()),
    }


# ----- Regenerate 执行（建 run、计费、调 service、回写 billing），与前端 /video-editing/regenerate-* 共用 -----


async def execute_regenerate_keyframes(
    thread_id: str,
    run_id: str,
    user_id: str,
    keyframes: List[Any],
    user_option: Optional[Any] = None,
    langsmith_extra: Optional[Dict[str, Any]] = None,
    regenerate_source: Optional[str] = None,
) -> Dict[str, Any]:
    """执行关键帧 regenerate：建 run、计费、调 service、回写 billing。与前端接口同一逻辑。"""
    shot_numbers = _collect_shot_numbers_from_keyframe_requests(keyframes)
    ok = await async_create_conversation_run_for_regenerate(
        thread_id, run_id, user_id, RunType.REGENERATE_KEYFRAMES.value,
        shot_numbers=shot_numbers or None,
    )
    if not ok:
        logger.warning("Regenerate 插入 conversation_run 失败: run_id=%s", run_id)
    cost_callback = create_credit_check_callback(user_id=user_id, action="regenerate_keyframes")
    _config_token = var_child_runnable_config.set(RunnableConfig(callbacks=[cost_callback]))
    success = False
    try:
        from .agent.video_agent_service import get_video_agent_service
        video_agent_service = get_video_agent_service()
        result = await video_agent_service.regenerate_keyframes_by_request(
            keyframes=keyframes,
            user_id=user_id,
            user_option=user_option,
            langsmith_extra=langsmith_extra or {"run_id": run_id, "metadata": {"thread_id": thread_id} if thread_id else None},
            request_thread_id=thread_id or None,
        )
        success = True
        try:
            from .post_regenerate_interaction_service import (
                attach_propagate_item_anchors_to_result,
                persist_post_regenerate_interaction_message,
            )

            _res = result if isinstance(result, dict) else {}
            await attach_propagate_item_anchors_to_result(thread_id or "", "regenerate_keyframes", _res)
            await persist_post_regenerate_interaction_message(
                thread_id=thread_id or "",
                run_id=run_id,
                user_id=user_id,
                kind="regenerate_keyframes",
                regenerate_source=regenerate_source or "panel",
                result=_res,
            )
        except Exception as e:
            logger.warning("post_regenerate interaction persist skipped: %s", e)
        return result
    finally:
        var_child_runnable_config.reset(_config_token)
        callback_cost = cost_callback.total_cost
        logger.debug("Regenerate keyframes 成本: $%s, run_id=%s", f"{callback_cost:.6f}", run_id)
        if callback_cost > 0:
            try:
                await async_update_conversation_run_billing(run_id, BillingStatus.PENDING.value, cost=callback_cost)
            except Exception as e:
                logger.warning("写入 regenerate_keyframes callback 成本失败: %s", e)
        cost_callback.write_metadata_to_langsmith(run_id)
        await async_update_conversation_run_after_regenerate(run_id, TaskStatus.COMPLETED.value if success else TaskStatus.FAILED.value)


async def execute_regenerate_videos(
    thread_id: str,
    run_id: str,
    user_id: str,
    videos: List[Any],
    user_option: Optional[Any] = None,
    langsmith_extra: Optional[Dict[str, Any]] = None,
    regenerate_source: Optional[str] = None,
) -> Dict[str, Any]:
    """执行视频 regenerate：建 run、计费、调 service、回写 billing。与前端接口同一逻辑。"""
    shot_numbers = _collect_shot_numbers_from_video_requests(videos)
    ok = await async_create_conversation_run_for_regenerate(
        thread_id, run_id, user_id, RunType.REGENERATE_VIDEOS.value,
        shot_numbers=shot_numbers or None,
    )
    if not ok:
        logger.warning("Regenerate 插入 conversation_run 失败: run_id=%s", run_id)
    cost_callback = create_credit_check_callback(user_id=user_id, action="regenerate_videos")
    _config_token = var_child_runnable_config.set(RunnableConfig(callbacks=[cost_callback]))
    success = False
    try:
        from .agent.video_agent_service import get_video_agent_service
        video_agent_service = get_video_agent_service()
        result = await video_agent_service.regenerate_videos_by_request(
            videos=videos,
            user_id=user_id,
            user_option=user_option,
            langsmith_extra=langsmith_extra or {"run_id": run_id, "metadata": {"thread_id": thread_id} if thread_id else None},
            request_thread_id=thread_id or None,
        )
        success = True
        try:
            from .post_regenerate_interaction_service import (
                attach_propagate_item_anchors_to_result,
                persist_post_regenerate_interaction_message,
            )

            _res = result if isinstance(result, dict) else {}
            await attach_propagate_item_anchors_to_result(thread_id or "", "regenerate_videos", _res)
            await persist_post_regenerate_interaction_message(
                thread_id=thread_id or "",
                run_id=run_id,
                user_id=user_id,
                kind="regenerate_videos",
                regenerate_source=regenerate_source or "panel",
                result=_res,
            )
        except Exception as e:
            logger.warning("post_regenerate interaction persist skipped: %s", e)
        return result
    finally:
        var_child_runnable_config.reset(_config_token)
        callback_cost = cost_callback.total_cost
        logger.debug("Regenerate videos 成本: $%s, run_id=%s", f"{callback_cost:.6f}", run_id)
        if callback_cost > 0:
            try:
                await async_update_conversation_run_billing(run_id, BillingStatus.PENDING.value, cost=callback_cost)
            except Exception as e:
                logger.warning("写入 regenerate_videos callback 成本失败: %s", e)
        cost_callback.write_metadata_to_langsmith(run_id)
        await async_update_conversation_run_after_regenerate(run_id, TaskStatus.COMPLETED.value if success else TaskStatus.FAILED.value)


async def execute_regenerate_timeline(
    thread_id: str,
    run_id: str,
    user_id: str,
    video_versions: List[Any],
    user_option: Optional[Any] = None,
    langsmith_extra: Optional[Dict[str, Any]] = None,
    regenerate_source: Optional[str] = None,
) -> Dict[str, Any]:
    """将面板已选镜头视频版本同步到 segment 并重新合成成片；建 conversation_run（run_type=regenerate_timeline），与 execute_regenerate_* 终态回写一致。"""
    ok = await async_create_conversation_run_for_regenerate(thread_id, run_id, user_id, RunType.REGENERATE_TIMELINE.value)
    if not ok:
        logger.warning("Regenerate timeline 插入 conversation_run 失败: run_id=%s", run_id)
    cost_callback = create_credit_check_callback(user_id=user_id, action="regenerate_timeline")
    _config_token = var_child_runnable_config.set(RunnableConfig(callbacks=[cost_callback]))
    success = False
    try:
        from .agent.video_agent_service import get_video_agent_service

        video_agent_service = get_video_agent_service()
        if video_versions:
            await video_agent_service.sync_segments_by_request(
                thread_id=thread_id,
                video_versions=video_versions,
                user_id=user_id,
                user_option=user_option,
                force=True,
            )
        await video_agent_service.video_assembly_by_request(
            thread_id=thread_id,
            segment_versions=None,
            user_id=user_id,
            user_option=user_option,
            videos=None,
        )
        success = True
        return {"success": True, "run_id": run_id}
    finally:
        var_child_runnable_config.reset(_config_token)
        callback_cost = cost_callback.total_cost
        logger.debug("Regenerate timeline 成本: $%s, run_id=%s", f"{callback_cost:.6f}", run_id)
        if callback_cost > 0:
            try:
                await async_update_conversation_run_billing(run_id, BillingStatus.PENDING.value, cost=callback_cost)
            except Exception as e:
                logger.warning("写入 regenerate_timeline callback 成本失败: %s", e)
        cost_callback.write_metadata_to_langsmith(run_id)
        await async_update_conversation_run_after_regenerate(run_id, TaskStatus.COMPLETED.value if success else TaskStatus.FAILED.value)


async def execute_regenerate_characters(
    thread_id: str,
    run_id: str,
    user_id: str,
    characters: List[Any],
    user_option: Optional[Any] = None,
    langsmith_extra: Optional[Dict[str, Any]] = None,
    regenerate_source: Optional[str] = None,
) -> Dict[str, Any]:
    """执行角色 regenerate：建 run、计费、调 service、回写 billing。与前端接口同一逻辑。"""
    character_uuids = _collect_character_uuids_from_character_requests(characters)
    ok = await async_create_conversation_run_for_regenerate(
        thread_id, run_id, user_id, RunType.REGENERATE_CHARACTERS.value,
        character_uuids=character_uuids or None,
    )
    if not ok:
        logger.warning("Regenerate 插入 conversation_run 失败: run_id=%s", run_id)
    cost_callback = create_credit_check_callback(user_id=user_id, action="regenerate_characters")
    _config_token = var_child_runnable_config.set(RunnableConfig(callbacks=[cost_callback]))
    success = False
    try:
        from .agent.video_agent_service import get_video_agent_service
        video_agent_service = get_video_agent_service()
        result = await video_agent_service.regenerate_characters_by_request(
            characters=characters,
            user_id=user_id,
            user_option=user_option,
            langsmith_extra=langsmith_extra or {"run_id": run_id, "metadata": {"thread_id": thread_id} if thread_id else None},
        )
        success = True
        try:
            from .post_regenerate_interaction_service import (
                attach_propagate_item_anchors_to_result,
                persist_post_regenerate_interaction_message,
            )

            _res = result if isinstance(result, dict) else {}
            await attach_propagate_item_anchors_to_result(thread_id or "", "regenerate_characters", _res)
            await persist_post_regenerate_interaction_message(
                thread_id=thread_id or "",
                run_id=run_id,
                user_id=user_id,
                kind="regenerate_characters",
                regenerate_source=regenerate_source or "panel",
                result=_res,
            )
        except Exception as e:
            logger.warning("post_regenerate interaction persist skipped: %s", e)
        return result
    finally:
        var_child_runnable_config.reset(_config_token)
        callback_cost = cost_callback.total_cost
        logger.debug("Regenerate characters 成本: $%s, run_id=%s", f"{callback_cost:.6f}", run_id)
        if callback_cost > 0:
            try:
                await async_update_conversation_run_billing(run_id, BillingStatus.PENDING.value, cost=callback_cost)
            except Exception as e:
                logger.warning("写入 regenerate_characters callback 成本失败: %s", e)
        cost_callback.write_metadata_to_langsmith(run_id)
        await async_update_conversation_run_after_regenerate(run_id, TaskStatus.COMPLETED.value if success else TaskStatus.FAILED.value)
