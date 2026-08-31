"""
基础Agent类 - 提供统一的消息管理和事件发送功能
所有Agent服务都应该继承这个基类
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
# from ...models.database import engine  # 已移除，不再需要
from ...exceptions import BusinessException, BusinessExceptionCode
from ...utils.asyncpg_utils import utc_isoformat

logger = logging.getLogger(__name__)


class MessageRole(str, Enum):
    """消息角色枚举"""
    HUMAN = "human"
    AI = "ai"
    SYSTEM = "system"


class MessageType(str, Enum):
    """
    消息类型枚举
    
    所有Agent服务都应该使用这个枚举来发送事件，而不是直接使用字符串。
    这样可以确保事件类型的一致性和可维护性。
    """
    
    # ========== Agent Router相关事件 ==========
    # 使用位置: agent_router_service.py
    SESSION_CREATED = "session_created"  # 会话创建完成 - agent_router_service.py
    AGENT_TYPE_DETERMINED = "agent_type_determined"  # Agent类型已确定 - agent_router_service.py
    USER_INPUT = "user_input"  # 用户输入 - agent_router_service.py, base_agent.py
    VIDEO_ELIGIBILITY_REQUIRED = "video_eligibility_required"  # 需先充值/订阅才能用视频 - 前端弹 toast 并跳定价页

    # ========== 视频代理相关事件 ==========
    # 使用位置: 各个video服务文件
    
    # 视频分析
    VIDEO_ANALYSIS = "video_analysis"  # 视频分析完成 - video_analysis_service.py, user_input_analysis_service.py
    
    # 故事生成
    STORY_OUTLINE_GENERATED = "story_outline_generated"  # 故事大纲生成完成 - outline_generation_service.py
    SCENES_GENERATED = "scenes_generated"  # 场景生成完成 - scene_generation_service.py
    STORYBOARD_DETAIL_GENERATED = "storyboard_detail_generated"  # 详细分镜生成完成 - storyboard_detail_generation_service.py
    
    # 角色设计
    CHARACTERS_DESIGNED = "characters_designed"  # 角色设计完成 - main_character_design_service.py
    
    # 音乐生成
    MUSIC_GENERATED = "music_generated"  # 音乐生成完成（视频代理） - music_generation_service.py
    MUSIC_GENERATION_PROGRESS = "music_generation_progress"  # 音乐生成进度（hidden事件） - music_generation_service.py
    
    # 关键帧生成
    KEYFRAMES_GENERATED = "keyframes_generated"  # 关键帧生成完成 - keyframe_generation_service.py
    KEYFRAME_GENERATION_PROGRESS = "keyframe_generation_progress"  # 关键帧生成进度（hidden事件） - keyframe_generation_service.py
    IMAGE_MODEL_SWITCHED = "image_model_switched"  # 图像模型降级/切换（Wrapper 自动重试备用模型后通知用户）
    GENERATION_FAILED = "generation_failed"  # 某生成环节失败并暂停（含 stage / failure_category / 用户友好原因，前端展示并停掉自动继续）
    # 关键帧反思（与 keyframe 一致：仅进度 + 完成两个事件）
    KEYFRAME_REFLECTION_PROGRESS = "keyframe_reflection_progress"  # 关键帧反思进度（hidden） - keyframe_reflection_service.py
    KEYFRAMES_REFLECTION_COMPLETED = "keyframes_reflection_completed"  # 关键帧反思完成 - keyframe_reflection_service.py
    
    # 旁白生成
    NARRATION_GENERATION_START = "narration_generation_start"  # 旁白生成开始（hidden） - narration_generation_service.py
    NARRATION_GENERATION_PROGRESS = "narration_generation_progress"  # 旁白生成进度（hidden） - narration_generation_service.py
    NARRATIONS_GENERATED = "narrations_generated"  # 旁白生成完成 - narration_generation_service.py
    
    # 音效生成
    AUDIO_EFFECTS_GENERATED = "audio_effects_generated"  # 音效生成完成 - audio_effect_generation_service.py
    
    # 视频生成
    VIDEO_SEGMENTS_GENERATED = "video_segments_generated"  # 单个视频片段生成完成（shots） - video_generation_service.py
    VIDEO_GENERATION_PROGRESS = "video_generation_progress"  # 视频生成进度（hidden事件） - video_generation_service.py
    VIDEO_COMPLETED = "video_completed"  # 视频完成 - video_assembly_service.py
    
    # 视频片段处理
    VIDEO_SEGMENTS_ASSEMBLED = "video_segments_assembled"  # 视频片段合并完成（segments） - video_segments_service.py
    VIDEO_SEGMENTS_PROGRESS = "video_segments_progress"  # 视频片段处理进度（hidden事件） - video_segments_service.py
    
    # 唇形同步
    VIDEO_LIPSYNC_PROGRESS = "video_lipsync_progress"  # 唇形同步处理进度（hidden事件） - video_lipsync_service.py
    VIDEO_LIPSYNC_COMPLETED = "video_lipsync_completed"  # 唇形同步完成 - video_lipsync_service.py
    
    # ========== 其他代理相关事件 ==========
    # 使用位置: 各个代理服务文件
    
    STORY_AGENT_GENERATED = "story_agent_generated"  # 故事代理生成完成 - story_generation_service.py
    MUSIC_AGENT_GENERATED = "music_agent_generated"  # 音乐代理生成完成 - music/music_generation_service.py
    IMAGE_AGENT_GENERATED = "image_agent_generated"  # 图像代理生成完成 - image/image_generation_service.py
    VIDEO_AGENT_GENERATED = "video_agent_generated"  # 视频直生代理生成完成（结构化多视频）- video_gen/video_generation_agent_service.py
    VIDEO_GEN_PROGRESS = "video_gen_progress"  # 视频直生代理逐个视频进度 - video_gen/video_generation_agent_service.py
    
    # ========== 澄清代理相关事件 ==========
    # 使用位置: agent_router_service.py
    CLARIFY_RESPONSE = "clarify_response"  # 澄清代理响应 - agent_router_service.py
    
    # ========== 工作流事件 ==========
    # 使用位置: agent_router_service.py, base_agent.py
    INTERRUPT = "interrupt"  # 中断事件 - agent_router_service.py
    ERROR = "error"  # 错误事件 - agent_router_service.py, base_agent.py
    WORKFLOW_STATE = "workflow_state"  # 底部进度条 path（顺序与节点 id），video_agent_service user_input_analysis 后发送

    # ========== 流式事件 ==========
    # 使用位置: agent_router_service.py, image/music/story服务
    STREAMING_CHUNK = "streaming_chunk"  # 通用流式chunk事件 - agent_router_service.py, image_generation_service.py
    CANCELLED = "cancelled"  # 取消事件 - agent_router_service.py
    STREAM_END = "stream_end"  # 流结束事件 - agent_router_service.py


class BaseAgent:
    """
    基础Agent类 - 提供统一的消息管理功能
    
    所有Agent服务都应该继承这个类，以获得统一的：
    - 事件发送功能
    - 数据库保存功能
    - 消息管理功能
    """
    
    def __init__(self):
        """
        初始化基础Agent
        """
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
        """
        异步事件发送方法 - 同时发送到stream和数据库
        
        Args:
            event_type: 事件类型
            conversation_id: 对话ID
            conversation_uuid: 对话UUID（可选，如果未提供会从 conversation_id 查询）
            message: 自定义消息
            role: 消息角色
            extra_data: 额外数据（可能包含 run_id）
            save_to_db: 是否保存到数据库
            send_to_stream: 是否发送到stream
            hidden: 是否隐藏
            run_id: 运行ID（可选，也可以从 extra_data 中获取）
            
        Returns:
            Dict: 事件数据
        """
        try:
            # 生成默认消息
            if message is None:
                message = self._get_default_message(event_type, extra_data)
            
            # 从 extra_data 中获取 run_id（如果未直接提供）
            if not run_id and extra_data and "run_id" in extra_data:
                run_id = extra_data.get("run_id")
            
            # 保存到数据库（过滤STREAMING_CHUNK和PROGRESS事件，不保存到DB）
            message_id = None
            if save_to_db and conversation_id:
                # STREAMING_CHUNK和PROGRESS事件不保存到DB（太多，占用空间）
                from ...services.redis.stream_service import PROGRESS_EVENTS
                should_save_to_db = event_type != MessageType.STREAMING_CHUNK and event_type.value not in PROGRESS_EVENTS
                
                if should_save_to_db:
                    try:
                        from ...crud.conversation import async_add_message_to_conversation
                        message_obj = await async_add_message_to_conversation(
                            conversation_id=conversation_id,  # 保持原类型（int）
                            conversation_uuid=conversation_uuid,  # 传递 conversation_uuid
                            role=role.value,
                            content=message,
                            event_type=event_type.value,
                            event_data=extra_data or {},
                            run_id=run_id  # 传递 run_id
                        )
                        message_id = message_obj['id']  # message_obj 是 dict，使用字典访问
                        logger.debug(f"事件已保存到数据库: {event_type.value}, message_id: {message_id}, run_id: {run_id}")
                    except Exception as e:
                        logger.error(f"保存事件到数据库失败: {e}")
                else:
                    logger.debug(f"事件跳过数据库保存（仅保存到Redis Stream）: {event_type.value}, run_id: {run_id}")
            
            # 构建事件数据
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
            
            # 发送到Redis Stream（如果提供了run_id）
            if send_to_stream and run_id:
                try:
                    from ...services.redis.connection import get_redis_stream_service
                    from ...services.redis.stream_service import GENERATED_EVENTS
                    from ...models.task_status import TaskStatus
                    redis_service = await get_redis_stream_service()
                    stream_id = await redis_service.add_message(run_id, event_data)
                    
                    # 如果是generated事件，更新last_generated_event和last_generated_id（用于消息恢复）
                    # 注意：任务状态（RUNNING）由process_task统一管理，这里需要先读取当前状态再更新
                    if event_type.value in GENERATED_EVENTS:
                        # 读取当前状态，避免覆盖status
                        current_status = await redis_service.get_task_status(run_id)
                        current_status_value = current_status.get('status', TaskStatus.RUNNING.value) if current_status else TaskStatus.RUNNING.value
                        await redis_service.update_task_status(
                            run_id,
                            status=current_status_value,  # 保持当前状态
                            last_generated_event=event_type.value,
                            last_generated_id=stream_id,
                            conversation_uuid=conversation_uuid
                        )
                    
                    logger.debug(f"事件已发送到Redis Stream: {event_type.value}, stream_id: {stream_id}")
                except Exception as e:
                    logger.error(f"发送事件到Redis Stream失败: {e}")
            
            # 同时发送到HTTP stream（用于实时推送）
            # ⭐ writer() 是同步调用，如果 stream buffer 满或客户端慢会阻塞事件循环
            if send_to_stream:
                try:
                    from langgraph.config import get_stream_writer
                    writer = get_stream_writer()
                    await asyncio.to_thread(writer, event_data)
                    logger.debug(f"事件已发送到HTTP stream: {event_type.value}, message_id: {message_id}")
                except Exception as e:
                    logger.debug(f"发送事件到HTTP stream失败（可能是上下文限制）: {e}")
            
            return event_data
            
        except Exception as e:
            logger.error(f"异步发送事件失败: {e}")
            return None
    
    async def async_update_event(
        self,
        message_id: int,
        extra_data: Optional[Dict[str, Any]] = None,
        update_db: bool = True
    ):
        """
        异步更新事件方法
        
        Args:
            message_id: 消息ID
            extra_data: 额外数据
            update_db: 是否更新数据库
        """
        if update_db:
            try:
                from ...crud.conversation import async_update_message_event_data_partial
                success = await async_update_message_event_data_partial(
                    message_id=message_id,
                    partial_data=extra_data
                )
                if success:
                    logger.debug(f"部分事件数据已更新到数据库: message_id={message_id}")
                else:
                    logger.warning(f"更新事件数据失败（消息不存在）: message_id={message_id}")
            except Exception as e:
                logger.error(f"异步更新部分事件数据失败: {e}")
    
    
    
    def _get_default_message(self, event_type: MessageType, extra_data: Optional[Dict[str, Any]] = None) -> str:
        """根据事件类型生成默认消息"""
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
            MessageType.GENERATION_FAILED: lambda: extra_data.get("message") or extra_data.get("message_key") or "当前环节生成失败，已暂停。",
            MessageType.VIDEO_SEGMENTS_GENERATED: lambda: "视频片段生成完成",
            MessageType.SESSION_CREATED: lambda: "会话已创建",
            MessageType.AGENT_TYPE_DETERMINED: lambda: "Agent 类型已确定",
            MessageType.VIDEO_ELIGIBILITY_REQUIRED: lambda: extra_data.get("message") or "视频创作需先完成一次充值或订阅",
            MessageType.WORKFLOW_STATE: lambda: "工作流路径",
        }
        
        message_func = default_messages.get(event_type)
        if message_func:
            return message_func()
        else:
            return f"事件: {event_type.value}"


