"""
对话相关的数据库模型
"""

from sqlmodel import SQLModel, Field, Column, JSON, Text
from typing import Optional, Dict, Any
from datetime import datetime
import json
import uuid

from ..utils.asyncpg_utils import utc_isoformat


def _normalize_json_field(value: Any, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """JSON 字段可能是 str（DB 原始）或 dict（asyncpg 已解析），统一为 dict。"""
    if value is None:
        return default or {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value) or {}
        except (TypeError, ValueError):
            return default or {}
    return default or {}


class ConversationDB(SQLModel, table=True):
    """对话会话表"""
    __tablename__ = "conversations"  # type: ignore
    
    id: Optional[int] = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True, description="唯一标识符")
    user_id: str = Field(index=True, description="用户ID")
    thread_id: str = Field(unique=True, index=True, description="LangGraph线程ID")
    title: Optional[str] = Field(default=None, description="对话标题")
    
    # 首次对话信息（用于快速预览）
    user_input: Optional[str] = Field(default=None, sa_column=Column(Text), description="首次用户输入文本")
    user_input_files: Optional[str] = Field(default=None, sa_column=Column(JSON), description="首次用户输入文件(JSON)")
    user_option: Optional[str] = Field(default=None, sa_column=Column(JSON), description="首次用户选项(JSON)")
    agent_type: Optional[str] = Field(default=None, max_length=20, description="Agent类型")
    
    # 时间戳
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="更新时间")
    last_active_at: datetime = Field(default_factory=datetime.utcnow, description="最后活跃时间")
    
    # 对话状态
    is_active: bool = Field(default=True, description="是否活跃")
    message_count: int = Field(default=0, description="消息数量")
    
    # 元数据
    meta_data: Optional[str] = Field(default=None, sa_column=Column(JSON), description="对话元数据(JSON)")
    additional_data: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON), default=None, description="扩展数据(JSON)")


class ConversationRunDB(SQLModel, table=True):
    """对话运行记录表 - 记录每次 run 的详细信息"""
    __tablename__ = "conversation_runs"  # type: ignore
    
    id: Optional[int] = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True, description="唯一标识符")
    
    # 关联字段（使用字符串，不使用外键）
    conversation_id: str = Field(index=True, description="对话ID（关联 conversations.id）")
    conversation_uuid: Optional[str] = Field(default=None, index=True, description="对话UUID（关联 conversations.uuid）")
    thread_id: str = Field(index=True, description="LangGraph线程ID")
    run_id: str = Field(unique=True, index=True, description="LangGraph运行ID")
    user_id: str = Field(index=True, description="用户ID")
    
    # Run 信息
    agent_type: Optional[str] = Field(default=None, max_length=20, description="Agent类型")
    user_option: Optional[str] = Field(default=None, sa_column=Column(JSON), description="用户选项(JSON)")
    user_input: Optional[str] = Field(default=None, sa_column=Column(Text), description="用户输入文本")
    user_input_files: Optional[str] = Field(default=None, sa_column=Column(JSON), description="用户输入文件(JSON)")
    
    # 运行状态
    status: str = Field(default="running", description="运行状态: running, completed, failed, cancelled, queued, resume_queued")
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text), description="错误信息")
    
    # Run 类型与计费（成本记在 run 上；conversation_run 覆盖全类型，video_task_record 仅 video）
    run_type: Optional[str] = Field(default=None, max_length=32, description="run 类型: main, resume 等")
    cost_credits: Optional[float] = Field(default=None, description="扣减积分（dev 原有，兼容）")
    billing_status: Optional[str] = Field(default=None, max_length=32, description="计费状态: pending, completed, failed")
    langsmith_cost: Optional[float] = Field(default=None, description="LangSmith 成本（美元）")
    cost: Optional[float] = Field(default=None, description="我方统计成本（美元）")
    cost_calculated: Optional[bool] = Field(default=None, description="是否已计算成本")
    credits_deducted: Optional[bool] = Field(default=None, description="是否已扣积分")
    credits_amount: Optional[int] = Field(default=None, description="扣减积分数")
    
    # 时间戳
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="更新时间")
    completed_at: Optional[datetime] = Field(default=None, description="完成时间")
    
    # 扩展数据
    additional_data: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON), default=None, description="扩展数据(JSON)")


class ConversationMessageDB(SQLModel, table=True):
    """对话消息表"""
    __tablename__ = "conversation_messages"  # type: ignore
    
    id: Optional[int] = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True, description="唯一标识符")
    
    # 关联字段（保留原有的 int 类型和外键）
    conversation_id: int = Field(foreign_key="conversations.id", index=True, description="对话ID")
    conversation_uuid: Optional[str] = Field(default=None, index=True, description="对话UUID（关联 conversations.uuid）")
    run_id: Optional[str] = Field(default=None, index=True, description="运行ID（关联 conversation_runs.run_id）")
    
    # 消息内容
    role: str = Field(description="消息角色: human, ai, system")
    content: str = Field(sa_column=Column(Text), description="消息内容")
    sequence: int = Field(description="消息序号")
    
    # 时间戳
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")
    
    # 元数据
    meta_data: Optional[str] = Field(default=None, sa_column=Column(JSON), description="消息元数据(JSON)")
    
    # 事件数据 - 用于存储intermediateResults格式的事件
    event_type: Optional[str] = Field(default=None, description="事件类型")
    event_data: Optional[str] = Field(default=None, sa_column=Column(JSON), description="完整事件数据(JSON)")
    
    # 扩展数据
    additional_data: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON), default=None, description="扩展数据(JSON)")




# 工具函数
def conversation_to_dict(conversation) -> Dict[str, Any]:
    """将对话转换为字典 - 支持ORM对象或dict"""
    # 如果是dict，直接处理
    if isinstance(conversation, dict):
        result = {
            "id": conversation.get("id"),
            "uuid": conversation.get("uuid"),
            "user_id": conversation.get("user_id"),
            "thread_id": conversation.get("thread_id"),
            "title": conversation.get("title"),
            "created_at": utc_isoformat(conversation.get("created_at")),
            "updated_at": utc_isoformat(conversation.get("updated_at")),
            "last_active_at": utc_isoformat(conversation.get("last_active_at")),
            "is_active": conversation.get("is_active"),
            "message_count": conversation.get("message_count"),
            "agent_type": conversation.get("agent_type"),
            "metadata": _normalize_json_field(conversation.get("meta_data")),
            "additional_data": _normalize_json_field(conversation.get("additional_data")),
        }
        result["preview"] = conversation.get("user_input")
        result["language"] = (result.get("additional_data") or {}).get("language")
        uo_row = conversation.get("user_option")
        result["user_option"] = _normalize_json_field(uo_row) if uo_row else None
        if result["user_option"] == {}:
            result["user_option"] = None
        return result
    
    # ORM对象（ConversationResult 等，additional_data 可能已是 dict）
    result = {
        "id": conversation.id,
        "uuid": conversation.uuid,
        "user_id": conversation.user_id,
        "thread_id": conversation.thread_id,
        "title": conversation.title,
        "created_at": utc_isoformat(conversation.created_at),
        "updated_at": utc_isoformat(conversation.updated_at),
        "last_active_at": utc_isoformat(conversation.last_active_at),
        "is_active": conversation.is_active,
        "message_count": conversation.message_count,
        "agent_type": conversation.agent_type,
        "metadata": _normalize_json_field(conversation.meta_data),
        "additional_data": _normalize_json_field(getattr(conversation, "additional_data", None)),
    }
    result["preview"] = conversation.user_input
    result["language"] = (result.get("additional_data") or {}).get("language")
    uo = getattr(conversation, "user_option", None)
    result["user_option"] = _normalize_json_field(uo) if uo else None
    if result["user_option"] == {}:
        result["user_option"] = None
    return result


def message_to_dict(message) -> Dict[str, Any]:
    """将消息转换为字典 - 支持ORM对象或dict"""
    # 如果是dict，直接处理
    if isinstance(message, dict):
        result = {
            "id": message.get("id"),
            "uuid": message.get("uuid"),
            "conversation_id": message.get("conversation_id"),
            "run_id": message.get("run_id"),
            "role": message.get("role"),
            "content": message.get("content"),
            "created_at": utc_isoformat(message.get("created_at")),
            "sequence": message.get("sequence"),
            "metadata": json.loads(message.get("meta_data")) if message.get("meta_data") else {},
            "additional_data": json.loads(message.get("additional_data")) if message.get("additional_data") else {}
        }
        if message.get('event_type'):
            result["event_type"] = message["event_type"]
        if message.get('event_data'):
            result["event_data"] = json.loads(message["event_data"]) if isinstance(message["event_data"], str) else message["event_data"]
        return result
    
    # ORM对象
    result = {
        "id": message.id,
        "uuid": message.uuid,
        "conversation_id": message.conversation_id,
        "run_id": message.run_id,
        "role": message.role,
        "content": message.content,
        "created_at": utc_isoformat(message.created_at),
        "sequence": message.sequence,
        "metadata": json.loads(message.meta_data) if message.meta_data else {},
        "additional_data": json.loads(message.additional_data) if message.additional_data else {}
    }
    
    # 添加事件数据支持
    if hasattr(message, 'event_type') and message.event_type:
        result["event_type"] = message.event_type
        
    if hasattr(message, 'event_data') and message.event_data:
        try:
            result["event_data"] = json.loads(message.event_data) if isinstance(message.event_data, str) else message.event_data
        except Exception as e:
            print(f"解析event_data失败: {e}, 原始数据: {message.event_data}")
            result["event_data"] = {}
    
    return result