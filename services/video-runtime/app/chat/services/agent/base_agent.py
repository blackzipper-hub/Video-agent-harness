"""
基础Agent类 - 提供统一的消息管理和事件发送功能
所有Agent服务都应该继承这个基类
（迁移自 Cuti-VideoAgent，与 VideoAgent 合并使用时 ... 指向 chat_agent）
"""

import logging
import asyncio
import uuid
from typing import Optional, Dict, Any, AsyncGenerator, List
from datetime import datetime
from enum import Enum
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session
from langgraph.config import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langchain_core.messages import AIMessage
from ...exceptions import BusinessException, BusinessExceptionCode
from ...utils.asyncpg_utils import utc_isoformat

logger = logging.getLogger(__name__)


class MessageRole(str, Enum):
    """消息角色枚举"""
    HUMAN = "human"
    AI = "ai"
    SYSTEM = "system"


class MessageType(str, Enum):
    """消息类型枚举 - 与 Cuti-VideoAgent 保持一致"""
    SESSION_CREATED = "session_created"
    AGENT_TYPE_DETERMINED = "agent_type_determined"
    USER_INPUT = "user_input"
    VIDEO_ELIGIBILITY_REQUIRED = "video_eligibility_required"
    VIDEO_ANALYSIS = "video_analysis"
    STORY_OUTLINE_GENERATED = "story_outline_generated"
    SCENES_GENERATED = "scenes_generated"
    STORYBOARD_DETAIL_GENERATED = "storyboard_detail_generated"
    CHARACTERS_DESIGNED = "characters_designed"
    MUSIC_GENERATED = "music_generated"
    MUSIC_GENERATION_PROGRESS = "music_generation_progress"
    KEYFRAMES_GENERATED = "keyframes_generated"
    KEYFRAME_GENERATION_PROGRESS = "keyframe_generation_progress"
    IMAGE_MODEL_SWITCHED = "image_model_switched"
    KEYFRAME_REFLECTION_PROGRESS = "keyframe_reflection_progress"
    KEYFRAMES_REFLECTION_COMPLETED = "keyframes_reflection_completed"
    NARRATIONS_GENERATED = "narrations_generated"
    AUDIO_EFFECTS_GENERATED = "audio_effects_generated"
    VIDEO_SEGMENTS_GENERATED = "video_segments_generated"
    VIDEO_GENERATION_PROGRESS = "video_generation_progress"
    VIDEO_COMPLETED = "video_completed"
    VIDEO_SEGMENTS_ASSEMBLED = "video_segments_assembled"
    VIDEO_SEGMENTS_PROGRESS = "video_segments_progress"
    VIDEO_LIPSYNC_PROGRESS = "video_lipsync_progress"
    VIDEO_LIPSYNC_COMPLETED = "video_lipsync_completed"
    STORY_AGENT_GENERATED = "story_agent_generated"
    MUSIC_AGENT_GENERATED = "music_agent_generated"
    IMAGE_AGENT_GENERATED = "image_agent_generated"
    CHAT_RESPONSE = "chat_response"
    CLARIFY_RESPONSE = "clarify_response"
    AGENT_CONFIRMATION_READY = "agent_confirmation_ready"
    INTERRUPT = "interrupt"
    ERROR = "error"
    STREAMING_CHUNK = "streaming_chunk"
    CANCELLED = "cancelled"
    STREAM_END = "stream_end"
    WORKFLOW_STATE = "workflow_state"


class BaseAgent:
    """基础Agent类 - 事件发送、DB、Redis Stream 等（与 VideoAgent 同构合并时 ... 指向 chat_agent）"""

    def __init__(self):
        pass

    async def async_send_event(
        self,
        event_type: MessageType,
        conversation_id: Optional[int] = None,
        conversation_uuid: Optional[str] = None,
        message: Optional[str] = None,
        role: MessageRole = MessageRole.AI,
        extra_data: Optional[Dict[str, Any]] = None,
        save_to_db: bool = True,
        send_to_stream: bool = True,
        hidden: bool = False,
        run_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        try:
            if message is None:
                message = self._get_default_message(event_type, extra_data)
            if not run_id and extra_data and "run_id" in extra_data:
                run_id = extra_data.get("run_id")
            message_id = None
            if save_to_db and conversation_id:
                try:
                    from ...services.redis.stream_service import PROGRESS_EVENTS
                except Exception:
                    PROGRESS_EVENTS = set()
                should_save_to_db = event_type != MessageType.STREAMING_CHUNK and event_type.value not in PROGRESS_EVENTS
                if should_save_to_db:
                    try:
                        from ...crud.conversation import async_add_message_to_conversation
                        message_obj = await async_add_message_to_conversation(
                            conversation_id=conversation_id,
                            conversation_uuid=conversation_uuid,
                            role=role.value,
                            content=message,
                            event_type=event_type.value,
                            event_data=extra_data or {},
                            run_id=run_id
                        )
                        message_id = message_obj['id']
                    except Exception as e:
                        logger.error(f"保存事件到数据库失败: {e}")
            if hidden:
                extra_data = {**(extra_data or {}), "hidden": True}
            event_data = {
                "type": event_type.value,
                "message": message,
                "timestamp": utc_isoformat(datetime.utcnow()),
                "message_id": message_id,
                "conversation_id": conversation_id,
                "conversation_uuid": conversation_uuid,
                "run_id": run_id,
                **(extra_data or {})
            }
            if send_to_stream and run_id:
                try:
                    from ...services.redis.connection import get_redis_stream_service
                    from ...services.redis.stream_service import GENERATED_EVENTS
                    from ...models.task_status import TaskStatus
                    redis_service = await get_redis_stream_service()
                    stream_id = await redis_service.add_message(run_id, event_data)
                    if event_type.value in GENERATED_EVENTS:
                        current_status = await redis_service.get_task_status(run_id)
                        current_status_value = current_status.get('status', TaskStatus.RUNNING.value) if current_status else TaskStatus.RUNNING.value
                        await redis_service.update_task_status(
                            run_id, status=current_status_value,
                            last_generated_event=event_type.value,
                            last_generated_id=stream_id,
                            conversation_uuid=conversation_uuid
                        )
                except Exception as e:
                    logger.error(f"发送事件到Redis Stream失败: {e}")
            if send_to_stream:
                try:
                    from langgraph.config import get_stream_writer
                    writer = get_stream_writer()
                    await asyncio.to_thread(writer, event_data)
                except Exception as e:
                    logger.debug(f"发送事件到HTTP stream失败: {e}")
            return event_data
        except Exception as e:
            logger.error(f"异步发送事件失败: {e}")
            return None

    async def async_update_event(
        self, message_id: int, extra_data: Optional[Dict[str, Any]] = None, update_db: bool = True
    ):
        if update_db:
            try:
                from ...crud.conversation import async_update_message_event_data_partial
                await async_update_message_event_data_partial(message_id=message_id, partial_data=extra_data)
            except Exception as e:
                logger.error(f"异步更新部分事件数据失败: {e}")

    def _get_default_message(self, event_type: MessageType, extra_data: Optional[Dict[str, Any]] = None) -> str:
        extra_data = extra_data or {}
        default_messages = {
            MessageType.USER_INPUT: lambda: extra_data.get("content", "用户输入"),
            MessageType.ERROR: lambda: f"错误: {extra_data.get('error_message', '未知错误')}",
            MessageType.CHARACTERS_DESIGNED: lambda: "角色设计完成",
            MessageType.MUSIC_GENERATED: lambda: "背景音乐生成完成",
            MessageType.VIDEO_COMPLETED: lambda: "视频拼接完成",
            MessageType.VIDEO_ANALYSIS: lambda: "视频分析",
            MessageType.STORY_OUTLINE_GENERATED: lambda: "故事大纲生成完成",
            MessageType.STORYBOARD_DETAIL_GENERATED: lambda: "详细分镜生成完成",
            MessageType.KEYFRAMES_GENERATED: lambda: "关键帧生成完成",
            MessageType.KEYFRAME_GENERATION_PROGRESS: lambda: f"关键帧生成进度: {extra_data.get('progress', '0%')}",
            MessageType.KEYFRAME_REFLECTION_PROGRESS: lambda: f"关键帧反思进度: {extra_data.get('completed', 0)}/{extra_data.get('total', 0)}",
            MessageType.KEYFRAMES_REFLECTION_COMPLETED: lambda: "关键帧反思完成",
            MessageType.IMAGE_MODEL_SWITCHED: lambda: extra_data.get("message") or extra_data.get("message_key") or "指定的模型因使用量暂时无法调用，已自动重试别的模型完成生成。",
            MessageType.VIDEO_SEGMENTS_GENERATED: lambda: "视频片段生成完成",
            MessageType.SESSION_CREATED: lambda: "会话已创建",
            MessageType.AGENT_TYPE_DETERMINED: lambda: "Agent 类型已确定",
            MessageType.CHAT_RESPONSE: lambda: "聊天回复已生成",
            MessageType.AGENT_CONFIRMATION_READY: lambda: "生成信息已确认",
            MessageType.VIDEO_ELIGIBILITY_REQUIRED: lambda: extra_data.get("message") or "视频创作需先完成一次充值或订阅",
            MessageType.WORKFLOW_STATE: lambda: "工作流路径",
        }
        message_func = default_messages.get(event_type)
        return message_func() if message_func else f"事件: {event_type.value}"
