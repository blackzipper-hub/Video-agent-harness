"""
LangGraph Agent路由器服务 - 使用AsyncPostgresSaver和流式输出
"""
import logging
import json
import asyncio
import math
import re
import uuid
from typing import Dict, Any, List, AsyncGenerator, Optional, Literal, Union, Tuple
from datetime import datetime
from enum import Enum
from typing_extensions import Annotated, TypedDict
from dataclasses import dataclass

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage
from app.chat.prompts.prompt_config import PromptName, PROMPTS_CONFIG
from langgraph.config import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver, AsyncConnectionPool
from pydantic import BaseModel, Field
from sqlmodel import Session
from langgraph.graph.state import Command, Send
from langgraph.types import interrupt

from ...exceptions import BusinessException, BusinessExceptionCode
from ...models.database import get_db
from ...config import settings
from ...utils.thread_id_utils import generate_new_thread_id
from ...utils.asyncpg_utils import utc_isoformat
from .base_agent import BaseAgent, MessageType, MessageRole
from ...models.task_status import TaskStatus
from .workflow_client import WorkflowClient, create_default_workflow_client
from ...models.user_options import UserOption, VideoGenerationTool
from ...models.video_state import VideoAgentState, UserInput, ImageUserInput, AudioFileUserInput, VideoFileUserInput
from ...models.chat_orchestration import delegated_va_run_id_reducer
from ...models.conversation import ConversationDB, ConversationRunDB
from ...schemas.response import ClarifyResponse
from ...crud.conversation import (
    async_set_conversation_delegated_va_run_id,
    async_get_conversation_by_id,
    async_get_conversation_messages,
    async_get_conversation_runs,
    async_update_conversation_run_status,
    async_set_conversation_run_billing_pending_if_not_completed,
)
from .prompt_shield import generate_shield_refusal_reply, validate_output_line
from .utils.llm_resilience import StructuredResilienceKind, ainvoke_structured_resilient
from .action_suggestions_merge import (
    ActionSuggestionsStreamSplitter,
    split_chat_reply_and_suggestions_tail,
    parse_merged_action_suggestions,
)

logger = logging.getLogger(__name__)

# 推荐动作模型：附带的最近对话原文（User/Assistant 行）轮数上限与总字符上限
RECENT_DIALOG_TURNS_FOR_ACTION_SUGGESTIONS = 5
RECENT_DIALOG_MAX_CHARS_FOR_ACTION_SUGGESTIONS = 8000


def _message_plain_text_for_action_suggestions(msg: BaseMessage) -> str:
    """从 LangGraph 消息中提取可拼进推荐动作上下文的纯文本。"""
    raw = getattr(msg, "content", None)
    if isinstance(raw, str):
        return " ".join(raw.split()).strip()
    if isinstance(raw, list):
        parts: List[str] = []
        for p in raw:
            if isinstance(p, dict):
                t = p.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(" ".join(t.split()).strip())
            elif isinstance(p, str) and p.strip():
                parts.append(" ".join(p.split()).strip())
        return "\n".join(parts).strip()
    if raw is None:
        return ""
    return " ".join(str(raw).split()).strip()


def format_recent_dialog_excerpt_for_action_suggestions(
    history: Optional[List[BaseMessage]],
    *,
    latest_assistant_text: str,
    max_turns: int = RECENT_DIALOG_TURNS_FOR_ACTION_SUGGESTIONS,
    max_chars: int = RECENT_DIALOG_MAX_CHARS_FOR_ACTION_SUGGESTIONS,
) -> str:
    """按时间顺序拼接最近若干轮 Human/AI 原文，供推荐动作模型显式阅读。"""
    rows: List[Tuple[str, str]] = []
    for m in history or []:
        if isinstance(m, HumanMessage):
            label = "User"
        elif isinstance(m, AIMessage):
            label = "Assistant"
        else:
            continue
        txt = _message_plain_text_for_action_suggestions(m)
        if not txt:
            continue
        rows.append((label, txt))
    tail_txt = (latest_assistant_text or "").strip()
    if tail_txt:
        if rows and rows[-1][0] == "Assistant" and rows[-1][1].strip() == tail_txt:
            pass
        else:
            rows.append(("Assistant", tail_txt))
    max_rows = max(2, max_turns * 2)
    window = rows[-max_rows:]
    lines = [f"{role}: {text}" for role, text in window]
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[-max_chars:]
    return out


async def _persist_conversation_run_terminal_status(
    run_id: Optional[str],
    status: str,
    error_message: Optional[str] = None,
) -> None:
    """Persist terminal status to DB and Redis so conversation_runs / task cache do not stay stuck on running."""
    if not run_id:
        return
    try:
        await async_update_conversation_run_status(
            run_id, status, error_message=error_message,
            completed_at=datetime.utcnow() if status in ("completed", "failed", "cancelled", "interrupted") else None,
        )
    except Exception as e:
        logger.warning("async_update_conversation_run_status(%s): %s", status, e)
    try:
        await async_set_conversation_run_billing_pending_if_not_completed(run_id)
    except Exception as e:
        logger.warning("async_set_conversation_run_billing_pending(%s): %s", run_id, e)
    try:
        from ...services.redis.connection import get_redis_stream_service
        redis_service = await get_redis_stream_service()
        kwargs = {}
        if error_message:
            kwargs["error_message"] = error_message
        await redis_service.update_task_status(run_id, status, **kwargs)
    except Exception as e:
        logger.warning("redis update_task_status(%s): %s", status, e)


def merge_user_input_resources(
    new_items: List[Union[ImageUserInput, AudioFileUserInput, VideoFileUserInput]],
    old_items: List[Union[ImageUserInput, AudioFileUserInput, VideoFileUserInput]]
) -> List[Union[ImageUserInput, AudioFileUserInput, VideoFileUserInput]]:
    """
    合并新旧资源列表（images/audio_files/video_files）
    
    规则：
    1. 新资源放在列表前面（优先级更高），标记 is_new=True
    2. 旧资源放在列表后面，标记 is_new=False
    3. 基于 url 去重（保留第一次出现的）
    
    Args:
        new_items: 新的资源列表（来自当前请求，会放在前面）
        old_items: 旧的资源列表（来自历史，会放在后面）
    
    Returns:
        合并后的资源列表：[新资源...] + [旧资源（去重后）...]
    """
    seen_urls = set()
    merged = []
    
    # 第一步：添加新资源到列表前面，标记为 is_new=True
    for item in new_items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            # 确保新资源标记为 is_new=True
            item.is_new = True
            merged.append(item)
    
    # 第二步：添加旧资源到列表后面（跳过重复的），标记为 is_new=False
    for item in old_items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            # 旧资源标记为 is_new=False
            item.is_new = False
            merged.append(item)
    
    return merged


def merge_user_input_data(
    new_user_input_data: UserInput,
    old_user_input_data: Optional[UserInput]
) -> UserInput:
    """
    合并新旧 UserInput 数据
    
    规则：
    - 使用新的 user_input、user_option、agent_type
    - 合并资源列表（新的在前，去重）
    
    Args:
        new_user_input_data: 新的用户输入数据（来自当前请求）
        old_user_input_data: 旧的用户输入数据（来自历史 state）
    
    Returns:
        合并后的 UserInput
    """
    # 如果没有历史数据，直接返回新数据
    if not old_user_input_data:
        return new_user_input_data
    
    # 合并资源列表（新的在前面）
    merged_images = merge_user_input_resources(
        new_user_input_data.images,
        old_user_input_data.images
    )
    merged_audio_files = merge_user_input_resources(
        new_user_input_data.audio_files,
        old_user_input_data.audio_files
    )
    merged_video_files = merge_user_input_resources(
        new_user_input_data.video_files,
        old_user_input_data.video_files
    )
    
    # 创建合并后的 UserInput（使用新的文本和配置；content_category 在 user_option 内）
    merged_user_input = UserInput(
        user_input=new_user_input_data.user_input,
        images=merged_images,
        audio_files=merged_audio_files,
        video_files=merged_video_files,
        user_option=new_user_input_data.user_option,
        agent_type=new_user_input_data.agent_type,
        has_confirmed=new_user_input_data.has_confirmed,
    )
    
    logger.info(
        f"🔄 资源合并完成: "
        f"images={len(merged_images)} (新:{len(new_user_input_data.images)}, 旧:{len(old_user_input_data.images)}), "
        f"audio={len(merged_audio_files)} (新:{len(new_user_input_data.audio_files)}, 旧:{len(old_user_input_data.audio_files)}), "
        f"video={len(merged_video_files)} (新:{len(new_user_input_data.video_files)}, 旧:{len(old_user_input_data.video_files)})"
    )
    
    return merged_user_input


async def merge_user_option_with_input(
    user_input: str,
    default_user_option: UserOption
) -> UserOption:
    """使用 LLM 根据用户输入智能覆盖用户选项"""
    from app.chat.prompts.prompt_loader import load_prompt_with_fallback_async
    from app.chat.prompts.prompt_config import PromptName, PROMPTS_CONFIG
    from app.chat.services.agent.utils.llm_resilience import (
        StructuredResilienceKind,
        ainvoke_structured_resilient,
    )

    prompt_template, _llm = await load_prompt_with_fallback_async(
        hub_name=PromptName.AGENT_ROUTER_USER_OPTION_MERGE.value,
        local_template_name="agent_router/agent_router_user_option_merge",
        schema=None,
        include_raw=False
    )

    messages = prompt_template.format_messages(
        user_input=user_input,
        default_user_option=default_user_option.model_dump_json()
    )

    result = await ainvoke_structured_resilient(
        prompt_entry=PROMPTS_CONFIG[PromptName.AGENT_ROUTER_USER_OPTION_MERGE],
        kind=StructuredResilienceKind.CREATE_AGENT,
        agent_inputs={"messages": messages},
    )
    merged_user_option = result["structured_response"]
    merged_user_option = merged_user_option.model_copy(
        update={"full_auto": default_user_option.full_auto}
    )

    logger.info(f"✅ 用户选项合并完成: {merged_user_option.model_dump()}")

    return merged_user_option


def _stream_chunk_to_text(raw_chunk: Any) -> str:
    """将不同 LLM SDK 的 chunk.content 统一抽取为纯文本。"""
    if raw_chunk is None:
        return ""
    if isinstance(raw_chunk, str):
        return raw_chunk
    if isinstance(raw_chunk, list):
        parts: List[str] = []
        for item in raw_chunk:
            text = _stream_chunk_to_text(item)
            if text:
                parts.append(text)
        return "".join(parts)
    if isinstance(raw_chunk, dict):
        if raw_chunk.get("type") in ("reasoning", "thinking"):
            return ""
        text = raw_chunk.get("text") or raw_chunk.get("content")
        if isinstance(text, str):
            return text
        if isinstance(text, list):
            return _stream_chunk_to_text(text)
        return ""
    text_attr = getattr(raw_chunk, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    if isinstance(text_attr, list):
        return _stream_chunk_to_text(text_attr)
    return ""


@dataclass
class AgentRouterContextSchema:
    """Agent Router Runtime Context Schema"""
    async_db: AsyncSession  # 异步数据库连接


class AgentType(str, Enum):
    """代理类型枚举"""
    VIDEO = "video"          # 视频代理（旧 Workflow 多节点流水线）
    VIDEO_GEN = "video_gen"  # 视频直生代理（SD2 单模型直接出片，对齐 image agent）
    STORY = "story"          # 故事代理
    MUSIC = "music"          # 音乐代理
    IMAGE = "image"          # 图像代理
    CHAT = "chat"            # 聊天代理，用于自由聊天和灵感启发
    CLARIFY = "clarify"      # 澄清代理，用于询问用户意图
    UNKNOWN = "unknown"       # 未知类型，需要用户选择（保留备用）


class RouterAnalysisAgentType(str, Enum):
    """路由分析 / 多轮确认阶段 LLM 可输出的代理类型（不含 video_gen，execution 由程序判定）。"""
    VIDEO = "video"
    STORY = "story"
    MUSIC = "music"
    IMAGE = "image"
    CHAT = "chat"
    CLARIFY = "clarify"


class InterruptType(str, Enum):
    """中断类型枚举"""
    AGENT_SELECTION = "agent_selection"  # 代理选择


class RouterAnalysisResult(BaseModel):
    """路由分析结果"""
    agent_type: RouterAnalysisAgentType = Field(description="选择的代理类型")
    confidence: float = Field(description="置信度 0.0-1.0")
    reason: str = Field(description="选择原因")
    key_indicators: List[str] = Field(description="关键指标", default_factory=list)
    detected_language: str = Field(default="en", description="检测到的用户输入语言的ISO 639-1语言代码（如en, zh, fr, es, ja, de等）")


class ConfirmationCheckResult(BaseModel):
    """需求确认检查结果。"""
    agent_type: RouterAnalysisAgentType = Field(description="已确认的目标代理类型")
    has_confirmed: bool = Field(description="是否已经达到可开始生成的确认标准")
    confirmed_summary: str = Field(default="", description="给前端展示的确认摘要")
    summary_input: str = Field(default="", description="前端确认后回传给生成代理的整理后输入")
    missing_requirements: List[str] = Field(default_factory=list, description="仍需补充的关键信息")
    reason: str = Field(description="判断原因")


class ActionSuggestionItem(BaseModel):
    """单条推荐动作：label 为 chip 展示，message 为点击后发送内容。"""
    label: str = Field(description="chip 显示文案（15字以内）")
    message: str = Field(description="点击后发送给助手的完整内容")


class ActionSuggestionsResult(BaseModel):
    """推荐动作结构化输出；「直接生成」由服务端固定追加。"""
    reply_type: Literal["choice", "open"] = Field(description="助手回复类型：选项型或开放型")
    suggestions: List[ActionSuggestionItem] = Field(
        ...,
        min_length=2,
        max_length=5,
        description="推荐动作列表（不含直接生成）；choice 2–5 条，open 固定 3 条",
    )



class AgentRouterRequest(BaseModel):
    """Agent路由器请求模型"""
    user_id: str = Field(description="用户ID")
    user_input_data: UserInput = Field(description="用户输入数据")
    conversation_id: Optional[int] = Field(description="会话ID", default=None)
    thread_id: Optional[str] = Field(description="线程ID", default=None)



apool = AsyncConnectionPool(
    conninfo=settings.DATABASE_URL,
    max_size=10,
    max_idle=300,  # 对标 asyncpg max_inactive_connection_lifetime
    check=AsyncConnectionPool.check_connection,  # 对标 SQLAlchemy pool_pre_ping
    open=False,
    kwargs={'autocommit': True, 'prepare_threshold': 0},
)

async def setup_async_checkpointer():
    await apool.open()
    apostgres_checkpointer = AsyncPostgresSaver(apool)
    await apostgres_checkpointer.setup()
    return apostgres_checkpointer

_apostgres_checkpointer = None

async def apostgres_checkpointer():
    global _apostgres_checkpointer
    if not _apostgres_checkpointer:
        _apostgres_checkpointer = await setup_async_checkpointer()
    return _apostgres_checkpointer


def _run_id_reducer(current: Optional[str], update: Optional[str]) -> Optional[str]:
    """Resolve concurrent run_id updates in one step (e.g. resume + Command(update={\"run_id\": ...})). Prefer non-None update."""
    return update if update is not None else current


# 定义Agent路由器的状态类型
class AgentRouterState(BaseModel):
    """Agent路由器状态定义"""
    # 原始请求
    request: Optional[AgentRouterRequest] = Field(default=None, description="原始请求对象")
    
    # 处理后的字段
    user_id: str = Field(default="", description="用户ID")
    user_input_data: Optional[UserInput] = Field(default=None, description="用户输入数据")
    conversation_id: Optional[int] = Field(default=None, description="会话ID")
    conversation_uuid: Optional[str] = Field(default=None, description="会话UUID")
    thread_id: Optional[str] = Field(default=None, description="线程ID")
    run_id: Annotated[Optional[str], _run_id_reducer] = Field(default=None, description="运行ID")
    
    # 语言识别
    detected_language: Optional[str] = Field(default=None, description="检测到的用户输入语言（ISO 639-1代码）")
    
    # 分析结果
    router_analysis: Optional[RouterAnalysisResult] = Field(default=None, description="路由分析结果")
    selected_agent: Optional[AgentType] = Field(default=None, description="选中的代理类型")
    has_confirmed: bool = Field(default=False, description="用户是否已经确认开始生成")
    ready_for_confirmation: bool = Field(default=False, description="需求是否已经整理完成，可等待前端点击确认生成")
    confirmed_summary: Optional[str] = Field(default=None, description="给前端展示的确认摘要")
    confirmed_input_summary: Optional[str] = Field(default=None, description="前端确认生成时应回传的整理后输入")
    prefer_panel_user_option: bool = Field(default=False, description="为 true 时跳过 LLM 合并，以配置卡片 user_option 为准（仅保留音频时长规则）")
    multimodal_context_summary: Optional[str] = Field(default=None, description="用户上传附件的分析摘要，供 chat agent 使用")

    video_agent_state: Optional[VideoAgentState] = Field(default=None, description="视频代理状态")

    # 全自动模式（仅 admin 测试用，不暴露前端）：True 时视频门控不 interrupt
    full_auto: Optional[bool] = Field(default=None, description="全自动执行到完成，不暂停等待用户")

    # 测试用语言覆盖（如 zh），由 task_data.language 传入，有则覆盖路由分析得到的 detected_language
    language: Optional[str] = Field(default=None, description="测试用语言（ISO 639-1），有则覆盖 detected_language")
    force_clarify: Optional[bool] = Field(default=None, description="测试开关：正常完成路由分析后，强制将最终路由覆盖为 clarify")
    force_chat: Optional[bool] = Field(default=None, description="测试开关：正常完成路由分析后，强制将最终路由覆盖为 chat")
    
    # LangGraph标准消息历史
    messages: Annotated[List[BaseMessage], add_messages] = Field(default_factory=list, description="消息历史")

    # 对 Cuti-VideoAgent delegate-submit 成功后得到的 pipeline run_id；有则允许挂载 Edit（整颗 Companion）单 tool
    delegated_va_run_id: Annotated[Optional[str], delegated_va_run_id_reducer] = Field(
        default=None,
        description="VA 侧任务 run_id，delegate 成功后写入",
    )

    # Prompt Shield Input Rail 命中标志，由 input_rail 节点设置后，条件边直接跳到 END
    shield_blocked: bool = Field(default=False, description="Input Rail 是否拦截了本次请求")


class AgentRouterService(BaseAgent):
    """Agent路由器服务 - 使用LangGraph和AsyncPostgresSaver"""
    
    def __init__(self, workflow_client: Optional[WorkflowClient] = None):
        """初始化Agent路由器服务。

        video / story / music / image 子 workflow 在 Cuti-VideoAgent 中实现；此处通过
        ``WorkflowClient`` 远程调用。默认会创建 HTTP ``WorkflowClient``，按
        ``docs/VIDEOCHATAGENT_API.md`` 调用 VideoAgent 的 ``delegate-submit``；
        也可在集成层显式传入自定义实现。
        """
        BaseAgent.__init__(self)
        self._workflow_client: WorkflowClient = workflow_client or create_default_workflow_client()
        # 路由分析使用 load_prompt(AGENT_ROUTER_ANALYSIS) 取 LLM，不再维护 default LLM

    @staticmethod
    def _creative_agent_types() -> tuple[AgentType, ...]:
        return (
            AgentType.VIDEO,
            AgentType.VIDEO_GEN,
            AgentType.STORY,
            AgentType.MUSIC,
            AgentType.IMAGE,
        )

    # video_gen 单模型直生支持的 SD2 全家桶（与 Cuti-VideoAgent 对齐）
    _SD2_VIDEO_GEN_TOOLS = frozenset({
        VideoGenerationTool.SEEDANCE_2_I2V,
        VideoGenerationTool.SEEDANCE_2_I2V_TURBO,
        VideoGenerationTool.SEEDANCE_2_FAST_I2V,
        VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO,
    })
    _SD2_MAX_DURATION = 15  # SD2 单段最大时长

    @classmethod
    def _fits_single_model(cls, user_option: Optional["UserOption"]) -> bool:
        """符合单模型直生规格 ⇔ 模型 ∈ SD2 全家桶 且 duration ≤ 单段上限（与 VideoAgent 守门一致）。"""
        if user_option is None:
            return False
        tool = getattr(user_option, "video_generation_tool", None)
        if tool not in cls._SD2_VIDEO_GEN_TOOLS:
            return False
        duration = getattr(user_option, "duration", None)
        if duration is None:
            return True
        try:
            return int(duration) <= cls._SD2_MAX_DURATION
        except (TypeError, ValueError):
            return True

    @classmethod
    def _is_creative_agent(cls, agent_type: Optional[AgentType]) -> bool:
        return agent_type in cls._creative_agent_types()

    @classmethod
    def _normalize_creative_agent_type(cls, agent_type: Optional[str]) -> Optional[AgentType]:
        if not agent_type:
            return None
        try:
            normalized = AgentType(agent_type.strip().lower())
        except ValueError:
            return None
        return normalized if cls._is_creative_agent(normalized) else None

    @classmethod
    def _execution_agent_to_router_analysis_type(cls, agent_type: AgentType) -> RouterAnalysisAgentType:
        if agent_type == AgentType.VIDEO_GEN:
            return RouterAnalysisAgentType.VIDEO
        return RouterAnalysisAgentType(agent_type.value)

    @classmethod
    def _router_analysis_agent_to_agent_type(cls, router_type: RouterAnalysisAgentType) -> AgentType:
        return AgentType(router_type.value)

    @classmethod
    def _dialog_selected_agent(cls, selected_agent: AgentType) -> AgentType:
        """多轮对话阶段对外只暴露 video，video_gen 由确认后程序判定。"""
        if selected_agent == AgentType.VIDEO_GEN:
            return AgentType.VIDEO
        return selected_agent

    @staticmethod
    def _build_router_multimodal_template_data(user_input_data: UserInput) -> Dict[str, Any]:
        """与 ``docs/multimudal_example.md`` 一致：使用 ``UserInput`` 里 S3/CDN 的 ``url``（上传链路已保证为 CDN https）。"""
        router_images = [
            {"url": img.url}
            for img in (user_input_data.images or [])
            if (getattr(img, "url", None) or "").strip()
        ]

        router_audio = None
        router_audio_duration_seconds = None
        for audio in user_input_data.audio_files or []:
            if (getattr(audio, "url", None) or "").strip():
                router_audio = audio.url
                router_audio_duration_seconds = getattr(audio, "duration", None)
                break

        router_video = None
        for video in user_input_data.video_files or []:
            if (getattr(video, "url", None) or "").strip():
                router_video = video.url
                break

        return {
            "router_has_images": len(router_images) > 0,
            "router_images": router_images,
            "router_has_audio": router_audio is not None,
            "router_audio": router_audio,
            "router_audio_duration_seconds": router_audio_duration_seconds,
            "router_has_video": router_video is not None,
            "router_video": router_video,
        }

    @staticmethod
    def _is_direct_generate_request(user_text: str) -> bool:
        text = (user_text or "").strip().lower()
        return text in {
            "直接生成",
            "直接开始生成",
            "开始生成",
            "generate directly",
            "start generating",
            "generate now",
        }

    @classmethod
    def _is_meaningful_generation_summary(cls, value: Optional[str]) -> bool:
        text = (value or "").strip()
        if not text or cls._is_direct_generate_request(text):
            return False
        if cls._is_heuristic_concat_generation_summary(text):
            return False
        if "我会根据以下总结开始生成" in text or "I will start generation based on this summary" in text:
            return False
        return True

    _USER_OPTION_DURATION_PATTERNS = (
        re.compile(r"\d{1,3}\s*(?:秒|分钟|分)", re.I),
        re.compile(r"\d{1,3}\s*(?:s|sec(?:ond)?s?|min(?:ute)?s?)\b", re.I),
        re.compile(r"\d{1,3}\s*[-~～—]\s*\d{1,3}\s*(?:秒|分钟|分)", re.I),
        re.compile(r"\d{1,3}\s*[-~～—]\s*\d{1,3}\s*(?:s|sec(?:ond)?s?|min(?:ute)?s?)\b", re.I),
    )
    _USER_OPTION_ASPECT_PATTERNS = (
        re.compile(r"\b(?:16:9|9:16|1:1)\b"),
        re.compile(r"(?:竖屏|横屏|landscape|portrait)", re.I),
    )
    _USER_OPTION_RESOLUTION_PATTERNS = (
        re.compile(r"\b(?:480p|720p|1080p|4k)\b", re.I),
    )
    _USER_OPTION_EXPLICIT_PATTERNS = (
        *_USER_OPTION_DURATION_PATTERNS,
        *_USER_OPTION_ASPECT_PATTERNS,
        *_USER_OPTION_RESOLUTION_PATTERNS,
        re.compile(r"(?:lip-?sync|唇形|口型)", re.I),
        re.compile(r"(?:continuity|连续性)\s*mode", re.I),
    )

    @classmethod
    def _text_explicitly_mentions_user_options(cls, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        return any(p.search(t) for p in cls._USER_OPTION_EXPLICIT_PATTERNS)

    @classmethod
    def _text_explicitly_mentions_duration(cls, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        return any(p.search(t) for p in cls._USER_OPTION_DURATION_PATTERNS)

    @classmethod
    def _conversation_explicitly_mentioned_options(cls, messages: Optional[List[BaseMessage]]) -> bool:
        return bool(cls._conversation_explicit_user_option_text(messages))

    @classmethod
    def _conversation_explicit_user_option_text(cls, messages: Optional[List[BaseMessage]]) -> str:
        lines: List[str] = []
        for msg in messages or []:
            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
                if cls._text_explicitly_mentions_user_options(content):
                    lines.append(content)
        return "\n".join(lines[-12:])

    @classmethod
    async def _conversation_explicit_user_option_text_from_rows(
        cls,
        conversation_id: Optional[int],
        state_messages: Optional[List[BaseMessage]],
    ) -> str:
        lines = cls._conversation_explicit_user_option_text(state_messages)
        if lines or not conversation_id:
            return lines
        try:
            rows = await async_get_conversation_messages(conversation_id, limit=200)
        except Exception as exc:
            logger.warning("读取历史配置原话失败: %s", exc)
            return ""
        matched: List[str] = []
        for row in rows[-80:]:
            if row.get("event_type") != "user_input":
                continue
            content = (row.get("content") or "").strip()
            if content and not cls._is_direct_generate_request(content) and cls._text_explicitly_mentions_user_options(content):
                matched.append(content)
        return "\n".join(matched[-12:])

    @classmethod
    def _strip_unmentioned_direct_generate_options(cls, brief: str, user_texts: List[str]) -> str:
        source = "\n".join(t for t in (user_texts or []) if t)
        cleaned = (brief or "").strip()
        if not cleaned:
            return cleaned

        if not any(p.search(source) for p in cls._USER_OPTION_DURATION_PATTERNS):
            cleaned = re.sub(
                r"(?:[，,。.]?\s*)时长(?:约|大约|为|控制在|建议为)?\s*[\d一二三四五六七八九十半]+(?:\s*[-~～—到至]\s*[\d一二三四五六七八九十半]+)?\s*(?:秒|分钟|分)(?:左右|以内|上下)?",
                "",
                cleaned,
            )
            cleaned = re.sub(
                r"(?:[，,。.]?\s*)(?:duration|length)\s*(?:of|around|about|:|is)?\s*[\d.]+(?:\s*[-~–—]\s*[\d.]+)?\s*(?:seconds?|secs?|s|minutes?|mins?)",
                "",
                cleaned,
                flags=re.I,
            )

        if not any(p.search(source) for p in cls._USER_OPTION_ASPECT_PATTERNS):
            cleaned = re.sub(
                r"(?:[，,。.]?\s*)画幅(?:比例)?(?:约|为|是)?\s*(?:16:9|9:16|1:1|竖屏|横屏)",
                "",
                cleaned,
                flags=re.I,
            )
            cleaned = re.sub(
                r"(?:[，,。.]?\s*)(?:aspect\s*ratio|format)\s*(?:of|:|is)?\s*(?:16:9|9:16|1:1|portrait|landscape)",
                "",
                cleaned,
                flags=re.I,
            )

        if not any(p.search(source) for p in cls._USER_OPTION_RESOLUTION_PATTERNS):
            cleaned = re.sub(
                r"(?:[，,。.]?\s*)分辨率(?:约|为|是)?\s*(?:480p|720p|1080p|4k)",
                "",
                cleaned,
                flags=re.I,
            )
            cleaned = re.sub(
                r"(?:[，,。.]?\s*)resolution\s*(?:of|:|is)?\s*(?:480p|720p|1080p|4k)",
                "",
                cleaned,
                flags=re.I,
            )

        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"^[，,。.\s]+", "", cleaned).strip()
        return cleaned or brief

    @staticmethod
    def _user_option_value(value: Any) -> Any:
        return getattr(value, "value", value)

    @classmethod
    def _format_panel_user_option_for_prompt(cls, user_option: Optional[UserOption]) -> Tuple[str, str]:
        option = user_option or UserOption.default()
        data = option.model_dump()
        normalized = {key: cls._user_option_value(value) for key, value in data.items()}
        summary = "\n".join(
            [
                f"duration: {normalized.get('duration')} seconds",
                f"aspect_ratio: {normalized.get('aspect_ratio')}",
                f"resolution: {normalized.get('resolution')}",
                f"video_generation_tool: {normalized.get('video_generation_tool')}",
                f"image_generation_tool: {normalized.get('image_generation_tool')}",
                f"lipsync_video_tool: {normalized.get('lipsync_video_tool')}",
                f"lipsync_coverage: {normalized.get('lipsync_coverage')}",
                f"enable_continuity_mode: {normalized.get('enable_continuity_mode')}",
                f"enable_keyframe_reflection: {normalized.get('enable_keyframe_reflection')}",
                f"full_auto: {normalized.get('full_auto')}",
            ]
        )
        return summary, json.dumps(normalized, ensure_ascii=False, default=str)

    def _should_merge_user_option_with_input(
        self,
        state: AgentRouterState,
        *,
        has_confirmed: bool,
        direct_generate_requested: bool,
        generation_confirmation_summary: Optional[str],
        user_input_text: str,
    ) -> bool:
        if state.prefer_panel_user_option:
            return False

        if not has_confirmed:
            text = (user_input_text or "").strip()
            return bool(text) and self._text_explicitly_mentions_user_options(text)

        if direct_generate_requested:
            return bool((user_input_text or "").strip())

        if self._is_meaningful_generation_summary(state.confirmed_input_summary):
            return self._text_explicitly_mentions_user_options(state.confirmed_input_summary)
        if self._is_meaningful_generation_summary(generation_confirmation_summary):
            return self._text_explicitly_mentions_user_options(generation_confirmation_summary)
        if self._is_meaningful_generation_summary(user_input_text):
            return self._text_explicitly_mentions_user_options(user_input_text)

        return False

    @staticmethod
    def _is_heuristic_concat_generation_summary(text: str) -> bool:
        """识别旧版拼接兜底摘要，避免直接生成时复用。"""
        t = (text or "").strip()
        if not t:
            return False
        return (
            "用户希望制作一个关于「" in t
            or "未明确的时长、风格和细节可以根据主题合理补齐" in t
            or (
                t.startswith("The user wants to create a ")
                and "Unspecified details can be filled in reasonably" in t
            )
        )

    @staticmethod
    def _heuristic_direct_generate_brief_from_user_lines(
        user_texts: List[str],
        agent_type: Optional[AgentType],
        detected_language: str,
    ) -> str:
        """当 LLM 摘要失败时的兜底：合并用户原话为一段可执行说明（不用旧模板套话）。"""
        parts = [t.strip() for t in (user_texts or []) if t and t.strip()]
        if not parts:
            return ""
        is_en = (detected_language or "").lower().startswith("en")
        tail = parts[-6:]
        if is_en:
            return " ".join(tail)
        return "。".join(tail) if len(tail) > 1 else tail[0]

    @classmethod
    def _dialog_transcript_from_state_messages(
        cls,
        messages: Optional[List[BaseMessage]],
        detected_language: str,
    ) -> str:
        if not messages:
            return ""
        is_en = (detected_language or "").lower().startswith("en")
        u_lab, a_lab = ("User", "Assistant") if is_en else ("用户", "助手")
        lines: List[str] = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                text = _stream_chunk_to_text(msg.content).strip()
                if text and not cls._is_direct_generate_request(text):
                    lines.append(f"{u_lab}: {text[:4000]}")
            elif isinstance(msg, AIMessage):
                text = _stream_chunk_to_text(msg.content).strip()
                if text:
                    lines.append(f"{a_lab}: {text[:4000]}")
        return "\n".join(lines[-28:])

    @staticmethod
    def _parse_event_data_row(row: Dict[str, Any]) -> Dict[str, Any]:
        event_data = row.get("event_data") or {}
        if isinstance(event_data, str):
            try:
                event_data = json.loads(event_data)
            except Exception:
                event_data = {}
        return event_data if isinstance(event_data, dict) else {}

    @classmethod
    def _collect_attachments_from_conversation_rows(
        cls,
        rows: List[Dict[str, Any]],
    ) -> Tuple[List[ImageUserInput], List[AudioFileUserInput], List[VideoFileUserInput]]:
        images: List[ImageUserInput] = []
        audio_files: List[AudioFileUserInput] = []
        video_files: List[VideoFileUserInput] = []
        seen_urls: set[str] = set()

        def _append(items: Optional[List[Any]], bucket: List[Any], model_cls: type) -> None:
            for raw in items or []:
                if not isinstance(raw, dict):
                    continue
                url = (raw.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                if model_cls is AudioFileUserInput:
                    bucket.append(
                        AudioFileUserInput(
                            url=url,
                            filename=raw.get("filename"),
                            is_new=False,
                            duration=raw.get("duration"),
                            generated_lyrics=raw.get("generated_lyrics"),
                        )
                    )
                else:
                    bucket.append(
                        model_cls(url=url, filename=raw.get("filename"), is_new=False)
                    )

        for row in rows:
            if row.get("event_type") != "user_input":
                continue
            ed = cls._parse_event_data_row(row)
            _append(ed.get("images"), images, ImageUserInput)
            _append(ed.get("audio_files"), audio_files, AudioFileUserInput)
            _append(ed.get("video_files"), video_files, VideoFileUserInput)
        return images, audio_files, video_files

    async def _restore_conversation_attachments_into_user_input(
        self,
        user_input_data: UserInput,
        conversation_id: int,
    ) -> None:
        """直接生成时若本轮未上传文件，从会话历史 user_input 事件恢复图片/音频/视频 URL。"""
        try:
            rows = await async_get_conversation_messages(conversation_id, limit=200)
        except Exception as exc:
            logger.warning("直接生成恢复附件失败: %s", exc)
            return
        if len(rows) > 80:
            rows = rows[-80:]
        hist_images, hist_audio, hist_video = self._collect_attachments_from_conversation_rows(rows)
        try:
            conversation = await async_get_conversation_by_id(conversation_id)
            if conversation and getattr(conversation, "user_input_files", None):
                files_raw = conversation.user_input_files
                if isinstance(files_raw, str):
                    files_raw = json.loads(files_raw)
                if isinstance(files_raw, dict):
                    extra_rows = [{"event_type": "user_input", "event_data": files_raw}]
                    ei, ea, ev = self._collect_attachments_from_conversation_rows(extra_rows)
                    hist_images = merge_user_input_resources(ei, hist_images)
                    hist_audio = merge_user_input_resources(ea, hist_audio)
                    hist_video = merge_user_input_resources(ev, hist_video)
        except Exception as exc:
            logger.warning("直接生成读取 conversation.user_input_files 失败: %s", exc)
        if hist_images and not user_input_data.images:
            user_input_data.images = hist_images
        elif hist_images:
            user_input_data.images = merge_user_input_resources(user_input_data.images, hist_images)
        if hist_audio and not user_input_data.audio_files:
            user_input_data.audio_files = hist_audio
        elif hist_audio:
            user_input_data.audio_files = merge_user_input_resources(user_input_data.audio_files, hist_audio)
        if hist_video and not user_input_data.video_files:
            user_input_data.video_files = hist_video
        elif hist_video:
            user_input_data.video_files = merge_user_input_resources(user_input_data.video_files, hist_video)
        if hist_images or hist_audio or hist_video:
            logger.info(
                "直接生成已恢复历史附件: images=%s audio=%s video=%s",
                len(user_input_data.images or []),
                len(user_input_data.audio_files or []),
                len(user_input_data.video_files or []),
            )

    async def _llm_summarize_conversation_for_direct_generate(
        self,
        *,
        dialog_transcript: str,
        agent_type: Optional[AgentType],
        detected_language: str,
        panel_user_option: Optional[UserOption],
        explicit_user_option_text: str = "",
    ) -> Optional[str]:
        """根据对话节选生成一条可交给创作代理的执行说明。"""
        text = (dialog_transcript or "").strip()
        if not text:
            return None
        from ...services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
        from app.chat.prompts.prompt_loader import load_prompt_with_fallback_async, create_llm_from_model_config
        from app.chat.prompts.prompt_config import PromptName, PROMPTS_CONFIG
        from .utils.llm_resilience import build_resilience_bundle_from_prompt_entry, execute_with_resilience

        at = self._normalize_creative_agent_type(agent_type) if agent_type else AgentType.VIDEO
        is_en = (detected_language or "").lower().startswith("en")
        if is_en:
            hint = {AgentType.VIDEO: "a short video", AgentType.IMAGE: "an image", AgentType.MUSIC: "music", AgentType.STORY: "a story"}.get(
                at, "a creative deliverable"
            )
            lang_hint = "English"
        else:
            hint = {AgentType.VIDEO: "短视频", AgentType.IMAGE: "图片", AgentType.MUSIC: "音乐", AgentType.STORY: "故事"}.get(at, "创意成品")
            lang_hint = "中文"
        panel_user_option_summary, panel_user_option_json = self._format_panel_user_option_for_prompt(panel_user_option)

        prompt_template, _ = await load_prompt_with_fallback_async(
            hub_name=PromptName.AGENT_ROUTER_DIRECT_GENERATE_BRIEF.value,
            local_template_name="agent_router/agent_router_direct_generate_brief",
            schema=None,
            include_raw=False,
        )
        prompt_messages = (await prompt_template.ainvoke(
            {
                "dialog_transcript": text[:14000],
                "creative_agent_hint": hint,
                "output_language_hint": lang_hint,
                "panel_user_option_summary": panel_user_option_summary,
                "panel_user_option_json": panel_user_option_json,
                "explicit_user_option_text": explicit_user_option_text.strip() or "None",
            }
        )).messages
        apply_language_suffix_to_system_message_in_messages(prompt_messages, detected_language or "zh")

        _entry = PROMPTS_CONFIG[PromptName.AGENT_ROUTER_DIRECT_GENERATE_BRIEF]
        _ctx, _routes = build_resilience_bundle_from_prompt_entry(_entry, structured_schema=None)
        from prompts.llm_model_profiles import resolve_model_config

        _mc_base = resolve_model_config(_entry.get("model_config") or {})

        async def _invoke_brief(route: Tuple[str, Any]) -> str:
            model_id, _s = route
            llm = create_llm_from_model_config({**_mc_base, "model": model_id})
            result = await llm.ainvoke(prompt_messages)
            return _stream_chunk_to_text(getattr(result, "content", result)).strip()

        out = await execute_with_resilience(
            _invoke_brief,
            routes=_routes,
            context=_ctx,
            log_context={"phase": "agent_router_direct_generate_brief"},
        )
        if not out or self._is_direct_generate_request(out) or self._is_heuristic_concat_generation_summary(out):
            return None
        return out

    async def _build_direct_generate_summary_from_history(
        self,
        conversation_id: Optional[int],
        agent_type: Optional[AgentType],
        detected_language: str,
        state_messages: Optional[List[BaseMessage]] = None,
        preferred_summary: Optional[str] = None,
        panel_user_option: Optional[UserOption] = None,
    ) -> Optional[str]:
        """根据多轮对话生成创作摘要；优先 LLM 归纳，避免复读用户首句原文。"""
        pref = (preferred_summary or "").strip()
        user_texts: List[str] = []
        dialog_pairs: List[Tuple[str, str]] = []
        ready_summary: Optional[str] = None

        if conversation_id:
            try:
                rows = await async_get_conversation_messages(conversation_id, limit=200)
            except Exception as exc:
                logger.warning("直接生成历史摘要恢复失败: %s", exc)
                rows = []
            if len(rows) > 80:
                rows = rows[-80:]

            for row in rows:
                event_type = row.get("event_type")
                content = (row.get("content") or "").strip()
                event_data = self._parse_event_data_row(row)
                if event_data.get("hidden"):
                    continue

                if event_type == "agent_confirmation_ready":
                    candidate = (
                        event_data.get("summary_input")
                        or event_data.get("confirmed_input_summary")
                        or ""
                    )
                    if self._is_meaningful_generation_summary(candidate):
                        ready_summary = str(candidate).strip()

                role = (row.get("role") or "").strip().lower()
                if event_type == "user_input" and content and not self._is_direct_generate_request(content):
                    if not role or role == "user":
                        user_texts.append(content)
                        dialog_pairs.append(("user", content[:4000]))
                elif event_type == "chat_response" and content:
                    if not role or role in ("ai", "assistant"):
                        if "我会根据以下总结开始生成" not in content and "I will start generation based on this summary" not in content:
                            dialog_pairs.append(("assistant", content[:4000]))

        for msg in state_messages or []:
            if isinstance(msg, HumanMessage):
                text = _stream_chunk_to_text(msg.content).strip()
                if text and not self._is_direct_generate_request(text) and text not in user_texts:
                    user_texts.append(text)

        is_en = (detected_language or "").lower().startswith("en")
        u_lab, a_lab = ("User", "Assistant") if is_en else ("用户", "助手")
        tail = dialog_pairs[-28:]
        lines = [f"{u_lab}: {t}" if k == "user" else f"{a_lab}: {t}" for k, t in tail]
        dialog_transcript = "\n".join(lines)
        summary_for_context = pref if self._is_meaningful_generation_summary(pref) else ready_summary
        if summary_for_context and self._is_meaningful_generation_summary(summary_for_context):
            dialog_transcript = f"{dialog_transcript}\n{a_lab}: 已整理的生成摘要：{summary_for_context}".strip()
        state_transcript = self._dialog_transcript_from_state_messages(state_messages, detected_language)
        if state_transcript and len(state_transcript) > len(dialog_transcript):
            dialog_transcript = state_transcript
            if summary_for_context and self._is_meaningful_generation_summary(summary_for_context):
                dialog_transcript = f"{dialog_transcript}\n{a_lab}: 已整理的生成摘要：{summary_for_context}".strip()
        if not dialog_transcript.strip() and user_texts:
            dialog_transcript = "\n".join(f"{u_lab}: {t}" for t in user_texts[-12:])
        if summary_for_context and not dialog_transcript.strip():
            dialog_transcript = f"{a_lab}: 已整理的生成摘要：{summary_for_context}".strip()
        explicit_user_option_text = "\n".join(
            t for t in user_texts[-12:] if self._text_explicitly_mentions_user_options(t)
        )

        if dialog_transcript.strip():
            try:
                llm_brief = await self._llm_summarize_conversation_for_direct_generate(
                    dialog_transcript=dialog_transcript,
                    agent_type=agent_type,
                    detected_language=detected_language or "zh",
                    panel_user_option=panel_user_option,
                    explicit_user_option_text=explicit_user_option_text,
                )
                if llm_brief and self._is_meaningful_generation_summary(llm_brief):
                    logger.info("直接生成：已用 LLM 根据对话节选生成创作摘要")
                    return llm_brief
            except Exception as exc:
                logger.warning("直接生成 LLM 摘要失败，使用用户原话兜底: %s", exc)

        if user_texts:
            brief = self._heuristic_direct_generate_brief_from_user_lines(
                user_texts, agent_type, detected_language
            )
            panel_summary, _ = self._format_panel_user_option_for_prompt(panel_user_option)
            fallback = f"{brief.strip()}\n\n配置卡片：\n{panel_summary}".strip()
            return fallback or None
        return None

    @staticmethod
    def _fallback_action_user_anchor(user_input: str, max_len: int = 28) -> str:
        """截断用户原话，供 fallback chips 锚定主题（避免与输入无关的模板句）。"""
        core = (user_input or "").strip().replace("\n", " ").replace("\t", " ")
        core = " ".join(core.split())
        if not core:
            return ""
        if len(core) > max_len:
            return core[: max_len - 1].rstrip() + "…"
        return core

    @staticmethod
    def _direct_generate_action_item(detected_language: str) -> Dict[str, Any]:
        lang = (detected_language or "zh").lower()
        label = "Generate directly" if lang.startswith("en") else "直接生成"
        return {"label": label, "message": label, "is_direct_generate": True}

    @classmethod
    def _action_items_from_texts(
        cls,
        texts: List[str],
        detected_language: str,
    ) -> List[Dict[str, Any]]:
        lang = (detected_language or "zh").lower()
        direct_label = "Generate directly" if lang.startswith("en") else "直接生成"
        direct_aliases = {
            "直接生成", "直接开始生成", "开始生成",
            "generate directly", "start generating", "generate now",
        }
        items: List[Dict[str, Any]] = []
        for raw in texts or []:
            text = cls._declarative_action_chip_cleanup(" ".join(str(raw or "").split()))
            if not text or text.lower() in direct_aliases:
                continue
            items.append({"label": text[:80], "message": text[:500]})
        while items and items[-1].get("label", "").lower() in direct_aliases:
            items.pop()
        return items + [cls._direct_generate_action_item(detected_language)]

    @classmethod
    def _build_fallback_action_suggestions(
        cls,
        agent_type: AgentType,
        detected_language: str,
        user_input: str,
        missing_requirements: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fallback chips used only when the structured suggestion model is unavailable."""
        lang = (detected_language or "zh").lower()
        missing_text = " ".join(missing_requirements or [])
        is_en = lang.startswith("en")
        anchor = cls._fallback_action_user_anchor(user_input)
        miss0 = (missing_requirements or [None])[0]
        miss0 = (miss0 or "").strip()[:24] if isinstance(miss0, str) else ""

        if is_en:
            if anchor:
                s0 = f'I want more concrete beats for "{anchor}"'
                s1 = f'I want tighter pacing on "{anchor}"'
                s2 = f'I want the style and ending locked in for "{anchor}"'
            else:
                en_by_agent = {
                    AgentType.VIDEO: (
                        "I want my video idea spelled out with more concrete detail",
                        "I want the shot pacing tightened the way I described",
                        "I want the look and ending set in one clear pass",
                    ),
                    AgentType.IMAGE: (
                        "I want my image idea spelled out with more concrete detail",
                        "I want the layout and palette tightened the way I described",
                        "I want the subject and mood set in one clear pass",
                    ),
                    AgentType.MUSIC: (
                        "I want my track idea spelled out with more concrete detail",
                        "I want the feel and arrangement tightened the way I described",
                        "I want the chorus and ending set in one clear pass",
                    ),
                    AgentType.STORY: (
                        "I want my story idea spelled out with more concrete detail",
                        "I want the characters and conflict tightened the way I described",
                        "I want the structure and ending set in one clear pass",
                    ),
                }
                s0, s1, s2 = en_by_agent.get(
                    agent_type,
                    (
                        "I want my last message spelled out with more concrete detail",
                        "I want the pacing tightened the way I described",
                        "I want the style and ending set in one clear pass",
                    ),
                )
            suggestions = [s0, s1, s2]
            if miss0:
                suggestions[0] = f'I want "{anchor}" with "{miss0}" filled in' if anchor else f'I want that gap filled: {miss0}'
            if "duration" in missing_text.lower():
                suggestions[0] = (
                    f'I want "{anchor}" at about 30 seconds' if anchor else "I want this at about 30 seconds"
                )
            if "style" in missing_text.lower():
                suggestions[1] = (
                    f'I want "{anchor}" in one cohesive style' if anchor else "I want one cohesive style end to end"
                )
            return cls._action_items_from_texts(suggestions[:3] + ["Generate directly"], detected_language)

        if anchor:
            s0 = f"就按「{anchor}」把画面和镜头想得更细一点"
            s1 = f"还是「{anchor}」这个方向，把节奏收得更紧一点"
            s2 = f"围绕「{anchor}」，把风格和结尾一次说清楚"
        else:
            zh_by_agent = {
                AgentType.VIDEO: ("把我刚说的视频想法写具体一点", "镜头节奏我想再收一收", "风格和结尾我想一次定清楚"),
                AgentType.IMAGE: ("把我刚说的画面写具体一点", "构图和用色我想再收一收", "主体和氛围我想一次定清楚"),
                AgentType.MUSIC: ("把我刚说的曲子写具体一点", "情绪和编曲我想再收一收", "副歌和结尾我想一次定清楚"),
                AgentType.STORY: ("把我刚说的故事写具体一点", "人物和冲突我想再收一收", "结构和结尾我想一次定清楚"),
            }
            s0, s1, s2 = zh_by_agent.get(agent_type, ("把我刚说的主题写具体一点", "节奏和镜头我想再收一收", "风格和结尾我想一次定清楚"))
        suggestions = [s0, s1, s2]
        if miss0:
            suggestions[0] = f"围绕「{anchor}」，把「{miss0}」这块我补上" if anchor else f"把我缺的「{miss0}」用陈述写清楚发出去"
        if "时长" in missing_text or "duration" in missing_text.lower():
            suggestions[0] = f"按「{anchor}」把时长定在 30 秒左右" if anchor else "时长就按 30 秒左右来定"
        if "风格" in missing_text or "style" in missing_text.lower():
            suggestions[1] = f"「{anchor}」整体风格走统一的一条线" if anchor else "整体风格走统一的一条线"
        if "内容" in missing_text or "content" in missing_text.lower():
            suggestions[2] = f"在「{anchor}」里把主角、场景变化和结尾亮点写死" if anchor else "主角、场景变化和结尾亮点我想写死"
        return cls._action_items_from_texts(suggestions[:3] + ["直接生成"], detected_language)

    @staticmethod
    def _declarative_action_chip_cleanup(text: str) -> str:
        """去掉疑问标点与句末「吗」，降低模型偶发疑问句对前端的污染。"""
        t = " ".join(str(text or "").split()).strip()
        while t.endswith("?") or t.endswith("？"):
            t = t[:-1].rstrip()
        if t.endswith("吗") and len(t) > 1:
            t = t[:-1].rstrip()
        return t

    @staticmethod
    def _action_suggestion_matches_language(label: str, message: str, detected_language: str) -> bool:
        """过滤明显错语种的推荐动作，避免英文对话里混入中文 chips。"""
        lang = (detected_language or "").lower()
        combined = f"{label} {message}"
        has_cjk = bool(re.search(r"[\u3400-\u9fff]", combined))
        if lang.startswith("en"):
            return not has_cjk
        return True

    @staticmethod
    def _normalize_action_suggestions(
        parsed: ActionSuggestionsResult,
        detected_language: str,
    ) -> List[Dict[str, Any]]:
        lang = (detected_language or "zh").lower()
        direct_aliases = {
            "直接生成", "直接开始生成", "开始生成",
            "generate directly", "start generating", "generate now",
        }
        is_open = parsed.reply_type == "open"
        max_count = 3 if is_open else 5
        min_count = 3 if is_open else 2
        cleaned: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in parsed.suggestions or []:
            label = AgentRouterService._declarative_action_chip_cleanup(
                " ".join(str(getattr(item, "label", "") or "").split())
            )
            message = AgentRouterService._declarative_action_chip_cleanup(
                " ".join(str(getattr(item, "message", "") or "").split())
            )
            if not label and message:
                label = message[:15]
            if not message and label:
                message = label
            if not label:
                continue
            if not AgentRouterService._action_suggestion_matches_language(label, message, detected_language):
                logger.info(
                    "drop action suggestion due to language mismatch detected_language=%s label=%s",
                    detected_language,
                    label[:40],
                )
                continue
            if label.lower() in direct_aliases or message.lower() in direct_aliases:
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append({"label": label[:80], "message": (message or label)[:500]})
            if len(cleaned) >= max_count:
                break
        if len(cleaned) < min_count:
            return []
        return cleaned + [AgentRouterService._direct_generate_action_item(detected_language)]

    async def _build_action_suggestions(
        self,
        agent_type: AgentType,
        detected_language: str,
        user_input: str,
        assistant_reply: str,
        confirmation_result: ConfirmationCheckResult,
        recent_dialog_excerpt: str = "",
    ) -> List[Dict[str, Any]]:
        """Use mustache prompt + structured JSON to generate dynamic action chips."""
        from ...services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
        from app.chat.prompts.prompt_loader import load_prompt_with_fallback_async

        excerpt = (recent_dialog_excerpt or "").strip()
        template_data = self._build_shield_template_data(
            user_input=user_input,
            extra={
                "assistant_reply": assistant_reply,
                "recent_dialog_excerpt": excerpt,
            },
        )
        try:
            prompt_template, _ = await load_prompt_with_fallback_async(
                hub_name=PromptName.AGENT_ROUTER_ACTION_SUGGESTIONS.value,
                local_template_name="agent_router/agent_router_action_suggestions",
                schema=None,
                include_raw=False,
            )
            prompt_messages = (await prompt_template.ainvoke(template_data)).messages
            apply_language_suffix_to_system_message_in_messages(prompt_messages, detected_language)
            raw_result = await ainvoke_structured_resilient(
                prompt_entry=PROMPTS_CONFIG[PromptName.AGENT_ROUTER_ACTION_SUGGESTIONS],
                kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
                structured_chat_messages=prompt_messages,
                include_raw=True,
                structured_schema=ActionSuggestionsResult,
                log_context={"caller": "agent_router_action_suggestions"},
            )
            parsed = raw_result["parsed"]
            raw_message = raw_result.get("raw")
            suggestions = self._normalize_action_suggestions(parsed, detected_language)
            min_required = 3 if getattr(parsed, "reply_type", "") == "open" else 2
            if len(suggestions) >= min_required + 1:
                logger.info(
                    "✅ action suggestions generated: reply_type=%s count=%s raw=%s",
                    getattr(parsed, "reply_type", ""),
                    len(suggestions),
                    bool(raw_message),
                )
                return suggestions
        except Exception as e:
            logger.warning("action suggestions structured generation failed: %s", e)
        return self._build_fallback_action_suggestions(
            agent_type,
            detected_language,
            user_input,
            confirmation_result.missing_requirements,
        )

    async def _resolve_action_suggestions_for_chat(
        self,
        *,
        merged_enabled: bool,
        suggestions_tail: str,
        agent_type: AgentType,
        detected_language: str,
        user_input: str,
        assistant_reply: str,
        confirmation_result: ConfirmationCheckResult,
        recent_dialog_excerpt: str = "",
    ) -> Tuple[List[Dict[str, Any]], str]:
        """合并模式下解析 chat 尾 JSON；失败则回退独立 action_suggestions LLM。"""
        if merged_enabled:
            parsed = parse_merged_action_suggestions(
                suggestions_tail,
                result_model=ActionSuggestionsResult,
            )
            if parsed is not None:
                suggestions = self._normalize_action_suggestions(parsed, detected_language)
                min_required = 3 if getattr(parsed, "reply_type", "") == "open" else 2
                if len(suggestions) >= min_required + 1:
                    logger.info(
                        "merged_action_suggestions=ok reply_type=%s count=%s",
                        getattr(parsed, "reply_type", ""),
                        len(suggestions),
                    )
                    return suggestions, "merged"
            logger.info(
                "merged_action_suggestions=fallback tail_len=%s",
                len(suggestions_tail or ""),
            )
        suggestions = await self._build_action_suggestions(
            agent_type,
            detected_language,
            user_input,
            assistant_reply,
            confirmation_result,
            recent_dialog_excerpt=recent_dialog_excerpt,
        )
        return suggestions, "fallback"

    @staticmethod
    def _has_router_multimodal_content(multimodal_data: Dict[str, Any]) -> bool:
        return bool(
            multimodal_data.get("router_has_images")
            or multimodal_data.get("router_has_audio")
            or multimodal_data.get("router_has_video")
        )

    @staticmethod
    def _get_uploaded_audio_duration_seconds(user_input_data: Optional[UserInput]) -> Optional[float]:
        if not user_input_data:
            return None
        for audio in user_input_data.audio_files or []:
            duration = getattr(audio, "duration", None)
            if duration and duration > 0:
                return float(duration)
        return None

    @staticmethod
    def _get_new_uploaded_audio_duration_seconds(user_input_data: Optional[UserInput]) -> Optional[float]:
        if not user_input_data:
            return None
        for audio in user_input_data.audio_files or []:
            if not getattr(audio, "is_new", False):
                continue
            duration = getattr(audio, "duration", None)
            if duration and duration > 0:
                return float(duration)
        return None

    @classmethod
    def _apply_audio_duration_to_user_option(
        cls,
        user_option: Optional[UserOption],
        user_input_data: Optional[UserInput],
        explicit_duration_text: str = "",
    ) -> Optional[UserOption]:
        if not user_option:
            return user_option
        audio_duration = cls._get_new_uploaded_audio_duration_seconds(user_input_data)
        if audio_duration is None:
            audio_duration = cls._get_uploaded_audio_duration_seconds(user_input_data)
        if audio_duration is None:
            return user_option
        audio_duration_sec = max(5, min(600, math.ceil(audio_duration)))
        current_duration = int(user_option.duration or 0)
        default_panel_duration = UserOption.default().duration
        duration_explicitly_requested = cls._text_explicitly_mentions_duration(
            "\n".join(
                part
                for part in [
                    user_input_data.user_input if user_input_data else "",
                    explicit_duration_text,
                ]
                if part
            )
        )
        if (
            0 < current_duration < audio_duration_sec
            and (current_duration != default_panel_duration or duration_explicitly_requested)
        ):
            user_option.duration = max(5, min(600, current_duration))
            return user_option
        user_option.duration = audio_duration_sec
        return user_option

    async def _generate_multimodal_context_summary(
        self,
        user_input_data: UserInput,
        detected_language: str,
    ) -> Optional[str]:
        from ...services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
        from app.chat.prompts.prompt_loader import load_prompt_with_fallback_async, invoke_prompt_with_multimodal
        from app.chat.prompts.prompt_config import PromptName, PROMPTS_CONFIG

        multimodal_data = self._build_router_multimodal_template_data(user_input_data)
        if not self._has_router_multimodal_content(multimodal_data):
            return None

        prompt_template, llm = await load_prompt_with_fallback_async(
            hub_name=PromptName.AGENT_ROUTER_MULTIMODAL_SUMMARY.value,
            local_template_name="agent_router/agent_router_multimodal_summary",
            schema=None,
            include_raw=False,
        )
        prompt_entry = PROMPTS_CONFIG[PromptName.AGENT_ROUTER_MULTIMODAL_SUMMARY]
        from prompts.llm_model_profiles import resolve_model_config

        summary_model = str(
            resolve_model_config(prompt_entry["model_config"]).get("model") or ""
        )
        template_data = {
            "user_input": user_input_data.user_input,
            **multimodal_data,
        }
        prompt_messages = await invoke_prompt_with_multimodal(prompt_template, template_data, summary_model)
        apply_language_suffix_to_system_message_in_messages(prompt_messages, detected_language)
        result = await llm.ainvoke(prompt_messages)
        summary_text = _stream_chunk_to_text(getattr(result, "content", result)).strip()
        return summary_text or None

    @classmethod
    def _pick_confirmation_agent(
        cls,
        requested_agent_type: Optional[str],
        analyzed_agent_type: Optional[AgentType],
    ) -> AgentType:
        requested_agent = cls._normalize_creative_agent_type(requested_agent_type)
        if requested_agent:
            return requested_agent
        if cls._is_creative_agent(analyzed_agent_type):
            return analyzed_agent_type
        return AgentType.STORY

    def _create_async_send_event_func(self, runtime: Runtime[AgentRouterContextSchema], state: AgentRouterState):
        """创建 send_event 包装：注入 conversation_uuid 和 run_id，确保事件能推送到 Redis stream。
        resume 时优先使用 config 中的 run_id（当前任务 run_id），否则事件会写入旧 run_id 的 stream。"""
        conversation_uuid = state.conversation_uuid if state.conversation_uuid else None
        run_id = state.run_id if state.run_id else None
        try:
            from langgraph.config import get_config
            config_run_id = (get_config() or {}).get("configurable", {}).get("run_id")
            if config_run_id:
                run_id = config_run_id
        except Exception:
            pass

        async def async_send_event_with_db(*args, **kwargs):
            kwargs.pop("async_db", None)  # BaseAgent.async_send_event 不接受，避免 image/story/music 报错
            if conversation_uuid and "conversation_uuid" not in kwargs:
                kwargs["conversation_uuid"] = conversation_uuid
            if run_id and "run_id" not in kwargs:
                kwargs["run_id"] = run_id
            extra = kwargs.get("extra_data") or {}
            if run_id and "run_id" not in extra:
                extra["run_id"] = run_id
                kwargs["extra_data"] = extra
            return await self.async_send_event(*args, **kwargs)
        return async_send_event_with_db

    async def _evaluate_confirmation_result(
        self,
        state: AgentRouterState,
        prompt_messages: List[BaseMessage],
        ai_message: AIMessage,
    ) -> ConfirmationCheckResult:
        """判断当前多轮确认是否已经具备开始生成的条件。"""
        from ...services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
        from app.chat.prompts.prompt_loader import load_prompt_with_fallback_async
        from app.chat.prompts.prompt_config import PromptName, PROMPTS_CONFIG
        from app.chat.services.agent.utils.llm_resilience import (
            StructuredResilienceKind,
            ainvoke_structured_resilient,
        )

        user_input_data = state.user_input_data
        if isinstance(user_input_data, dict):
            user_input_data = UserInput(**user_input_data)

        selected_agent = state.selected_agent if self._is_creative_agent(state.selected_agent) else AgentType.STORY
        dialog_agent = self._dialog_selected_agent(selected_agent)
        confirmation_template, _ = await load_prompt_with_fallback_async(
            hub_name=PromptName.AGENT_ROUTER_CONFIRMATION_CHECK.value,
            local_template_name="agent_router/agent_router_confirmation_check",
            schema=None,
            include_raw=False,
        )
        template_data = {
            "agent_type": dialog_agent.value,
            "user_input": user_input_data.user_input if user_input_data else "",
        }
        confirmation_messages = (await confirmation_template.ainvoke(template_data)).messages
        apply_language_suffix_to_system_message_in_messages(
            confirmation_messages,
            state.detected_language or "en",
        )
        structured_messages = list(state.messages or []) + prompt_messages + [ai_message] + confirmation_messages
        raw_result = await ainvoke_structured_resilient(
            prompt_entry=PROMPTS_CONFIG[PromptName.AGENT_ROUTER_CONFIRMATION_CHECK],
            kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
            structured_chat_messages=structured_messages,
            include_raw=True,
            structured_schema=ConfirmationCheckResult,
        )
        parsed = raw_result["parsed"]
        expected_router_type = self._execution_agent_to_router_analysis_type(
            self._dialog_selected_agent(selected_agent)
        )
        if parsed.agent_type != expected_router_type:
            parsed.agent_type = expected_router_type
        return parsed
    
    async def _build_graph(self) -> CompiledStateGraph:
        """构建Agent路由器图（用于FastAPI服务，使用PostgreSQL持久化）"""
        graph = self._build_router_graph()
        memory = await apostgres_checkpointer()
        return graph.compile(checkpointer=memory, name="Agent Router")

    def build_graph_for_langsmith(self) -> CompiledStateGraph:
        """
        构建用于 LangSmith Deployment 的图（同步版本，无需 PostgreSQL checkpointer）。
        仅用于 langgraph dev 和 LangSmith cloud deployment，不影响 FastAPI 服务逻辑。
        """
        return self._build_router_graph().compile()

    def _build_router_graph(self) -> StateGraph:
        """Agent Router 的 graph 拓扑（FastAPI 与 LangSmith 共用）。

        拓扑：
            START
              └─ process_user_request
                    └─ input_rail           (Prompt Shield 前置；flag off 时透传)
                          ├─ shield_blocked ──→ END
                          └─ else ──→ _decide_route:
                                route_to_video_edit | route_to_video | route_to_story |
                                route_to_music | route_to_image | route_to_chat |
                                route_to_clarify | route_to_unknown
            各 route_to_* → END（route_to_unknown 额外按 interrupt 结果分流到 video/story/music/image）
        """
        graph = StateGraph(AgentRouterState)

        graph.add_node("route_to_video_edit", self._route_to_video_edit)
        graph.add_node("process_user_request", self.process_user_request)
        graph.add_node("input_rail", self._node_input_rail)
        graph.add_node("route_to_video", self._route_to_video)
        graph.add_node("route_to_video_gen", self._route_to_video_gen)
        graph.add_node("route_to_story", self._route_to_story)
        graph.add_node("route_to_music", self._route_to_music)
        graph.add_node("route_to_image", self._route_to_image)
        graph.add_node("route_to_chat", self._route_to_chat)
        graph.add_node("route_to_clarify", self._route_to_clarify)
        graph.add_node("route_to_unknown", self._route_to_unknown)

        graph.add_edge(START, "process_user_request")
        graph.add_edge("process_user_request", "input_rail")
        graph.add_edge("route_to_video_edit", END)

        graph.add_conditional_edges(
            "input_rail",
            self._decide_after_input_rail,
            {
                "end": END,
                "route_to_video_edit": "route_to_video_edit",
                AgentType.VIDEO.value: "route_to_video",
                AgentType.VIDEO_GEN.value: "route_to_video_gen",
                AgentType.STORY.value: "route_to_story",
                AgentType.MUSIC.value: "route_to_music",
                AgentType.IMAGE.value: "route_to_image",
                AgentType.CHAT.value: "route_to_chat",
                AgentType.CLARIFY.value: "route_to_clarify",
                AgentType.UNKNOWN.value: "route_to_unknown",
            },
        )
        graph.add_edge("route_to_video", END)
        graph.add_edge("route_to_video_gen", END)
        graph.add_edge("route_to_story", END)
        graph.add_edge("route_to_music", END)
        graph.add_edge("route_to_image", END)
        graph.add_edge("route_to_chat", END)
        graph.add_edge("route_to_clarify", END)
        graph.add_conditional_edges(
            "route_to_unknown",
            self._decide_route_after_selection,
            {
                AgentType.VIDEO.value: "route_to_video",
                AgentType.VIDEO_GEN.value: "route_to_video_gen",
                AgentType.STORY.value: "route_to_story",
                AgentType.MUSIC.value: "route_to_music",
                AgentType.IMAGE.value: "route_to_image",
            },
        )
        return graph

    def _decide_after_input_rail(self, state: AgentRouterState) -> str:
        """input_rail 之后的条件边：被拦截则直接 END，否则复用原 _decide_route 逻辑。"""
        if getattr(state, "shield_blocked", False):
            return "end"
        return self._decide_route(state)
    
    @staticmethod
    def _delegated_va_run_id_from_conversation_row(conversation: Any) -> Optional[str]:
        ad = getattr(conversation, "additional_data", None)
        if ad is None:
            return None
        if isinstance(ad, str):
            try:
                ad = json.loads(ad)
            except json.JSONDecodeError:
                return None
        if not isinstance(ad, dict):
            return None
        rid = ad.get("delegated_va_run_id") or ad.get("pipeline_run_id")
        if isinstance(rid, str) and rid.strip():
            return rid.strip()
        return None

    @staticmethod
    def _build_va_mcp_url() -> str:
        """从配置推导 MCP endpoint（/mcp 挂在 VA 根路径）。

        优先级（同一份代码，靠环境变量区分部署）：
        1. ``VIDEOAGENT_BASE_URL`` — AWS/分进程部署应显式配置（如 ``https://va.example/api/cuti``）
        2. ``PUBLIC_BASE_URL`` — 合并单体本地/docker 由 start.sh / compose 导出
        3. ``http://127.0.0.1:8000`` — 仅最后兜底，避免拼出相对路径 ``/mcp/``

        若 ``VIDEOAGENT_BASE_URL`` 含 ``/api/cuti``，会剥掉该后缀再拼 ``/mcp/``。
        """
        import os
        from ...config import get_settings
        settings = get_settings()
        base = (settings.VIDEOAGENT_BASE_URL or "").strip().rstrip("/")
        if not base:
            base = (
                (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
                or (getattr(settings, "PUBLIC_BASE_URL", None) or "").strip().rstrip("/")
                or "http://127.0.0.1:8000"
            )
        if "/api/cuti" in base:
            root = base.split("/api/cuti")[0]
        else:
            root = base
        return f"{root}/mcp/"

    async def _route_to_video_edit(
        self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]
    ) -> Dict[str, Any]:
        """与 ``_route_to_video`` 并列：已 delegate 后的视频编辑对话。通过 MCP 获取 video_edit_agent tool，
        由 ChatAgent 侧 create_agent + system prompt 过滤输出；事件走 send_event_func（标准 SSE）。"""
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain.agents import create_agent
        from app.chat.prompts.prompt_config import PromptName, PROMPTS_CONFIG
        from app.chat.prompts.prompt_loader import create_llm_from_model_config
        from .companion_gateway_prompt import VIDEO_EDIT_OUTER_AGENT_SYSTEM_ZH

        req = state.request
        if not req:
            return {}
        rid = state.delegated_va_run_id
        if not rid:
            return {}
        uid = req.user_id
        tid = (req.thread_id or state.thread_id or "").strip()
        ui = req.user_input_data
        if isinstance(ui, dict):
            ui = UserInput(**ui)
        text = (ui.user_input or "").strip()
        if not text:
            return {}

        conversation_id = state.conversation_id
        run_id = state.run_id

        from ...config import get_settings
        svc_token = (get_settings().CUTI_SERVICE_TOKEN or "").strip()
        mcp_url = self._build_va_mcp_url()

        send_event_func = self._create_async_send_event_func(runtime, state)

        # Input Rail 已由独立 input_rail 节点统一处理；被拦截时本节点不会被调度

        # USER_INPUT 已在 process_user_request 中发送，此处不再重复发送

        mcp_client = MultiServerMCPClient({
            "video_edit": {
                "transport": "streamable_http",
                "url": mcp_url,
                "headers": {"Authorization": f"Bearer {svc_token}"},
            }
        })
        mcp_tools = await mcp_client.get_tools()

        from prompts.llm_model_profiles import resolve_model_config

        _ve_config = resolve_model_config(
            PROMPTS_CONFIG[PromptName.VIDEO_EDIT].get("model_config", {}) or {}
        )
        llm = create_llm_from_model_config(_ve_config)
        from langchain.agents.middleware import SummarizationMiddleware
        agent = create_agent(
            llm,
            system_prompt=VIDEO_EDIT_OUTER_AGENT_SYSTEM_ZH,
            tools=mcp_tools,
            middleware=[
                SummarizationMiddleware(
                    model=llm,
                    trigger=("tokens", 80_000),
                    keep=("messages", 20),
                ),
            ],
            name="video_edit_agent",
        )

        context_prefix = f"[context: run_id={rid}, user_id={uid}, thread_id={tid}]\n"

        history: List[BaseMessage] = []
        for msg in state.messages:
            if isinstance(msg, (HumanMessage, AIMessage)) and msg.content:
                history.append(msg)
        history.append(HumanMessage(content=context_prefix + text))

        async def _edit_tokens():
            async for event in agent.astream_events(
                {"messages": history},
                config={"configurable": {"thread_id": f"edit-{tid}"}},
                version="v2",
            ):
                if event.get("event", "") == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        # gpt-5.6 Responses：content 可能是 list[dict] block，需抽成纯文本
                        text = _stream_chunk_to_text(chunk.content)
                        if text:
                            yield text

        full_text, _, _output_blocked, _block_reason = await self._stream_and_emit_with_output_rail(
            _edit_tokens(),
            target_event=MessageType.CHAT_RESPONSE.value,
            conversation_id=conversation_id,
            run_id=run_id,
            state=state,
            send_event_func=send_event_func,
        )

        if _output_blocked:
            reply = await generate_shield_refusal_reply(
                user_snippet=(full_text or "")[-800:],
                shield_reason=_block_reason,
                layer="output",
            )
            await self._send_shield_refusal_message(
                send_event_func,
                event_type=MessageType.CHAT_RESPONSE,
                message=reply,
                state=state,
                extra_data={"source": "video_edit"},
            )
            return {"messages": [HumanMessage(content=text), AIMessage(content="")]}

        final_text = full_text

        if final_text and send_event_func and conversation_id:
            await send_event_func(
                event_type=MessageType.CHAT_RESPONSE,
                conversation_id=conversation_id,
                message=final_text,
                extra_data={
                    "run_id": run_id,
                    "thread_id": tid,
                    "source": "video_edit",
                },
            )

        return {"messages": [HumanMessage(content=text), AIMessage(content=final_text or "")]}

    async def process_user_request(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """处理用户请求 - 初始化会话并验证用户权限"""
        request = state.request
        thread_id = (request.thread_id if request else None) or state.thread_id
        
        # 处理conversation_id并验证安全性（必须在最开始验证）
        conversation_id = (request.conversation_id if request else None) or state.conversation_id
        user_id = (request.user_id if request else None) or state.user_id
        # 使用state中的run_id（从任务中传递过来的）
        # AgentRouterState是Pydantic BaseModel，使用属性访问而不是.get()
        run_id = state.run_id or str(uuid.uuid4())
        
        # ✅ 修复：按需创建连接，不依赖 context
        from ...crud.conversation import async_get_conversation_by_id, async_get_conversation_by_thread_id, async_create_conversation
        from ...models.database import AsyncSessionLocal
        
        # 安全验证：对话表应该在任务提交时已创建，这里只验证和获取
        if conversation_id:
            conversation = await async_get_conversation_by_id(conversation_id)
            if not conversation:
                raise BusinessException(BusinessExceptionCode.NOT_FOUND, "会话不存在")
            if conversation.user_id != user_id:
                raise BusinessException(BusinessExceptionCode.FORBIDDEN, "无权访问此会话")
            if conversation.thread_id != thread_id:
                raise BusinessException(BusinessExceptionCode.FORBIDDEN, "会话thread_id不匹配")
        else:
            # 通过thread_id查找现有会话（应该已存在）
            existing_conversation = await async_get_conversation_by_thread_id(thread_id)
            if not existing_conversation:
                raise BusinessException(
                    BusinessExceptionCode.NOT_FOUND, 
                    "对话不存在，应该在任务提交时创建"
                )
            # 验证用户权限
            if existing_conversation.user_id != user_id:
                raise BusinessException(BusinessExceptionCode.FORBIDDEN, "无权访问此会话")
            conversation_id = existing_conversation.id
            conversation = existing_conversation
            logger.info(f"找到现有会话: {conversation_id}")
        
        is_new_conversation = False  # 不再在这里创建新对话
        
        # ── 恢复 delegated_va_run_id（含老数据 backfill）──
        delegated = state.delegated_va_run_id
        if not delegated and conversation:
            delegated = self._delegated_va_run_id_from_conversation_row(conversation)
        if not delegated and conversation:
            agent_type_str = getattr(conversation, "agent_type", None)
            if agent_type_str == "video":
                from ...crud.conversation import async_get_conversation_runs
                runs = await async_get_conversation_runs(str(conversation_id))
                if len(runs) == 0:
                    delegated = thread_id
                    logger.info(
                        "backfill old data: conversation_id=%s agent_type=video, "
                        "conversation_runs=0 → delegated_va_run_id=%s",
                        conversation_id, delegated,
                    )

        # 获取并合并用户输入数据
        if request:
            # 有新请求：合并新旧数据（新资源在前）
            user_input_data = merge_user_input_data(
                new_user_input_data=request.user_input_data,
                old_user_input_data=state.user_input_data
            )
        else:
            # 无新请求：直接使用 state 中的数据
            user_input_data = state.user_input_data
        
        # ✅ 修复：在 langgraph dev 环境下，state 中的对象会被序列化为 dict
        # 需要将 dict 转换回 UserInput 对象
        if isinstance(user_input_data, dict):
            user_input_data = UserInput(**user_input_data)
        direct_generate_display_input = user_input_data.user_input if user_input_data else ""
        generation_confirmation_summary: Optional[str] = None
        direct_generate_requested = bool(user_input_data and self._is_direct_generate_request(user_input_data.user_input))
        if direct_generate_requested:
            panel_user_option_for_summary = self._apply_audio_duration_to_user_option(
                user_input_data.user_option if user_input_data.user_option else UserOption.default(),
                user_input_data,
                state.confirmed_input_summary or "",
            )
            state_confirmed = (
                (state.confirmed_input_summary or "").strip()
                if self._is_meaningful_generation_summary(state.confirmed_input_summary)
                else ""
            )
            confirmed_input = (
                await self._build_direct_generate_summary_from_history(
                    conversation_id=conversation_id,
                    agent_type=self._normalize_creative_agent_type(user_input_data.agent_type),
                    detected_language=state.detected_language or getattr(state, "language", None) or "zh",
                    state_messages=state.messages,
                    preferred_summary=state_confirmed or None,
                    panel_user_option=panel_user_option_for_summary,
                )
                or ""
            )
            if confirmed_input:
                logger.info("直接生成请求使用对话归纳摘要作为生成输入，避免按钮文案覆盖需求")
                generation_confirmation_summary = confirmed_input
                user_input_data.user_input = confirmed_input
            if conversation_id:
                await self._restore_conversation_attachments_into_user_input(
                    user_input_data, conversation_id
                )
        has_confirmed = bool(user_input_data.has_confirmed if user_input_data else False)
        if direct_generate_requested:
            has_confirmed = True
            user_input_data.has_confirmed = True
        if has_confirmed and not (user_input_data and user_input_data.user_input.strip()):
            raise BusinessException(
                BusinessExceptionCode.INVALID_PARAMETER,
                "has_confirmed=true 时必须携带整理后的 user_input",
            )

        # 处理 userOption：使用 LLM 根据用户输入智能覆盖
        default_user_option = user_input_data.user_option if user_input_data.user_option else UserOption.default()
        explicit_duration_text = "\n".join(
            text
            for text in [
                state.confirmed_input_summary or "",
                generation_confirmation_summary or "",
            ]
            if text
        )
        default_user_option = self._apply_audio_duration_to_user_option(
            default_user_option,
            user_input_data,
            explicit_duration_text,
        )
        merged_user_option = default_user_option
        user_option_merge_input = user_input_data.user_input or ""
        if direct_generate_requested:
            user_option_merge_input = generation_confirmation_summary or user_input_data.user_input or ""
        should_merge_user_option = self._should_merge_user_option_with_input(
            state,
            has_confirmed=has_confirmed,
            direct_generate_requested=direct_generate_requested,
            generation_confirmation_summary=generation_confirmation_summary,
            user_input_text=user_option_merge_input,
        )

        if user_option_merge_input and should_merge_user_option:
            try:
                merged_user_option = await merge_user_option_with_input(
                    user_input=user_option_merge_input,
                    default_user_option=default_user_option
                )
                merged_user_option = self._apply_audio_duration_to_user_option(
                    merged_user_option,
                    user_input_data,
                    explicit_duration_text,
                )
                user_input_data.user_option = merged_user_option
                logger.info("✅ 用户选项已根据输入智能合并")
            except Exception as e:
                logger.warning(f"⚠️ 用户选项合并失败，使用默认值: {e}")
                user_input_data.user_option = self._apply_audio_duration_to_user_option(
                    merged_user_option,
                    user_input_data,
                    explicit_duration_text,
                )
        else:
            user_input_data.user_option = self._apply_audio_duration_to_user_option(
                merged_user_option,
                user_input_data,
                explicit_duration_text,
            )
            if user_input_data.user_input and not should_merge_user_option:
                logger.info("跳过 user_option 合并，使用配置卡片/默认值（prefer_panel 或未在对话中显式提及技术参数）")

        # 直接生成：若摘要未写入 generation_confirmation_summary，但 user_input 已是有效生成文案，则补全（供 USER_INPUT.extra_data 与后续气泡一致）
        if direct_generate_requested and not generation_confirmation_summary:
            _cand = (user_input_data.user_input or "").strip()
            if _cand and not self._is_direct_generate_request(_cand):
                generation_confirmation_summary = _cand

        # 前端若以「整段确认摘要」作为 user_input 提交（未带「直接生成」口令），与 state 摘要对齐后仍走生成摘要气泡逻辑
        if (
            not direct_generate_requested
            and not generation_confirmation_summary
            and has_confirmed
            and user_input_data.has_confirmed
        ):
            u = (user_input_data.user_input or "").strip()
            s1 = (state.confirmed_input_summary or "").strip()
            s2 = (state.confirmed_summary or "").strip()
            if u and not self._is_direct_generate_request(u) and (u == s1 or u == s2):
                generation_confirmation_summary = u
        
        # 准备文件列表（用于存储）
        user_input_files_dict = None
        if user_input_data.images or user_input_data.audio_files or user_input_data.video_files:
            user_input_files_dict = {
                "images": [img.model_dump() for img in user_input_data.images] if user_input_data.images else [],
                "audio_files": [audio.model_dump() for audio in user_input_data.audio_files] if user_input_data.audio_files else [],
                "video_files": [video.model_dump() for video in user_input_data.video_files] if user_input_data.video_files else []
            }
        
        # ConversationRunDB记录应该在任务提交时已创建，这里不再创建
        # 只需要获取conversation对象（如果还没有）
        if 'conversation' not in locals():
            from ...crud.conversation import async_get_conversation_by_id
            conversation = await async_get_conversation_by_id(conversation_id)
        
        logger.info(f"✅ 使用已有对话运行记录: run_id={run_id}, conversation_id={conversation_id}")

        # 发送会话创建事件，前端会监听此事件来刷新对话列表
        # hidden=True: 不在对话框中显示，但前端需要监听此事件来刷新对话列表
        await self.async_send_event(
            event_type=MessageType.SESSION_CREATED,
            conversation_id=conversation_id,
            conversation_uuid=conversation.uuid if conversation else None,
            extra_data={
                "conversation_id": conversation_id,
                "thread_id": thread_id,
                "user_id": user_id,
                "run_id": run_id
            },
            hidden=True
        )
        
        # 先做前置判断：如果前端已携带 agent_type 且 has_confirmed=true，则直接进入对应生成代理
        user_input = user_input_data.user_input
        images = user_input_data.images
        agent_type = user_input_data.agent_type
        messages = state.messages or []
        has_history = len(messages) > 0
        previous_selected_agent = (
            self._dialog_selected_agent(state.selected_agent)
            if self._is_creative_agent(state.selected_agent)
            else None
        )
        prompt_messages: List[BaseMessage] = []
        analysis_result: Optional[RouterAnalysisResult] = None
        raw_message: Optional[BaseMessage] = None
        detected_language = state.detected_language or getattr(state, "language", None) or "en"
        multimodal_context_summary = state.multimodal_context_summary

        selected_agent = self._normalize_creative_agent_type(agent_type)
        if delegated:
            selected_agent = AgentType.VIDEO
            analysis_result = RouterAnalysisResult(
                agent_type=RouterAnalysisAgentType.VIDEO,
                confidence=1.0,
                reason="已存在视频生成委派 run，后续对话固定进入视频编辑",
                key_indicators=["delegated_va_run_id", "route_to_video_edit"],
                detected_language=detected_language,
            )
        elif selected_agent and has_confirmed:
            analysis_result = RouterAnalysisResult(
                agent_type=self._execution_agent_to_router_analysis_type(selected_agent),
                confidence=1.0,
                reason="用户已确认生成，直接进入对应生成代理",
                key_indicators=["has_confirmed=true", f"agent_type={selected_agent.value}"],
                detected_language=detected_language,
            )
        else:
            from app.chat.prompts.prompt_loader import load_prompt_with_fallback_async, invoke_prompt_with_multimodal
            from app.chat.prompts.prompt_config import PromptName, PROMPTS_CONFIG
            from app.chat.services.agent.utils.llm_resilience import (
                StructuredResilienceKind,
                ainvoke_structured_resilient,
            )

            prompt_template, _router_llm = await load_prompt_with_fallback_async(
                hub_name=PromptName.AGENT_ROUTER_ANALYSIS.value,
                local_template_name="agent_router/agent_router_analysis",
                schema=None,
                include_raw=False
            )
            multimodal_template_data = self._build_router_multimodal_template_data(user_input_data)
            template_data = {
                "user_input": user_input,
                "has_images": len(images) > 0 if images else False,
                **multimodal_template_data,
                "specified_agent_type": agent_type if agent_type else None,
                "has_history": has_history
            }
            from prompts.llm_model_profiles import resolve_model_config

            analysis_model = str(
                resolve_model_config(
                    PROMPTS_CONFIG[PromptName.AGENT_ROUTER_ANALYSIS]["model_config"]
                ).get("model")
                or ""
            )
            prompt_messages = await invoke_prompt_with_multimodal(prompt_template, template_data, analysis_model)
            all_messages = messages + prompt_messages
            raw_result = await ainvoke_structured_resilient(
                prompt_entry=PROMPTS_CONFIG[PromptName.AGENT_ROUTER_ANALYSIS],
                kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
                structured_chat_messages=all_messages,
                include_raw=True,
                structured_schema=RouterAnalysisResult,
            )
            analysis_result = raw_result["parsed"]
            raw_message = raw_result.get("raw")
            detected_language = analysis_result.detected_language
            if getattr(state, "language", None):
                detected_language = state.language
            selected_agent = self._router_analysis_agent_to_agent_type(analysis_result.agent_type)
            if selected_agent == AgentType.VIDEO_GEN and not has_confirmed:
                selected_agent = AgentType.VIDEO
            if (
                selected_agent in {AgentType.CLARIFY, AgentType.CHAT}
                and has_history
                and previous_selected_agent
                and not has_confirmed
            ):
                logger.info(
                    "🔒 保持 chat 连续性：analysis=%s，但存在历史创作上下文，沿用上一轮 agent=%s",
                    selected_agent.value,
                    previous_selected_agent.value,
                )
                selected_agent = previous_selected_agent
            if selected_agent == AgentType.CHAT:
                selected_agent = AgentType.CLARIFY
            if getattr(state, "force_chat", None):
                selected_agent = self._pick_confirmation_agent(agent_type, selected_agent)
            elif getattr(state, "force_clarify", None):
                selected_agent = AgentType.CLARIFY

            has_new_multimodal_input = bool(
                request
                and request.user_input_data
                and (
                    request.user_input_data.images
                    or request.user_input_data.audio_files
                    or request.user_input_data.video_files
                )
            )
            if self._has_router_multimodal_content(multimodal_template_data) and (
                has_new_multimodal_input or not multimodal_context_summary
            ):
                try:
                    multimodal_context_summary = await self._generate_multimodal_context_summary(
                        user_input_data=user_input_data,
                        detected_language=detected_language,
                    )
                except Exception as exc:
                    logger.warning("生成多模态附件摘要失败，继续使用已有上下文: %s", exc)

        if has_confirmed:
            multimodal_template_data = self._build_router_multimodal_template_data(user_input_data)
            if self._has_router_multimodal_content(multimodal_template_data) and not multimodal_context_summary:
                try:
                    multimodal_context_summary = await self._generate_multimodal_context_summary(
                        user_input_data=user_input_data,
                        detected_language=detected_language,
                    )
                except Exception as exc:
                    logger.warning("直接生成多模态附件摘要失败: %s", exc)
            if multimodal_context_summary:
                ui_text = (user_input_data.user_input or "").strip()
                if multimodal_context_summary not in ui_text:
                    user_input_data.user_input = f"{ui_text}\n\n【附件参考】{multimodal_context_summary}".strip()
                    if generation_confirmation_summary and multimodal_context_summary not in generation_confirmation_summary:
                        generation_confirmation_summary = user_input_data.user_input

        # video_gen 规格判定：纯 SD2 单模型 + 时长 ≤ 单段上限 → 改走 video_gen 直生（对齐 image agent，画廊式多视频）。
        # 仅在用户已确认生成（has_confirmed）且非 delegated 时生效；对话阶段保持 video。
        if (
            has_confirmed
            and not delegated
            and selected_agent == AgentType.VIDEO
            and self._fits_single_model(getattr(user_input_data, "user_option", None))
        ):
            logger.info("🎬 命中单模型直生规格，video → video_gen")
            selected_agent = AgentType.VIDEO_GEN

        agent_type_value = selected_agent.value
        if analysis_result and selected_agent.value != analysis_result.agent_type.value:
            logger.info(
                "🧪 测试模式启用：保留路由分析结果 %s，但最终路由强制覆盖为 %s",
                analysis_result.agent_type.value,
                selected_agent.value,
            )
        logger.info(f"🌐 识别到用户输入语言: {detected_language}")
        logger.info(f"🎯 选择的 Agent: {agent_type_value}")
        logger.info(f"📊 置信度: {analysis_result.confidence}")
        
        # 写入 DB 两次（conversation 一次、run 一次），供对话列表/详情接口返回
        try:
            from ...crud.conversation import (
                async_update_conversation_agent_type_and_language,
                async_update_conversation_run_agent_type_and_language,
            )
            await async_update_conversation_agent_type_and_language(
                conversation_id, agent_type_value, detected_language
            )
            await async_update_conversation_run_agent_type_and_language(
                run_id, agent_type_value, detected_language
            )
            logger.info(f"✅ 已更新 conversation {conversation_id} 与 run {run_id} agent_type={agent_type_value} language={detected_language}")
        except Exception as e:
            logger.warning(f"⚠️ 更新 conversation/run agent_type/language 失败: {e}")
        
        # 发送用户输入事件（带上 language + agent_type，前端从 stream 或 detail 都能确定）
        new_images = [img for img in user_input_data.images if img.is_new]
        new_audio_files = [audio for audio in user_input_data.audio_files if audio.is_new]
        new_video_files = [video for video in user_input_data.video_files if video.is_new]

        _raw_initial_user_text = (direct_generate_display_input or "").strip()
        _final_user_text = (user_input_data.user_input or "").strip()
        _gen_summary_for_ui = (generation_confirmation_summary or "").strip()
        if _gen_summary_for_ui:
            if self._is_direct_generate_request(_raw_initial_user_text):
                user_input_stream_message = direct_generate_display_input
            else:
                user_input_stream_message = (
                    "Generate directly"
                    if (detected_language or "").lower().startswith("en")
                    else "直接生成"
                )
        else:
            user_input_stream_message = user_input_data.user_input

        await self.async_send_event(
            event_type=MessageType.USER_INPUT,
            conversation_id=conversation_id,
            conversation_uuid=conversation.uuid if conversation else None,
            message=user_input_stream_message,
            extra_data={
                "thread_id": thread_id,
                "run_id": run_id,
                "user_id": user_id,
                "has_confirmed": has_confirmed,
                **({"generation_input_summary": generation_confirmation_summary} if generation_confirmation_summary else {}),
                "detected_language": detected_language,
                "agent_type": agent_type_value,
                "images": [img.model_dump() for img in new_images],
                "images_count": len(new_images),
                "audio_files": [audio.model_dump() for audio in new_audio_files],
                "audio_files_count": len(new_audio_files),
                "video_files": [video.model_dump() for video in new_video_files],
                "video_files_count": len(new_video_files)
            }
        )

        bubble_body = (_final_user_text or "").strip()
        if (
            _gen_summary_for_ui
            and bubble_body
            and not self._is_direct_generate_request(bubble_body)
        ):
            is_zh_bubble = (detected_language or "").lower().startswith("zh")
            summary_message = (
                f"我会根据以下总结开始生成：\n\n{bubble_body}"
                if is_zh_bubble
                else f"I will start generation based on this summary:\n\n{bubble_body}"
            )
            summary_for_extra = _gen_summary_for_ui or bubble_body
            await self.async_send_event(
                event_type=MessageType.CHAT_RESPONSE,
                conversation_id=conversation_id,
                conversation_uuid=conversation.uuid if conversation else None,
                message=summary_message,
                extra_data={
                    "thread_id": thread_id,
                    "run_id": run_id,
                    "agent_type": agent_type_value,
                    "has_confirmed": True,
                    "ready_for_confirmation": False,
                    "confirmed_summary": summary_for_extra,
                    "summary_input": summary_for_extra,
                    "source": "generation_confirmation_summary",
                },
            )

        # 兼容前端：仍发 AGENT_TYPE_DETERMINED（前端 agentType===auto 时据此更新 UI）
        await self.async_send_event(
            event_type=MessageType.AGENT_TYPE_DETERMINED,
            conversation_id=conversation_id,
            conversation_uuid=conversation.uuid if conversation else None,
            extra_data={
                "agent_type": agent_type_value,
                "has_confirmed": has_confirmed,
                "detected_language": detected_language,
                "thread_id": thread_id,
                "run_id": run_id
            },
            hidden=True,
        )

        # 不要把 router analysis 的底层 multimodal prompt 注入后续对话历史。
        # 否则后续 chat/clarify 若切到不同 provider（如 OpenAI），会重新消费这些 media/input_audio 块并报格式错误。
        collected_messages = [raw_message] if raw_message else []
        logger.info(f"✅ 路由分析完成: {agent_type_value}")
        
        result = {
            "user_id": user_id,
            "user_input_data": user_input_data,
            "conversation_id": conversation_id,
            "conversation_uuid": conversation.uuid if conversation else None,
            "thread_id": thread_id,
            "run_id": run_id,
            "detected_language": detected_language,
            "router_analysis": analysis_result,
            "selected_agent": selected_agent,
            "has_confirmed": has_confirmed,
            "ready_for_confirmation": False,
            "multimodal_context_summary": multimodal_context_summary,
            "messages": collected_messages,
        }
        if delegated:
            result["delegated_va_run_id"] = delegated
        return result

    def _decide_route(self, state: AgentRouterState):
        """决定路由方向。
        delegated_va_run_id 存在 → 说明 VA pipeline 已被提交过，后续消息走 route_to_video_edit；
        否则按正常 analysis → confirm → create 流程。
        """
        if state.delegated_va_run_id:
            logger.info(
                "_decide_route: delegated_va_run_id=%s exists, routing to route_to_video_edit",
                state.delegated_va_run_id,
            )
            return "route_to_video_edit"
        if state.selected_agent == AgentType.CLARIFY:
            return AgentType.CLARIFY.value
        if self._is_creative_agent(state.selected_agent):
            return state.selected_agent.value if state.has_confirmed else AgentType.CHAT.value
        return AgentType.CHAT.value
    
    def _decide_route_after_selection(self, state: AgentRouterState):
        """用户选择后的路由决策"""
        # 如果用户已经选择了代理（通过 interrupt resume），使用用户选择
        selected_agent = state.selected_agent
        if selected_agent:
            if self._is_creative_agent(selected_agent):
                return selected_agent.value if state.has_confirmed else AgentType.CHAT.value
            return selected_agent.value
        # 默认路由到故事代理
        return AgentType.CHAT.value
    
    
    
    async def _route_to_video(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到视频代理。"""
        run_id = state.run_id
        execution_state = {
            "user_input_data": state.user_input_data,
            "user_id": state.user_id,
            "conversation_id": state.conversation_id,
            "thread_id": state.thread_id,
            "run_id": run_id,
            "detected_language": state.detected_language or "en",  # 从路由状态获取语言
            "messages": state.messages or [],  # 传递历史消息
            "full_auto": getattr(state, "full_auto", None),
        }
        
        # 视频生成 workflow 在 Cuti-VideoAgent；通过 WorkflowClient 调用（如 HTTP → Worker）
        from .schemas import VideoContextSchema

        context = VideoContextSchema()
        submit_response = await self._workflow_client.invoke_video(execution_state, context=context)

        delegated_va_run_id = None
        if submit_response is not None and getattr(submit_response, "run_id", None):
            delegated_va_run_id = submit_response.run_id
            if state.conversation_id:
                await async_set_conversation_delegated_va_run_id(state.conversation_id, delegated_va_run_id)

        route_message = AIMessage(content="已路由到视频代理")
        out: Dict[str, Any] = {"messages": [route_message]}
        if delegated_va_run_id is not None:
            out["delegated_va_run_id"] = delegated_va_run_id
        return out
    
    async def _route_to_story(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到故事代理（workflow 在 Cuti-VideoAgent，经 WorkflowClient 调用）。"""
        run_id = state.run_id
        user_input_data = state.user_input_data
        messages = state.messages or []
        conversation_id = state.conversation_id
        detected_language = state.detected_language or "en"

        payload = {
            "user_input_data": user_input_data,
            "user_id": state.user_id,
            "messages": messages,
            "conversation_id": conversation_id,
            "run_id": run_id,
            "thread_id": state.thread_id,
            "detected_language": detected_language,
        }
        send_event_func = self._create_async_send_event_func(runtime, state)
        result = await self._workflow_client.invoke_story(payload, send_event_func)
        return {"messages": result.get("messages", [])}
    
    async def _route_to_music(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到音乐代理（workflow 在 Cuti-VideoAgent，经 WorkflowClient 调用）。"""
        run_id = state.run_id
        user_input_data = state.user_input_data
        messages = state.messages or []
        conversation_id = state.conversation_id
        detected_language = state.detected_language or "en"

        payload = {
            "user_input_data": user_input_data,
            "user_id": state.user_id,
            "messages": messages,
            "conversation_id": conversation_id,
            "run_id": run_id,
            "thread_id": state.thread_id,
            "detected_language": detected_language,
        }
        send_event_func = self._create_async_send_event_func(runtime, state)
        result = await self._workflow_client.invoke_music(payload, send_event_func)
        return {"messages": result.get("messages", [])}
    
    async def _route_to_image(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到图像代理（workflow 在 Cuti-VideoAgent，经 WorkflowClient 调用）。"""
        run_id = state.run_id
        user_input_data = state.user_input_data
        messages = state.messages or []
        conversation_id = state.conversation_id
        detected_language = state.detected_language or "en"

        payload = {
            "user_input_data": user_input_data,
            "user_id": state.user_id,
            "messages": messages,
            "conversation_id": conversation_id,
            "run_id": run_id,
            "thread_id": state.thread_id,
            "detected_language": detected_language,
        }
        send_event_func = self._create_async_send_event_func(runtime, state)
        result = await self._workflow_client.invoke_image(payload, send_event_func)
        return {"messages": result.get("messages", [])}

    async def _route_to_video_gen(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到视频直生代理（SD2 单模型，workflow 在 Cuti-VideoAgent，经 WorkflowClient 委派）。"""
        run_id = state.run_id
        user_input_data = state.user_input_data
        messages = state.messages or []
        conversation_id = state.conversation_id
        detected_language = state.detected_language or "en"

        payload = {
            "user_input_data": user_input_data,
            "user_id": state.user_id,
            "messages": messages,
            "conversation_id": conversation_id,
            "run_id": run_id,
            "thread_id": state.thread_id,
            "detected_language": detected_language,
        }
        send_event_func = self._create_async_send_event_func(runtime, state)
        result = await self._workflow_client.invoke_video_gen(payload, send_event_func)
        return {"messages": result.get("messages", [])}

    def _build_shield_template_data(
        self,
        *,
        user_input: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """为 mustache 模板准备统一的 shield 变量。

        无论 ``SHIELD_INPUT_ENABLED`` 开关如何，都必须传入 ``security_policy`` /
        ``escape_user_input`` / ``canary`` 三个键，避免 langchain 的 mustache
        校验把 ``{{#security_policy}}`` section 当成缺失变量报错。
        """
        data: Dict[str, Any] = {"user_input": user_input}
        if extra:
            data.update(extra)
        enabled = False
        canary = ""
        try:
            from ...config import get_settings as _get_settings_sp
            _s = _get_settings_sp()
            enabled = bool(getattr(_s, "SHIELD_INPUT_ENABLED", False))
            canary = getattr(_s, "PROMPT_CANARY", "") or ""
        except Exception:
            pass
        data["security_policy"] = enabled
        data["escape_user_input"] = enabled
        data["canary"] = canary if enabled else ""
        return data

    async def _send_shield_refusal_message(
        self,
        send_event_func,
        *,
        event_type: MessageType,
        message: str,
        state: AgentRouterState,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """与正常助手回复同路径（``async_send_event`` → DB / Redis / LangGraph stream），使用既有
        ``CHAT_RESPONSE`` / ``CLARIFY_RESPONSE``，**不** 另增 event type；``extra_data`` 仅含与常规回复
        一致的安全字段（``thread_id`` / ``run_id`` / 可选 ``source``），不暴露 shield 内部原因。"""
        if not send_event_func or not state.conversation_id:
            return
        safe: Dict[str, Any] = {"thread_id": state.thread_id}
        if state.run_id:
            safe["run_id"] = state.run_id
        if extra_data:
            for k in ("source",):
                if k in extra_data:
                    safe[k] = extra_data[k]
        await send_event_func(
            event_type=event_type,
            conversation_id=state.conversation_id,
            message=message,
            extra_data=safe,
            save_to_db=True,
        )

    async def _node_input_rail(
        self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]
    ) -> Dict[str, Any]:
        """Input Rail 独立节点：在 process_user_request 之后、所有业务路由之前。

        职责：
        - 对 user_input_data.user_input 做 Unicode 净化 + meta-query 正则 +（可选）LLM 预筛
        - 被拦截：记 audit + throttle；经与普通回复相同的 ``async_send_event`` 推送 LLM 拒答正文
          （既有 ``CHAT_RESPONSE`` / ``CLARIFY_RESPONSE``，落库 + stream）；``messages`` 不追加 AIMessage；
          ``shield_blocked=True`` 供后续条件边短路到 END
        - 未拦截：把净化后的 user_input 写回 state.user_input_data（给后续 LLM 用）
        - ``SHIELD_INPUT_ENABLED=False`` 时本节点完全透传，啥也不干
        - 任意异常一律 passthrough，绝不影响主链路
        """
        try:
            from ...config import get_settings as _get_settings_shield
            _settings = _get_settings_shield()
            if not getattr(_settings, "SHIELD_INPUT_ENABLED", False):
                return {}

            user_input_data = state.user_input_data
            if isinstance(user_input_data, dict):
                user_input_data = UserInput(**user_input_data)
            if user_input_data is None:
                return {}
            raw = (user_input_data.user_input or "").strip()
            if not raw:
                return {}

            send_event_func = self._create_async_send_event_func(runtime, state)

            from .prompt_shield import input_rail
            from .prompt_shield_audit import (
                is_user_throttled,
                incr_shield_hit,
                record_shield_event,
            )

            uid = state.user_id or ""
            tid = state.thread_id or ""
            run_id = state.run_id

            # shield 拦截后以哪个事件类型回给前端：依据已路由 agent_type 选择
            selected = getattr(state, "selected_agent", None)
            target_event = (
                MessageType.CLARIFY_RESPONSE
                if selected is not None and selected.value == AgentType.CLARIFY.value
                else MessageType.CHAT_RESPONSE
            )

            if await is_user_throttled(uid):
                reply = await generate_shield_refusal_reply(
                    user_snippet=raw[:800], shield_reason="throttled", layer="input"
                )
                await self._send_shield_refusal_message(
                    send_event_func,
                    event_type=target_event,
                    message=reply,
                    state=state,
                )
                return {"shield_blocked": True, "messages": []}

            sanitized, blocked, reason = await input_rail(raw)

            if not blocked:
                if sanitized != raw:
                    user_input_data.user_input = sanitized
                    return {"user_input_data": user_input_data}
                return {}

            await incr_shield_hit(uid)
            await record_shield_event(
                layer="input",
                reason=reason,
                user_id=uid,
                thread_id=tid,
                run_id=run_id,
                matched_snippet=raw[:500],
                extra={"target_event": target_event.value},
            )
            reply = await generate_shield_refusal_reply(
                user_snippet=raw[:800], shield_reason=reason, layer="input"
            )
            await self._send_shield_refusal_message(
                send_event_func,
                event_type=target_event,
                message=reply,
                state=state,
            )
            return {"shield_blocked": True, "messages": []}
        except Exception as e:
            # shield 失败绝不影响主链路
            logger.warning(f"[shield] input rail node internal error (passthrough): {e}")
            return {}

    async def _stream_and_emit_with_output_rail(
        self,
        token_stream,
        *,
        target_event: str,
        conversation_id: Optional[int],
        run_id: Optional[str],
        state: AgentRouterState,
        send_event_func,
        merged_action_suggestions: bool = False,
    ) -> Tuple[str, str, bool, str]:
        """统一的 LLM 流式输出 + Output Rail 校验 + STREAMING_CHUNK 推送。

        每个模型 delta 到达后按 Unicode 字符拆分并立即推 SSE；Output Rail 同步校验
        当前行内容，但不再用行缓冲决定何时 emit，避免无换行中文回复被攒到结束才一次性发出。

        ``merged_action_suggestions=True`` 时：START 之前为可见聊天流式 chunk；START/END
        之间为 ``content_type=action_suggestions`` 的流式 chunk，并在 END 时发送 phase=end。

        返回 ``(visible_content, tail_content, blocked, block_reason)``：
        - 无合并模式时 tail_content 为空，visible_content 为完整文本
        - ``blocked=True``：校验失败；调用方用拒答覆盖（已通过校验的前缀可能已流式发出）
        """
        output_enabled = bool(getattr(settings, "SHIELD_OUTPUT_ENABLED", False))
        full: List[str] = []
        current_line = ""
        blocked = False
        reason = ""
        marker_splitter = (
            ActionSuggestionsStreamSplitter()
            if merged_action_suggestions
            else None
        )
        suggestions_stream_started = False
        suggestions_end_emitted = False

        async def _emit_stream_piece(piece: str, *, content_type: str = "text", suggestions_phase: Optional[str] = None) -> None:
            if not send_event_func or conversation_id is None:
                return
            if not piece and not suggestions_phase:
                return
            extra: Dict[str, Any] = {
                "target_event": target_event,
                "content_type": content_type,
            }
            if suggestions_phase:
                extra["suggestions_phase"] = suggestions_phase
            await send_event_func(
                event_type=MessageType.STREAMING_CHUNK,
                conversation_id=conversation_id,
                message=piece or "",
                extra_data=extra,
            )

        async for token in token_stream:
            # 防御：Responses / 多模态 chunk 可能是 list[dict]，统一成 str 再按字符过 rail
            text = _stream_chunk_to_text(token) if not isinstance(token, str) else token
            if not text:
                continue

            for piece in text:
                if not piece:
                    continue

                full.append(piece)
                if marker_splitter is not None:
                    visible_piece, suggestions_piece = marker_splitter.feed(piece)
                    emit_piece = visible_piece
                else:
                    visible_piece = piece
                    suggestions_piece = ""
                    emit_piece = piece

                if output_enabled and emit_piece:
                    for ch in emit_piece:
                        current_line += ch
                        ok, r = validate_output_line(current_line)
                        if not ok:
                            blocked = True
                            reason = r
                            break
                        if ch == "\n":
                            current_line = ""
                    if blocked:
                        break

                if emit_piece and not blocked:
                    await _emit_stream_piece(emit_piece, content_type="text")

                if suggestions_piece and not blocked:
                    if not suggestions_stream_started:
                        suggestions_stream_started = True
                        await _emit_stream_piece("", content_type="action_suggestions", suggestions_phase="start")
                    await _emit_stream_piece(
                        suggestions_piece,
                        content_type="action_suggestions",
                    )

                if (
                    marker_splitter is not None
                    and marker_splitter.end_reached
                    and suggestions_stream_started
                    and not suggestions_end_emitted
                    and not blocked
                ):
                    await _emit_stream_piece("", content_type="action_suggestions", suggestions_phase="end")
                    suggestions_end_emitted = True

            if blocked:
                break

        if output_enabled and current_line and not blocked:
            ok, r = validate_output_line(current_line)
            if not ok:
                blocked = True
                reason = r

        if blocked:
            try:
                from .prompt_shield_audit import record_shield_event, incr_shield_hit
                await record_shield_event(
                    layer="output",
                    reason=reason,
                    user_id=state.user_id,
                    thread_id=state.thread_id,
                    run_id=run_id,
                    matched_snippet="".join(full)[-500:],
                    extra={"target_event": target_event},
                )
                await incr_shield_hit(state.user_id)
            except Exception as e:
                logger.warning(f"[shield] output rail audit failed: {e}")

        if (
            marker_splitter is not None
            and suggestions_stream_started
            and not suggestions_end_emitted
            and not blocked
        ):
            await _emit_stream_piece("", content_type="action_suggestions", suggestions_phase="end")

        full_text = "".join(full)
        if marker_splitter is not None:
            visible_text, tail_text = marker_splitter.finish()
            if not visible_text and not tail_text:
                visible_text, tail_text = split_chat_reply_and_suggestions_tail(full_text)
            return visible_text, tail_text, blocked, reason
        return full_text, "", blocked, reason

    async def _route_to_chat(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """围绕创作任务进行确认式对话，并在信息足够时向前端返回确认结果。"""
        user_input_data = state.user_input_data

        if isinstance(user_input_data, dict):
            user_input_data = UserInput(**user_input_data)

        user_input = user_input_data.user_input
        detected_language = state.detected_language or "en"
        conversation_id = state.conversation_id
        run_id = state.run_id
        messages = state.messages or []

        # Input Rail 已由独立 input_rail 节点统一处理；被拦截时本节点不会被调度

        from ...services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
        from app.chat.prompts.prompt_loader import load_prompt_with_fallback_async
        from app.chat.prompts.prompt_config import PromptName

        prompt_template, _ = await load_prompt_with_fallback_async(
            hub_name=PromptName.AGENT_ROUTER_CHAT.value,
            local_template_name="agent_router/agent_router_chat",
            schema=None,
            include_raw=False,
        )

        from ...config import get_settings as _get_settings_chat

        _chat_settings = _get_settings_chat()
        _merged_suggestions = bool(
            getattr(_chat_settings, "CHAT_ACTION_SUGGESTIONS_MERGED", False)
        )

        template_data = self._build_shield_template_data(
            user_input=user_input,
            extra={
                "agent_type": state.selected_agent.value if self._is_creative_agent(state.selected_agent) else "story",
                "multimodal_context_summary": state.multimodal_context_summary or "",
                "chat_action_suggestions_merged": _merged_suggestions,
            },
        )
        prompt_messages = (await prompt_template.ainvoke(template_data)).messages
        apply_language_suffix_to_system_message_in_messages(prompt_messages, detected_language)

        if messages:
            all_messages = messages + prompt_messages
        else:
            all_messages = prompt_messages

        from app.chat.prompts.prompt_config import PROMPTS_CONFIG
        from app.chat.prompts.prompt_loader import create_llm_from_model_config
        from .utils.llm_resilience import (
            build_resilience_bundle_from_prompt_entry,
            execute_with_resilience,
        )

        send_event_func = self._create_async_send_event_func(runtime, state)

        _entry = PROMPTS_CONFIG[PromptName.AGENT_ROUTER_CHAT]
        _ctx, _routes = build_resilience_bundle_from_prompt_entry(_entry)
        from prompts.llm_model_profiles import resolve_model_config

        _mc_base = resolve_model_config(_entry.get("model_config") or {})

        async def _invoke_chat_stream(route: Tuple[str, Any]) -> Dict[str, Any]:
            model_id, _s = route
            llm = create_llm_from_model_config({**_mc_base, "model": model_id})

            async def _tokens():
                async for chunk in llm.astream(all_messages):
                    raw_c = chunk.content if hasattr(chunk, "content") else chunk
                    content = _stream_chunk_to_text(raw_c)
                    if content:
                        yield content

            _output_enabled = bool(getattr(_chat_settings, "SHIELD_OUTPUT_ENABLED", False))
            visible_text, suggestions_tail, _output_blocked, _block_reason = (
                await self._stream_and_emit_with_output_rail(
                    _tokens(),
                    target_event=MessageType.CHAT_RESPONSE.value,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    state=state,
                    send_event_func=send_event_func,
                    merged_action_suggestions=_merged_suggestions,
                )
            )

            if _output_blocked:
                reply = await generate_shield_refusal_reply(
                    user_snippet=(visible_text or "")[-800:],
                    shield_reason=_block_reason,
                    layer="output",
                )
                await self._send_shield_refusal_message(
                    send_event_func,
                    event_type=MessageType.CHAT_RESPONSE,
                    message=reply,
                    state=state,
                )
                ai_message = AIMessage(content="")
                new_messages = prompt_messages + [ai_message]
                return {
                    "messages": new_messages,
                    "has_confirmed": False,
                    "ready_for_confirmation": False,
                    "confirmed_summary": None,
                    "confirmed_input_summary": None,
                }

            final_content = (visible_text or "").strip()
            ai_message = AIMessage(content=final_content)
            new_messages = prompt_messages + [ai_message]
            confirmation_result = await self._evaluate_confirmation_result(
                state=state,
                prompt_messages=prompt_messages,
                ai_message=ai_message,
            )
            recent_excerpt = format_recent_dialog_excerpt_for_action_suggestions(
                messages,
                latest_assistant_text=final_content or "",
                max_turns=RECENT_DIALOG_TURNS_FOR_ACTION_SUGGESTIONS,
                max_chars=RECENT_DIALOG_MAX_CHARS_FOR_ACTION_SUGGESTIONS,
            )
            action_suggestions, _suggestions_source = await self._resolve_action_suggestions_for_chat(
                merged_enabled=_merged_suggestions,
                suggestions_tail=suggestions_tail,
                agent_type=self._router_analysis_agent_to_agent_type(confirmation_result.agent_type),
                detected_language=detected_language,
                user_input=user_input,
                assistant_reply=final_content,
                confirmation_result=confirmation_result,
                recent_dialog_excerpt=recent_excerpt,
            )
            if send_event_func and conversation_id:
                # 开启 Output Rail 时同步剔除前端不消费的内部字段（confirmation_reason / missing_requirements）
                _extra_final: Dict[str, Any] = {
                    "run_id": run_id,
                    "thread_id": state.thread_id,
                    "agent_type": confirmation_result.agent_type.value,
                    "has_confirmed": False,
                    "ready_for_confirmation": confirmation_result.has_confirmed,
                    "confirmed_summary": confirmation_result.confirmed_summary,
                    "summary_input": confirmation_result.summary_input,
                    "action_suggestions": action_suggestions,
                }
                if not _output_enabled:
                    _extra_final["missing_requirements"] = confirmation_result.missing_requirements
                    _extra_final["confirmation_reason"] = confirmation_result.reason
                await send_event_func(
                    event_type=MessageType.CHAT_RESPONSE,
                    conversation_id=conversation_id,
                    message=final_content,
                    extra_data=_extra_final,
                )
                if confirmation_result.has_confirmed:
                    _extra_ready: Dict[str, Any] = {
                        "run_id": run_id,
                        "thread_id": state.thread_id,
                        "agent_type": confirmation_result.agent_type.value,
                        "has_confirmed": False,
                        "ready_for_confirmation": True,
                        "confirmed_summary": confirmation_result.confirmed_summary,
                        "summary_input": confirmation_result.summary_input,
                        "action_suggestions": action_suggestions,
                    }
                    if not _output_enabled:
                        _extra_ready["missing_requirements"] = []
                        _extra_ready["confirmation_reason"] = confirmation_result.reason
                    await send_event_func(
                        event_type=MessageType.AGENT_CONFIRMATION_READY,
                        conversation_id=conversation_id,
                        message=confirmation_result.confirmed_summary or "需求已经整理完成，等待用户确认生成",
                        extra_data=_extra_ready,
                    )
            return {
                "messages": new_messages,
                "has_confirmed": False,
                "ready_for_confirmation": confirmation_result.has_confirmed,
                "confirmed_summary": confirmation_result.confirmed_summary,
                "confirmed_input_summary": confirmation_result.summary_input,
                "selected_agent": self._router_analysis_agent_to_agent_type(confirmation_result.agent_type),
            }

        return await execute_with_resilience(
            _invoke_chat_stream,
            routes=_routes,
            context=_ctx,
            log_context={
                "phase": "agent_router_chat_stream",
                "conversation_id": conversation_id,
                "run_id": run_id,
            },
        )
    
    async def _route_to_clarify(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到澄清代理 - 流式生成澄清消息"""
        user_input_data = state.user_input_data
        
        # ✅ 修复：在 langgraph dev 环境下，state 中的对象会被序列化为 dict
        if isinstance(user_input_data, dict):
            user_input_data = UserInput(**user_input_data)
        
        user_input = user_input_data.user_input
        detected_language = state.detected_language or "en"
        conversation_id = state.conversation_id
        run_id = state.run_id
        messages = state.messages or []

        # Input Rail 已由独立 input_rail 节点统一处理；被拦截时本节点不会被调度

        # 导入必要的模块
        from ...services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
        from app.chat.prompts.prompt_loader import load_prompt_with_fallback_async
        from app.chat.prompts.prompt_config import PromptName
        
        prompt_template, _ = await load_prompt_with_fallback_async(
            hub_name=PromptName.AGENT_ROUTER_CLARIFY.value,
            local_template_name="agent_router/agent_router_clarify",
            schema=None,
            include_raw=False,
        )
        
        template_data = self._build_shield_template_data(user_input=user_input)

        # 格式化消息
        # 使用 invoke 格式化消息（LangSmith 可追踪）
        prompt_messages = (await prompt_template.ainvoke(template_data)).messages
        
        # 添加语言提示（System + Human 都强化）
        apply_language_suffix_to_system_message_in_messages(prompt_messages, detected_language)
        
        # 如果有历史消息，添加到prompt_messages前面
        if messages:
            all_messages = messages + prompt_messages
        else:
            all_messages = prompt_messages
        
        from app.chat.prompts.prompt_config import PROMPTS_CONFIG
        from app.chat.prompts.prompt_loader import create_llm_from_model_config
        from .utils.llm_resilience import (
            build_resilience_bundle_from_prompt_entry,
            execute_with_resilience,
        )

        send_event_func = self._create_async_send_event_func(runtime, state)

        _entry = PROMPTS_CONFIG[PromptName.AGENT_ROUTER_CLARIFY]
        _ctx, _routes = build_resilience_bundle_from_prompt_entry(_entry)
        from prompts.llm_model_profiles import resolve_model_config

        _mc_base = resolve_model_config(_entry.get("model_config") or {})

        async def _invoke_clarify_stream(route: Tuple[str, Any]) -> Dict[str, Any]:
            model_id, _s = route
            llm = create_llm_from_model_config({**_mc_base, "model": model_id})

            async def _tokens():
                async for chunk in llm.astream(all_messages):
                    raw_c = chunk.content if hasattr(chunk, "content") else chunk
                    content = _stream_chunk_to_text(raw_c)
                    if content:
                        yield content

            full_text, _, _output_blocked, _block_reason = await self._stream_and_emit_with_output_rail(
                _tokens(),
                target_event=MessageType.CLARIFY_RESPONSE.value,
                conversation_id=conversation_id,
                run_id=run_id,
                state=state,
                send_event_func=send_event_func,
            )

            if _output_blocked:
                reply = await generate_shield_refusal_reply(
                    user_snippet=(full_text or "")[-800:],
                    shield_reason=_block_reason,
                    layer="output",
                )
                await self._send_shield_refusal_message(
                    send_event_func,
                    event_type=MessageType.CLARIFY_RESPONSE,
                    message=reply,
                    state=state,
                )
                ai_message = AIMessage(content="")
                new_messages = prompt_messages + [ai_message]
                return {"messages": new_messages}

            final_content = (full_text or "").strip()
            ai_message = AIMessage(content=final_content)
            new_messages = prompt_messages + [ai_message]
            if send_event_func and conversation_id:
                await send_event_func(
                    event_type=MessageType.CLARIFY_RESPONSE,
                    conversation_id=conversation_id,
                    message=final_content,
                    extra_data={
                        "run_id": run_id,
                        "thread_id": state.thread_id,
                    },
                )
            return {"messages": new_messages}

        return await execute_with_resilience(
            _invoke_clarify_stream,
            routes=_routes,
            context=_ctx,
            log_context={
                "phase": "agent_router_clarify_stream",
                "conversation_id": conversation_id,
                "run_id": run_id,
            },
        )
    
    async def _route_to_unknown(self, state: AgentRouterState, runtime: Runtime[AgentRouterContextSchema]) -> Dict[str, Any]:
        """路由到未知类型 - 使用 LangGraph interrupt 机制让用户选择"""
        from langgraph.types import interrupt
        
        # 使用 LangGraph interrupt 机制 - 暂停执行等待用户输入
        # 如果是 resume 的情况，interrupt() 会直接返回 resume_data 的值（用户选择的 AgentType 字符串值）
        # 如果是首次进入，会暂停等待用户通过 resume_data 恢复
        user_choice = interrupt({
            "interrupt_type": InterruptType.AGENT_SELECTION.value,
            "available_agents": [AgentType.STORY.value, AgentType.MUSIC.value, AgentType.VIDEO.value, AgentType.IMAGE.value]
        })
        
        # 将用户选择保存到 state 中，供 conditional edge 使用
        # user_choice 应该是 AgentType 的字符串值（如 "video", "story", "music"）
        selected_agent = AgentType(user_choice) if user_choice else AgentType.STORY
        
        return {
            "selected_agent": selected_agent
        }

    async def astream(
        self, 
        user_input_data: UserInput,
        user_id: str, 
        conversation_id: int, 
        thread_id: str,
        async_db = None,
        resume_data: str = None,
        run_id: Optional[str] = None,
        conversation_uuid: Optional[str] = None,
        credit_callback=None,
        full_auto: bool = False,
        language: Optional[str] = None,
        confirmed_input_summary: Optional[str] = None,
        prefer_panel_user_option: bool = False,
        force_clarify: bool = False,
        force_chat: bool = False,
     ) -> AsyncGenerator[str, None]:
        """异步流式处理请求
        
        Args:
            resume_data: 如果提供，表示这是从 interrupt 恢复的请求
            run_id: 如果提供，使用此run_id（用于Worker执行任务）
            credit_callback: 外部传入的成本追踪callback（task_worker创建），若为None则内部自建
            full_auto: 仅 admin 测试用；True 时视频门控不 interrupt，一路执行到完成
        """
        from langgraph.config import RunnableConfig
        
        router_agent = await self._build_graph()
        # 处理thread_id
        if not thread_id:
            thread_id = generate_new_thread_id(user_id)
            logger.info(f"生成新thread_id: {thread_id}")
        
        # 保存 conversation_id、thread_id 和 conversation_uuid 以便在异常处理和结束事件中使用
        _conversation_id = conversation_id
        _thread_id = thread_id
        _conversation_uuid = conversation_uuid  # 直接使用传入的参数
        
        # ✅ 修复：如果 async_db 为 None，不创建 context
        # 让 Agent 节点按需创建连接，避免长时间占用连接
        context = AgentRouterContextSchema(async_db=async_db) if async_db is not None else None

        try:
            # 准备初始状态（只在非 resume 模式下需要）
            initial_state = None
            if not resume_data:
                request = AgentRouterRequest(
                    user_id=user_id,
                    user_input_data=user_input_data,
                    conversation_id=conversation_id,
                    thread_id=thread_id
                )
                initial_state = {
                    "request": request,
                    "run_id": run_id,  # 使用任务中的 run_id
                    "full_auto": full_auto,
                    "language": language,
                    "confirmed_input_summary": confirmed_input_summary,
                    "prefer_panel_user_option": prefer_panel_user_option,
                    "force_clarify": force_clarify,
                    "force_chat": force_chat,
                    # 明确重置 has_confirmed，防止同一 thread_id 的旧 checkpoint 中 has_confirmed=True 泄漏到新轮次
                    # 若不显式写入，LangGraph 会保留 checkpoint 里的旧值，可能导致 dialog 阶段误进入 video 分支
                    "has_confirmed": user_input_data.has_confirmed if user_input_data else False,
                }
            
            # 设置配置（同时设置 thread_id 和 run_id，与 LangGraph 保持一致）
            # 加 "chat:" 前缀避免与 Cuti-VideoAgent router 的 checkpoint 冲突（两服务共享同一 PG checkpoints 表）
            configurable = {"thread_id": f"chat:{thread_id}"}
            if run_id:
                configurable["run_id"] = run_id
            
            # 积分检查callback：外部传入（task_worker创建）直接使用
            callbacks = []
            if credit_callback is not None:
                callbacks.append(credit_callback)
            
            # run_id 放 config 顶层，LangSmith tracer 才会用此 id 作为 root run，后续 read_run(run_id) 才能查到
            config = RunnableConfig(
                configurable=configurable,
                recursion_limit=300,
                callbacks=callbacks if callbacks else None,
                **({"run_id": run_id} if run_id else {}),
            )
            
            # 创建流式生成器
            # 根据 GitHub 讨论，需要使用 ["updates", "custom"] 来同时支持 interrupt 和自定义事件
            astream_kwargs = {
                "config": config,
                "stream_mode": ["updates", "custom"],  # 同时支持 interrupt 和自定义事件
                "subgraphs": True
            }
            
            # 如果提供了context，添加到参数中
            if context is not None:
                astream_kwargs["context"] = context
            
            # 根据是否有 resume_data 来决定调用方式
            if resume_data:
                # 继续被中断的对话 - 使用 Command(resume=...)，并注入当前任务 run_id 到 state，确保事件写入新 run_id 的 Redis stream
                cmd = Command(resume=resume_data, update={"run_id": run_id}) if run_id else Command(resume=resume_data)
                stream_generator = router_agent.astream(
                    cmd,
                    **astream_kwargs
                )
            else:
                # 新的对话
                stream_generator = router_agent.astream(initial_state, **astream_kwargs)
            
            # 处理流式事件
            # 注意：取消检查在 _execute_agent 中通过 cancelled_tasks 进行（Pub/Sub 通知）
            # 这里不需要额外的 Redis 状态检查，避免冗余查询
            stream_ended_by_interrupt = False
            async for event in stream_generator:
                namespace, mode, data = event
                
                # 处理 interrupt 事件
                if mode == "updates" and isinstance(data, dict) and "__interrupt__" in data:
                    interrupt_data = data["__interrupt__"]
                    # 提取 interrupt 的值
                    interrupt_value = interrupt_data[0].value if isinstance(interrupt_data, (list, tuple)) and len(interrupt_data) > 0 else interrupt_data
                    
                    # 通过 async_send_event 发送中断事件：写 DB + 写 Redis stream（event_data 里已含 message_id），status=interrupted 由 worker 在流结束时统一写；run_id 由 base_agent 写入 event_data
                    event_result = await self.async_send_event(
                        event_type=MessageType.INTERRUPT,
                        conversation_id=_conversation_id,
                        conversation_uuid=_conversation_uuid,
                        run_id=run_id,
                        extra_data={
                            "interrupt_data": interrupt_value,
                            "thread_id": _thread_id
                        },
                        save_to_db=True,
                        send_to_stream=True,
                        hidden=False
                    )
                    message_id = event_result.get("message_id") if event_result else None
                    # 同时 yield 给前端（SSE），与 Redis stream 一致带 message_id 作为 msgid
                    stream_ended_by_interrupt = True
                    await _persist_conversation_run_terminal_status(run_id, TaskStatus.INTERRUPTED.value)
                    interrupt_event = {
                        "type": MessageType.INTERRUPT.value,
                        "interrupt_data": interrupt_value,
                        "thread_id": _thread_id,
                        "message_id": message_id,
                        "timestamp": utc_isoformat(datetime.utcnow())
                    }
                    yield f"data: {json.dumps(interrupt_event, ensure_ascii=False)}\n\n"
                    break
                elif mode == "custom":
                    if isinstance(data, dict) and "companion_sse_line" in data:
                        yield data["companion_sse_line"] + "\n"
                    else:
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            
            # 仅当流正常结束（非 interrupt 退出）时发送 STREAM_END，否则前端会误判为完成
            if not stream_ended_by_interrupt:
                logger.info(f'🔚 Stream读取完成，发送结束事件: {_thread_id}')
                await self.async_send_event(
                    event_type=MessageType.STREAM_END,
                    conversation_id=_conversation_id,
                    conversation_uuid=_conversation_uuid,
                    run_id=run_id,
                    extra_data={"thread_id": _thread_id},
                    save_to_db=True,
                    send_to_stream=True,
                    hidden=True
                )
                await _persist_conversation_run_terminal_status(run_id, TaskStatus.COMPLETED.value)
                end_event = {
                    'type': MessageType.STREAM_END.value,
                    'conversation_id': _conversation_id,
                    'thread_id': _thread_id,
                    'timestamp': utc_isoformat(datetime.utcnow())
                }
                yield f"data: {json.dumps(end_event, ensure_ascii=False)}\n\n"
            
        except asyncio.CancelledError:
            logger.info(f"Task {_thread_id} was cancelled")
            
            # 通过 async_send_event 发送取消事件（保存到DB和Redis）
            if async_db:
                try:
                    await self.async_send_event(
                        event_type=MessageType.CANCELLED,
                        conversation_id=_conversation_id,
                        conversation_uuid=_conversation_uuid,
                        run_id=run_id,
                        extra_data={"thread_id": _thread_id},
                        save_to_db=True,
                        send_to_stream=True
                    )
                except Exception as e:
                    logger.error(f"发送取消事件失败: {e}")
            
            await _persist_conversation_run_terminal_status(run_id, TaskStatus.CANCELLED.value)
            cancel_event = {
                'type': MessageType.CANCELLED.value, 
                'conversation_id': _conversation_id,
                'thread_id': _thread_id,
                'timestamp': utc_isoformat(datetime.utcnow())
            }
            yield f"data: {json.dumps(cancel_event, ensure_ascii=False)}\n\n"
        except Exception as error:
            logger.error(f'智能对话流式处理失败: {error}', exc_info=True)
            
            # 通过 async_send_event 发送错误事件（保存到DB和Redis）
            if async_db:
                try:
                    await self.async_send_event(
                        event_type=MessageType.ERROR,
                        conversation_id=_conversation_id,
                        conversation_uuid=_conversation_uuid,
                        run_id=run_id,
                        message=str(error),
                        extra_data={"error_details": str(error), "thread_id": _thread_id},
                        save_to_db=True,
                        send_to_stream=True
                    )
                except Exception as e:
                    logger.error(f"发送错误事件失败: {e}")
            
            err_msg = str(error)[:2000]
            await _persist_conversation_run_terminal_status(run_id, TaskStatus.FAILED.value, error_message=err_msg)
            # 同时 yield 给前端（SSE）
            error_event = {
                'type': MessageType.ERROR.value,
                'conversation_id': _conversation_id,
                'thread_id': _thread_id,
                'timestamp': utc_isoformat(datetime.utcnow()),
                'message': str(error)  # 包含错误信息，便于调试
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            # 重新抛出异常，让调用者处理（Worker模式需要知道任务失败）
            raise
    

_agent_router_service_instance = None
def get_agent_router_service() -> AgentRouterService:
    """获取Agent路由器服务单例"""
    global _agent_router_service_instance
    if _agent_router_service_instance is None:
        _agent_router_service_instance = AgentRouterService()
    return _agent_router_service_instance


def get_agent_router() -> CompiledStateGraph:
    return get_agent_router_service().agent
