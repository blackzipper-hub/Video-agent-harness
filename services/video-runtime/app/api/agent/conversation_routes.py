"""
对话历史相关的API路由
"""

import json
from datetime import datetime

from fastapi import APIRouter, Security
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

from ...crud.conversation import (
    async_get_user_conversations,
    async_get_conversation_by_thread_id,
    async_get_conversation_messages,
    async_deactivate_conversation,
    async_get_conversation_runs,
    conversation_to_dict,
    message_to_dict
)
from ...crud.video.video_other import async_search_user_creations
from ...services.auth_service import auth_service
from ...schemas import ResponseModel
from ...exceptions import BusinessException, BusinessExceptionCode
from ...utils.asyncpg_utils import utc_isoformat

router = APIRouter()

# Response Models
class ConversationListResponse(BaseModel):
    conversations: List[Dict[str, Any]]
    page: int
    size: int
    total: int

class ConversationDetailResponse(BaseModel):
    conversation: Dict[str, Any]
    messages: List[Dict[str, Any]]
    tasks: List[Dict[str, Any]]  # 该对话的所有任务（使用 run_id）
    # 按 created_at DESC 第一条带 user_option 的 run（即最近一次提交的选项快照）
    latest_user_option: Optional[Dict[str, Any]] = None


def _run_user_option_to_dict(run: Any) -> Optional[Dict[str, Any]]:
    """将 run.user_option（JSON 列或 str）解析为 dict，供前端恢复面板。"""
    raw = getattr(run, "user_option", None)
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw if raw else None
    if isinstance(raw, str):
        try:
            out = json.loads(raw)
            return out if isinstance(out, dict) and out else None
        except (TypeError, ValueError):
            return None
    return None

class MessageListResponse(BaseModel):
    messages: List[Dict[str, Any]]

class DeleteResponse(BaseModel):
    message: str


class ConversationListRequest(BaseModel):
    """获取对话列表请求

    可选检索参数：q 按标题/首条用户输入做子串匹配；start_time/end_time 按 last_active_at
    做时间范围过滤；agent_type 精确过滤。均为可选，不传则与原列表行为一致。
    """
    page: int = 1
    size: int = 20
    q: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    agent_type: Optional[str] = None

@router.post("/conversations", response_model=ResponseModel[ConversationListResponse])
async def get_conversations(
    request: ConversationListRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """获取用户的对话列表（使用 thread_id），支持按文字与时间检索"""
    conversations, total = await async_get_user_conversations(
        user_id,
        request.page,
        request.size,
        q=request.q,
        start_time=request.start_time,
        end_time=request.end_time,
        agent_type=request.agent_type,
    )
    
    result = ConversationListResponse(
        conversations=[conversation_to_dict(conv) for conv in conversations],
        page=request.page,
        size=request.size,
        total=total
    )
    return ResponseModel.success(data=result)


class CreationSearchResponse(BaseModel):
    creations: List[Dict[str, Any]]
    page: int
    size: int
    total: int


class CreationSearchRequest(BaseModel):
    """检索「我的作品」（成片）请求：q 按标题/首条用户输入做子串匹配，时间按 created_at 过滤。"""
    page: int = 1
    size: int = 20
    q: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    success_only: bool = True


@router.post("/creations/search", response_model=ResponseModel[CreationSearchResponse])
async def search_creations(
    request: CreationSearchRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """检索用户成片作品（video_assemblies），支持按文字与时间检索"""
    creations, total = await async_search_user_creations(
        user_id,
        request.page,
        request.size,
        q=request.q,
        start_time=request.start_time,
        end_time=request.end_time,
        success_only=request.success_only,
    )
    # created_at 序列化为 ISO 字符串，前端可直接用于显示/分组
    for item in creations:
        if isinstance(item.get("created_at"), datetime):
            item["created_at"] = utc_isoformat(item["created_at"])

    result = CreationSearchResponse(
        creations=creations,
        page=request.page,
        size=request.size,
        total=total,
    )
    return ResponseModel.success(data=result)


class ConversationDetailRequest(BaseModel):
    """获取对话详情请求"""
    thread_id: str

@router.post("/conversation/detail", response_model=ResponseModel[ConversationDetailResponse])
async def get_conversation_detail(
    request: ConversationDetailRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """获取对话详情和消息历史（使用 thread_id）"""
    thread_id = request.thread_id
    # 获取对话信息
    conversation = await async_get_conversation_by_thread_id(thread_id)
    if not conversation:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "对话不存在"
        )
    
    # 检查权限
    if conversation.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权访问此对话"
        )
    
    # 获取消息历史
    all_messages = await async_get_conversation_messages(conversation.id)
    
    # 使用所有消息（不再过滤内部工作流事件，因为这些事件已经不再使用）
    filtered_messages = all_messages
    
    # 获取该对话的所有任务（使用 run_id）；ORDER BY created_at DESC 故 all_runs[0] 为最新 run
    all_runs = await async_get_conversation_runs(conversation.id)
    tasks = []
    latest_user_option: Optional[Dict[str, Any]] = None
    for run in all_runs:
        uo_dict = _run_user_option_to_dict(run)
        if latest_user_option is None and uo_dict:
            latest_user_option = uo_dict
        task_item = {
            'run_id': run.run_id,
            'status': run.status,
            'run_type': getattr(run, 'run_type', None),
            'created_at': utc_isoformat(run.created_at),
            'completed_at': utc_isoformat(run.completed_at),
            'error_message': run.error_message,
            'user_option': uo_dict,
        }
        run_lang = (run.additional_data or {}).get("language") if getattr(run, "additional_data", None) else None
        if run_lang is not None:
            task_item["language"] = run_lang
        run_add = run.additional_data if getattr(run, "additional_data", None) else None
        if isinstance(run_add, dict):
            if run_add.get("shot_numbers") is not None:
                task_item["shot_numbers"] = run_add.get("shot_numbers")
            if run_add.get("character_uuids") is not None:
                task_item["character_uuids"] = run_add.get("character_uuids")
        tasks.append(task_item)
    
    # P0-D：默认走既有 message_to_dict；开关开启后对每条消息走字段白名单过滤。
    from ...config import get_settings as _get_settings_shield
    if _get_settings_shield().SHIELD_CONVERSATION_DETAIL_SANITIZE_ENABLED:
        from ...services.agent.conversation_sanitize import sanitize_message_for_public
        messages_out = []
        for msg in filtered_messages:
            as_dict = message_to_dict(msg)
            cleaned = sanitize_message_for_public(as_dict)
            if cleaned is not None:
                messages_out.append(cleaned)
    else:
        messages_out = [message_to_dict(msg) for msg in filtered_messages]

    result = ConversationDetailResponse(
        conversation=conversation_to_dict(conversation),
        messages=messages_out,
        tasks=tasks,
        latest_user_option=latest_user_option,
    )
    return ResponseModel.success(data=result)


class ConversationTaskDetailRequest(BaseModel):
    """获取对话下视频任务详情请求（与 admin 视频任务详情同结构，仅鉴权为当前用户）"""
    run_id: str


class DeleteConversationRequest(BaseModel):
    """删除对话请求"""
    thread_id: str

@router.post("/conversation/task-detail", response_model=ResponseModel[Dict[str, Any]])
async def get_conversation_task_detail(
    request: ConversationTaskDetailRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """
    获取当前用户某次 run 的完整任务详情（与 admin 视频任务详情同一套数据）。
    run_id 即 task_id（LangGraph run_id）。仅当该任务属于当前用户时返回。
    """
    from ...crud.error_tracking import get_task_record_by_run_id
    from ...services.task_detail_service import build_task_full_data

    record = await get_task_record_by_run_id(request.run_id)
    if not record:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "任务记录不存在",
        )
    if str(getattr(record, "user_id", "")) != str(user_id):
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权访问此任务",
        )
    full_data = await build_task_full_data(record)
    return ResponseModel.success(data=full_data)


@router.post("/conversation/delete", response_model=ResponseModel[DeleteResponse])
async def delete_conversation_endpoint(
    request: DeleteConversationRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """删除对话（软删除，使用 thread_id）"""
    thread_id = request.thread_id
    # 获取对话信息
    conversation = await async_get_conversation_by_thread_id(thread_id)
    if not conversation:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "对话不存在"
        )
    
    # 检查权限并删除（软删除：设置is_active=False）
    if conversation.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权删除此对话"
        )
    
    success = await async_deactivate_conversation(conversation.id)
    if not success:
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            "删除对话失败"
        )
    
    result = DeleteResponse(message="对话删除成功")
    return ResponseModel.success(data=result)
