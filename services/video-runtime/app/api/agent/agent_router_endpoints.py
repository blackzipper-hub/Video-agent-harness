"""
Agent路由器API端点 - 统一的agent接入接口
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any, Literal, cast
from fastapi import APIRouter, UploadFile, File, Form, Depends, Security, Query, Request
from sqlmodel import Session
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from langsmith import traceable
from langchain_core.runnables.config import RunnableConfig, var_child_runnable_config
from ...callbacks.credit_check_callback import create_credit_check_callback

from ...services.auth_service import auth_service
from ...exceptions import BusinessException, BusinessExceptionCode
from ...models.database import get_db, get_async_db
from ...schemas import ResponseModel
from ...schemas.prompt_edit_presets import PromptEditPresetsOutput
from prompts.prompt_config import PromptName, PROMPTS_CONFIG
from ...services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
from ...services.agent.agent_router_service import get_agent_router_service
from ...models.user_options import (
    UserOption,
    VideoGenerationTool,
    ImageGenerationTool,
    DEFAULT_VIDEO_TOOL,
    DEFAULT_IMAGE_TOOL,
    get_tool_capabilities,
    materialize_video_generation_tool,
)
from ...services.redis.stream_service import GENERATED_EVENTS, PROGRESS_EVENTS
from ...models.task_status import TaskStatus, BillingStatus, RunType
from ...models.database import get_async_db
from sqlalchemy.ext.asyncio import AsyncSession
from ...models.video_state import (
    VideoAgentState,
    ImageUserInput,
    AudioFileUserInput
)
from ...services.agent.video_agent_service import VideoAgentService
from ...models.version_regenerate_strategy import (
    CharacterRegenerateStrategy,
    KeyframeRegenerateStrategy,
    VideoRegenerateStrategy,
)
from ...utils.asyncpg_utils import utc_isoformat
from ...crud.conversation import (
    async_get_conversation_by_thread_id,
    async_get_conversation_run_by_run_id,
    async_create_conversation,
    async_create_conversation_run,
    async_update_conversation_run_status,
    async_update_conversation_run_billing,
    async_set_conversation_run_billing_pending_if_not_completed,
    async_create_conversation_run_for_regenerate,
    async_update_conversation_run_after_regenerate,
    async_get_message_by_id,
    async_get_message_event_type_and_data_for_conversation,
    async_update_message_event_data_partial,
    async_get_runs_by_thread_id_and_status,
)

logger = logging.getLogger(__name__)


async def validate_thread_id(thread_id: Optional[str], user_id: str) -> None:
    """验证 thread_id 是否属于该 user_id（使用asyncpg CRUD）"""
    if thread_id:
        conversation = await async_get_conversation_by_thread_id(thread_id)
        conv_user_id = conversation.user_id if conversation else None
        if conversation and conv_user_id != user_id:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "无权访问此 thread_id"
            )

# 请求模型
class KeyframeVersionRequest(BaseModel):
    """关键帧版本请求"""
    uuid: str = ""
    custom_prompt: Optional[str] = Field(
        default=None,
        description="整段 t2i；若同时传 instruction，则可表示显式覆盖的基底 prompt",
    )
    instruction: Optional[str] = Field(
        default=None,
        description="可选：用户自然语言修改说明，与 custom_prompt 在 LLM 模板中分开展示",
    )
    use_reflection: Optional[bool] = False  # 是否使用 AI reflection 优化（小火箭功能）
    regenerate_strategy: KeyframeRegenerateStrategy = Field(
        default=KeyframeRegenerateStrategy.PROMPT_REGENERATE,
        description="prompt_regenerate（默认，以 custom_prompt 为主走完整出图）| instruction_regenerate（以 instruction 为主走完整出图）| instruction_merge_prompt（只融 t2i，仅 API 返回全文，不写新版本行）| instruction_edit_image（成图 I2I）",
    )

class KeyframeRequest(BaseModel):
    """关键帧请求"""
    uuid: str = ""
    shot_number: Optional[int] = None
    frame_index: int = 0
    versions: List[KeyframeVersionRequest]

    @model_validator(mode="after")
    def _require_shot_when_no_keyframe_uuid(self) -> "KeyframeRequest":
        if not (self.uuid or "").strip():
            if self.shot_number is None:
                raise ValueError("shot_number 必填（当 keyframe uuid 为空时表示空槽补全）")
        return self

class RegenerateKeyframesRequest(BaseModel):
    """重新生成关键帧请求"""
    keyframes: List[KeyframeRequest]
    user_option: Optional[UserOption] = None
    thread_id: Optional[str] = None

class VideoVersionRequest(BaseModel):
    """视频版本请求"""
    uuid: str = ""
    custom_prompt: Optional[str] = None
    instruction: Optional[str] = None
    forced_keyframe_version_uuid: Optional[str] = Field(
        default=None,
        description="非空时 I2V 取该关键帧版本出图，而不使用 keyframe.current_version_index（不写 DB 选用）",
    )
    regenerate_strategy: VideoRegenerateStrategy = Field(
        default=VideoRegenerateStrategy.PROMPT_REGENERATE,
        description="prompt_regenerate（默认，完整 I2V；可只传 instruction 用当前 motion 为基底）| instruction_merge_prompt（只融 motion，仅 API 返回，不写新版本行，不跑 I2V）",
    )

class VideoRequest(BaseModel):
    """视频请求"""
    uuid: str = ""
    shot_number: Optional[int] = None
    versions: List[VideoVersionRequest]

    @model_validator(mode="after")
    def _require_shot_when_no_video_uuid(self) -> "VideoRequest":
        if not (self.uuid or "").strip():
            if self.shot_number is None:
                raise ValueError("shot_number 必填（当 video uuid 为空时表示空槽补全）")
        return self

class RegenerateVideosRequest(BaseModel):
    """重新生成视频请求"""
    videos: List[VideoRequest]
    user_option: Optional[UserOption] = None
    thread_id: Optional[str] = None

class VideoSegmentVersionRequest(BaseModel):
    """视频片段版本请求"""
    segment_uuid: str  # 视频片段UUID
    segment_version_uuid: str  # 片段版本UUID


class VideoAssemblySelectedVersion(BaseModel):
    """视频合成时选中的 shot 版本"""
    uuid: str  # video_generation_version UUID


class VideoAssemblyVideoRequest(BaseModel):
    """视频合成时按 shot 指定版本（不展示 lyrics 时用此先 sync 再合成）"""
    uuid: str  # video_generation UUID
    selected_version: Optional[VideoAssemblySelectedVersion] = None


class VideoAssemblyRequest(BaseModel):
    """视频合成请求（按 thread 组装：使用该 thread 下当前全部资源）"""
    thread_id: str  # 必填，按该 thread 取资源并合成
    segment_versions: Optional[List[VideoSegmentVersionRequest]] = None  # 指定的片段版本列表（不展示 lyrics 时可省略）
    videos: Optional[List[VideoAssemblyVideoRequest]] = None  # 按 shot 选中的版本；传入时先 sync 再合成
    user_option: Optional[UserOption] = None

class MusicPromptUpdateRequest(BaseModel):
    """音乐提示词更新请求"""
    music_prompt: str

class SyncSegmentsVideoVersionRequest(BaseModel):
    """同步片段的视频版本请求（供 video_agent_service.sync_segments_by_request 使用）"""
    video_generation_uuid: str  # video_generation UUID
    video_generation_version_uuid: str  # video_generation_version UUID

class CharacterVersionRequest(BaseModel):
    """角色版本请求"""
    uuid: str
    custom_prompt: Optional[str] = None
    instruction: Optional[str] = None
    regenerate_strategy: CharacterRegenerateStrategy = Field(
        default=CharacterRegenerateStrategy.PROMPT_REGENERATE,
        description="prompt_regenerate（默认，完整出图；可只传 instruction 用当前 t2i 为基底）| instruction_merge_prompt（只融 t2i，仅 API 返回，不写新版本行）| instruction_edit_image（成图 I2I）",
    )

class CharacterRequest(BaseModel):
    """角色请求"""
    uuid: str
    versions: List[CharacterVersionRequest]

class RegenerateCharactersRequest(BaseModel):
    """重新生成角色请求"""
    characters: List[CharacterRequest]
    user_option: Optional[UserOption] = None
    thread_id: Optional[str] = None

class SelectCharacterVersionRequest(BaseModel):
    """选中角色版本请求"""
    version_uuid: str


class SelectKeyframeVersionRequest(BaseModel):
    """选中关键帧版本请求"""
    version_uuid: str


class SelectVideoVersionRequest(BaseModel):
    """选中视频版本请求"""
    version_uuid: str


class SuggestPromptEditPresetsRequest(BaseModel):
    """请求根据当前 prompt（与可选参考图）生成快捷修改建议"""
    artifact_kind: Literal["character", "keyframe", "video"]
    prompt: str = Field(default="", max_length=50000)
    image_url: Optional[str] = None
    video_url: Optional[str] = Field(
        default=None,
        description="镜头视频 artifact 时可选：当前版本成片/片段 URL，供多模态理解动态",
    )
    thread_id: Optional[str] = None


# 创建路由器
router = APIRouter(prefix="/agent-router", tags=["agent-router"])


async def get_async_db_dependency():
    """异步数据库依赖
    
    注意：不要手动关闭连接，get_async_db() 的 async with 会自动管理连接生命周期
    手动关闭会导致与 LangGraph runtime 的生命周期冲突，造成连接泄露
    """
    async for db in get_async_db():
        yield db


class AgentRouterRequest(BaseModel):
    """Agent路由器请求模型"""
    user_input: str
    images: Optional[List[ImageUserInput]] = []
    audio_files: Optional[List[AudioFileUserInput]] = []
    conversation_id: Optional[int] = None
    thread_id: Optional[str] = None
    user_option: Optional[UserOption] = None


class ChatDelegateSubmitRequest(BaseModel):
    """供 VideoChatAgent 委托下游 agent（任务入队后 Worker 跳过路由 LLM）。"""
    thread_id: str
    user_input: str
    target_agent: Literal["video", "video_gen", "story", "music", "image"]
    user_option: Optional[UserOption] = None
    language: Optional[str] = None
    user_input_files: Optional[Dict[str, Any]] = None


class ResumeRequest(BaseModel):
    """恢复中断流程的请求模型"""
    thread_id: str
    user_response: str
    conversation_id: int


class CancelRequest(BaseModel):
    """取消任务的请求模型"""
    thread_id: str


class DismissInterruptAutoResumeRequest(BaseModel):
    """取消「本次 interrupt 的 15s 自动 continue」（仍可手动点继续）。须传当前暂停中的父 run_id 与 interrupt 消息 id。"""
    thread_id: str
    interrupted_run_id: str
    interrupt_msgid: int


# Response Models
class DismissInterruptAutoResumeResponse(BaseModel):
    """关闭本次 interrupt 自动继续成功后的 data 载荷。"""
    ok: bool = True


class CancelTaskResponse(BaseModel):
    thread_id: str
    cancelled: bool

class RunningTasksResponse(BaseModel):
    running_tasks: List[Dict[str, Any]]
    count: int

class EditResponse(BaseModel):
    success: bool
    message: str
    request_id: str
    results_count: int

class ReflectionResponse(BaseModel):
    success: bool
    message: str
    evaluation: Optional[Dict[str, Any]]
    content_type: str

class VersionSelectionResponse(BaseModel):
    success: bool
    message: str
    shot_number: int
    version_index: int
    content_type: str

class AutoEditResponse(BaseModel):
    success: bool
    message: str
    final_video: Optional[Dict[str, Any]]

class EditingStateResponse(BaseModel):
    success: bool
    state: Dict[str, Any]
    conversation_id: int


def _parse_resume_data(resume_data: Optional[str]) -> Optional[Dict[str, Any]]:
    """若 resume_data 为 JSON 且含 run_id、interrupt_msgid 则返回 dict，否则返回 None。"""
    if not resume_data or not resume_data.strip():
        return None
    try:
        import json
        data = json.loads(resume_data) if isinstance(resume_data, str) else resume_data
        if isinstance(data, dict) and data.get("run_id") and data.get("interrupt_msgid") is not None:
            return data
    except Exception:
        pass
    return None


async def _submit_resume(
    resume_payload: Dict[str, Any],
    thread_id: str,
    user_id: str,
) -> Dict[str, Any]:
    """Resume：校验老 run + 生成新 run_id + 创建新 conversation_run + 入队。老 run 保持 INTERRUPTED。"""
    from ...services.task_enqueue_service import prepare_resume_task
    from ...services.queue import create_task_queue
    from ...services.redis.connection import get_redis_stream_service

    task_data = await prepare_resume_task(resume_payload, user_id)
    redis_service = await get_redis_stream_service()
    sqs_service = create_task_queue()
    await sqs_service.add_task_to_queue(task_data)
    await redis_service.add_task_index(thread_id, task_data["run_id"])
    result = {
        "run_id": task_data["run_id"],
        "thread_id": thread_id,
        "conversation_id": task_data["conversation_id"],
        "status": TaskStatus.RESUME_QUEUED.value,
        "message": "已继续，任务已入队"
    }
    return result


async def _submit_new_task(
    conversation: Any,
    thread_id: str,
    user_input: str,
    user_option: Optional[str],
    agent_type: Optional[str],
    files: List[UploadFile],
    user_id: str,
) -> Dict[str, Any]:
    """新任务：文件处理、解析选项、积分校验，然后复用入队服务创建 run、入队。"""
    import json
    from ...utils.file_utils import process_uploaded_files
    from ...services.task_enqueue_service import enqueue_video_task

    images, audio_files, video_files = await process_uploaded_files(files)
    parsed_user_option = None
    if user_option:
        try:
            user_option_dict = json.loads(user_option)
            parsed_user_option = UserOption(**user_option_dict)
            parsed_user_option = materialize_video_generation_tool(parsed_user_option) or parsed_user_option
            if parsed_user_option.image_generation_tool == ImageGenerationTool.AUTO:
                parsed_user_option.image_generation_tool = DEFAULT_IMAGE_TOOL
        except Exception:
            parsed_user_option = UserOption.default()
            parsed_user_option = materialize_video_generation_tool(parsed_user_option) or parsed_user_option
            if parsed_user_option.image_generation_tool == ImageGenerationTool.AUTO:
                parsed_user_option.image_generation_tool = DEFAULT_IMAGE_TOOL
    else:
        parsed_user_option = UserOption.default()
        parsed_user_option = materialize_video_generation_tool(parsed_user_option) or parsed_user_option
        if parsed_user_option.image_generation_tool == ImageGenerationTool.AUTO:
            parsed_user_option.image_generation_tool = DEFAULT_IMAGE_TOOL
    user_input_files_dict = None
    if images or audio_files or video_files:
        user_input_files_dict = {
            "images": [img.model_dump() for img in images] if images else [],
            "audio_files": [audio.model_dump() for audio in audio_files] if audio_files else [],
            "video_files": [video.model_dump() for video in video_files] if video_files else []
        }

    from ...utils.credit_deduction_utils import check_credits_before_task
    check_ok, _, check_err = await check_credits_before_task(user_id)
    if not check_ok:
        raise BusinessException(BusinessExceptionCode.INSUFFICIENT_CREDITS, check_err)

    user_option_for_enqueue = parsed_user_option.model_dump() if parsed_user_option else {}

    run_id = await enqueue_video_task(
        conversation_id=conversation.id,
        conversation_uuid=conversation.uuid,
        thread_id=thread_id,
        user_id=user_id,
        user_input=user_input,
        user_option=user_option_for_enqueue or None,
        user_input_files=user_input_files_dict,
        agent_type=agent_type or "video",
        full_auto=False,
    )
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "conversation_id": conversation.id,
        "status": TaskStatus.QUEUED.value,
        "message": "任务已加入队列"
    }


async def _submit_delegate_task(
    conversation: Any,
    thread_id: str,
    user_input: str,
    target_agent: str,
    user_option: Optional[UserOption],
    user_input_files: Optional[Dict[str, Any]],
    language: Optional[str],
    user_id: str,
) -> Dict[str, Any]:
    """ChatAgent 委托：入队并标记 skip_agent_router，Worker 内跳过路由 LLM。"""
    from ...services.task_enqueue_service import enqueue_video_task

    parsed_user_option = user_option
    if parsed_user_option is None:
        parsed_user_option = UserOption.default()
    # Short Drama + AUTO → SD2；勿先写成 DEFAULT(pollo_seedance) 否则跳关键帧默认失效
    parsed_user_option = materialize_video_generation_tool(parsed_user_option) or parsed_user_option
    if parsed_user_option.image_generation_tool == ImageGenerationTool.AUTO:
        parsed_user_option.image_generation_tool = DEFAULT_IMAGE_TOOL
    user_option_for_enqueue = parsed_user_option.model_dump() if parsed_user_option else {}

    # 自托管默认「自动继续」：VIDEO_FULL_AUTO=True 时写入 user_option.full_auto，
    # 门闩仍会 interrupt，但后端排 15s（智能裁切 60s）延迟 auto_resume；前端展示倒计时与「取消自动继续」。
    # 注意：不要写入 task_data/state.full_auto——那是 SmartTest「跳过门闩」路径，不会出现倒计时。
    from ...config import get_settings as _get_settings_full_auto
    if bool(_get_settings_full_auto().VIDEO_FULL_AUTO):
        user_option_for_enqueue = dict(user_option_for_enqueue or {})
        user_option_for_enqueue["full_auto"] = True

    run_id = await enqueue_video_task(
        conversation_id=conversation.id,
        conversation_uuid=conversation.uuid,
        thread_id=thread_id,
        user_id=user_id,
        user_input=user_input,
        user_option=user_option_for_enqueue or None,
        user_input_files=user_input_files,
        agent_type=target_agent,
        skip_agent_router=True,
        full_auto=False,
        language=language,
    )
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "conversation_id": conversation.id,
        "status": TaskStatus.QUEUED.value,
        "message": "任务已加入队列（委托模式）",
    }


@router.post("/delegate-submit")
async def agent_router_delegate_submit(
    body: ChatDelegateSubmitRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """
    供 VideoChatAgent 调用：按已选定的下游 agent 入队，不经过路由 LLM。
    与 ``/stream`` 并存；旧客户端继续用 Form ``/stream``。
    """
    try:
        thread_id = body.thread_id
        conversation = await async_get_conversation_by_thread_id(thread_id)
        if not conversation:
            title = body.user_input[:50] if len(body.user_input) <= 50 else f"{body.user_input[:50]}..."
            conversation = await async_create_conversation(
                user_id=user_id,
                thread_id=thread_id,
                title=title,
            )
        else:
            if conversation.user_id != user_id:
                raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "无权访问此对话")
        from ...utils.credit_deduction_utils import check_credits_before_task
        check_ok, _, check_err = await check_credits_before_task(user_id)
        if not check_ok:
            raise BusinessException(BusinessExceptionCode.INSUFFICIENT_CREDITS, check_err)
        data = await _submit_delegate_task(
            conversation,
            thread_id,
            body.user_input,
            body.target_agent,
            body.user_option,
            body.user_input_files,
            body.language,
            user_id,
        )
        return ResponseModel.success(data=data)
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"❌ delegate-submit 失败: {e}")
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            f"处理失败: {str(e)}"
        )


@router.post("/stream")
async def agent_router_stream(
    user_input: Optional[str] = Form(None),
    conversation_id: Optional[int] = Form(None),
    thread_id: Optional[str] = Form(None),
    user_option: Optional[str] = Form(None),
    resume_data: Optional[str] = Form(None),  # resume 时传 JSON：{"run_id":"...","interrupt_msgid":123}
    agent_type: Optional[str] = Form(None),
    files: List[UploadFile] = File(default=[]),
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """
    提交任务到队列。新任务：传 user_input、可选 thread_id。Resume：传 resume_data（JSON 含 run_id、interrupt_msgid）与 thread_id。
    """
    import uuid
    try:
        resume_payload = _parse_resume_data(resume_data)
        if resume_payload:
            if not thread_id:
                raise BusinessException(
                    BusinessExceptionCode.INVALID_PARAMETER,
                    "resume 时必须传 thread_id"
                )
            logger.info(f"🔄 Resume: user_id={user_id}, run_id={resume_payload.get('run_id')}")
            # ---- Resume 也需预检查积分 ----
            from ...utils.credit_deduction_utils import check_credits_before_task
            check_ok, _, check_err = await check_credits_before_task(user_id)
            if not check_ok:
                raise BusinessException(BusinessExceptionCode.INSUFFICIENT_CREDITS, check_err)
            conversation = await async_get_conversation_by_thread_id(thread_id)
            if not conversation:
                raise BusinessException(
                    BusinessExceptionCode.RESOURCE_NOT_FOUND,
                    "未找到对应对话，无法 resume"
                )
            if conversation.user_id != user_id:
                raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "无权访问此对话")
            try:
                data = await _submit_resume(resume_payload, thread_id, user_id)
                return ResponseModel.success(data=data)
            except BusinessException as e:
                # 幂等：RESUME_ALREADY_CONTINUED 时不向前端报错，返回 200 让前端无感
                if e.error_code == BusinessExceptionCode.RESUME_ALREADY_CONTINUED:
                    active_runs = await async_get_runs_by_thread_id_and_status(
                        thread_id,
                        [TaskStatus.QUEUED.value, TaskStatus.RESUME_QUEUED.value, TaskStatus.RUNNING.value],
                    )
                    run_id = active_runs[0]["run_id"] if active_runs else resume_payload.get("run_id")
                    conversation_id = active_runs[0]["conversation_id"] if active_runs else conversation.id
                    return ResponseModel.success(data={
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "conversation_id": conversation_id,
                        "status": TaskStatus.RESUME_QUEUED.value,
                        "message": "该任务已继续，请刷新或等待进度更新",
                    })
                raise
        else:
            if not user_input:
                raise BusinessException(
                    BusinessExceptionCode.INVALID_PARAMETER,
                    "新任务必须传 user_input"
                )
            logger.info(f"🚀 新任务: user_id={user_id}, input={user_input[:50]}...")
            thread_id = thread_id or str(uuid.uuid4())
            conversation = await async_get_conversation_by_thread_id(thread_id)
            if not conversation:
                title = user_input[:50] if len(user_input) <= 50 else f"{user_input[:50]}..."
                conversation = await async_create_conversation(
                    user_id=user_id,
                    thread_id=thread_id,
                    title=title,
                )
            else:
                if conversation.user_id != user_id:
                    raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "无权访问此对话")
            data = await _submit_new_task(
                conversation, thread_id, user_input, user_option, agent_type, files, user_id
            )
            return ResponseModel.success(data=data)
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"❌ Agent路由器API失败: {e}")
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            f"处理失败: {str(e)}"
        )


class MessageStreamRequest(BaseModel):
    """获取消息流请求"""
    run_id: str

async def _get_messages_stream_impl(
    run_id: str,
    user_id: str
):
    """获取消息流的实现逻辑（GET 和 POST 共用）- 持续监听直到任务完成 - 使用asyncpg
    同一 run_id 下按 message_id 去重，已下发过的 message_id 不再重复下发。
    同一 run_id 多连接由前端控制（前端同一 tab 内用 AbortController 只保留一个 stream）。
    """
    import json
    import asyncio
    from ...crud.conversation import async_get_conversation_run_by_run_id
    from ...services.redis.connection import get_redis_stream_service
    
    # 1. 验证权限（使用asyncpg CRUD）
    run = await async_get_conversation_run_by_run_id(run_id)
    if not run or run.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权访问此任务"
        )
    
    # 2. 获取 Redis 服务
    redis_service = await get_redis_stream_service()
    
    async def event_generator():
        seen_message_ids = set()  # 同一 run_id 下已下发的 DB message_id，去重
        try:
            logger.info(f"开始持续监听消息流: run_id={run_id}")
            
            # 3. 确定起始消息ID（从 last_generated_id 开始，如果没有则从0开始；状态先 Redis 再 DB）
            from ...crud.conversation import async_get_task_status_cached
            read_from_id = "0"
            task_status = await async_get_task_status_cached(run_id)
            if task_status and task_status.get('last_generated_id'):
                read_from_id = task_status.get('last_generated_id')
                logger.info(f"从 last_generated_id 开始: {read_from_id}")
            
            # 4. 持续监听新消息，直到任务完成
            max_wait_time = 600  # 最多等待10分钟，超时后客户端可以重新连接续上
            start_time = asyncio.get_event_loop().time()
            
            while True:
                # 检查超时
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > max_wait_time:
                    logger.info(f"消息流监听超时（10分钟）: run_id={run_id}, 已等待{elapsed:.0f}秒，客户端可以重新连接续上")
                    break
                
                # 检查任务状态，如果是终态则停止（先 Redis 再 DB）
                current_status = await async_get_task_status_cached(run_id)
                if current_status:
                    status = current_status.get('status')
                    if status in [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value, TaskStatus.INTERRUPTED.value]:
                        logger.info(f"任务已结束，停止监听: run_id={run_id}, status={status}")
                        break
                
                # 检查Redis Stream是否存在
                stream_exists = await redis_service.check_stream_exists(run_id)
                if not stream_exists:
                    logger.info(f"任务消息流已不存在（可能已完成并清理）: run_id={run_id}")
                    break
                
                batch = await redis_service.read_messages(run_id, read_from_id, count=100, block=1000)
                
                if batch:
                    logger.debug(f"读取到 {len(batch)} 条新消息: run_id={run_id}")
                    for msg_id, msg_data in batch:
                        # 同一 run_id 下按 DB message_id 去重：已下发过的不再下发
                        db_msg_id = msg_data.get("message_id")
                        if db_msg_id is not None and db_msg_id in seen_message_ids:
                            if msg_data.get("type") in GENERATED_EVENTS:
                                await redis_service.update_task_status(
                                    run_id,
                                    status=TaskStatus.RUNNING.value,
                                    last_generated_event=msg_data.get("type"),
                                    last_generated_id=msg_id,
                                    conversation_uuid=msg_data.get("conversation_uuid")
                                )
                            read_from_id = msg_id
                            continue
                        if db_msg_id is not None:
                            seen_message_ids.add(db_msg_id)
                        
                        cleaned_msg_data = {k: v for k, v in msg_data.items() if v is not None}
                        yield f"data: {json.dumps(cleaned_msg_data)}\n\n"
                        
                        event_type = msg_data.get("type", "")
                        if event_type in GENERATED_EVENTS:
                            await redis_service.update_task_status(
                                run_id,
                                status=TaskStatus.RUNNING.value,
                                last_generated_event=event_type,
                                last_generated_id=msg_id,
                                conversation_uuid=msg_data.get("conversation_uuid")
                            )
                        read_from_id = msg_id
                else:
                    await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"消息流生成器错误: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@router.get("/messages/{run_id}")
async def get_messages_stream_get(
    run_id: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """获取消息流（SSE）- GET 方法（持续监听直到任务完成）- 不再需要db"""
    return await _get_messages_stream_impl(run_id, user_id)

@router.post("/messages/stream", response_model=None)
async def get_messages_stream_post(
    request: MessageStreamRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """获取消息流（SSE）- POST 方法（持续监听直到任务完成）- 不再需要db"""
    return await _get_messages_stream_impl(request.run_id, user_id)


def compare_message_id(id1: str, id2: str) -> int:
    """比较Redis Stream消息ID"""
    if id1 == id2:
        return 0
    
    # Redis Stream ID格式: timestamp-sequence
    try:
        ts1, seq1 = id1.split("-")
        ts2, seq2 = id2.split("-")
        if ts1 != ts2:
            return 1 if int(ts1) > int(ts2) else -1
        return 1 if int(seq1) > int(seq2) else -1
    except:
        # 如果无法解析，默认id1更大
        return 1


class TaskStatusRequest(BaseModel):
    """获取任务状态请求"""
    run_id: str


class EstimateCreditsRequest(BaseModel):
    """积分预估请求：用户提交任务前，预估本次任务消耗积分。
    参数与 stream 提交一致的子集：user_option 必传，is_audio_driven + audio_duration_sec 可选。
    """
    user_option: UserOption
    is_audio_driven: bool = False
    audio_duration_sec: Optional[float] = None
    audio_segment_durations: Optional[List[float]] = None


@router.post("/estimate-credits", response_model=ResponseModel[Dict[str, Any]])
async def estimate_credits(
    request: EstimateCreditsRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """用户提交任务前，预估本次任务消耗的积分（仅 tool 成本）。
    返回 keyframe + video 全量预估，供前端在提交按钮旁展示。
    """
    from ...services.agent.video.cost_estimation import estimate_video_agent_credits
    result = estimate_video_agent_credits(
        user_option=request.user_option,
        is_audio_driven=request.is_audio_driven,
        audio_duration_sec=request.audio_duration_sec,
        audio_segment_durations=request.audio_segment_durations,
    )
    return ResponseModel.success(data=result.model_dump())


@router.get("/video-eligibility", response_model=ResponseModel[Dict[str, Any]])
async def get_video_eligibility(
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """视频资格：是否曾真实付费或订阅。供前端展示「需先充值」横幅，以 Go 为准查 paymenttransaction + subscriptions。通过后所有视频能力可用（含普通视频与口型视频，如 Wan 2.6 Flash、LTX 2.3 等）。"""
    from ...crud.video_eligibility import get_video_eligibility_async
    has_paid_once, err_msg = await get_video_eligibility_async(user_id)
    if err_msg:
        raise BusinessException(BusinessExceptionCode.INTERNAL_SERVER_ERROR, err_msg)
    return ResponseModel.success(data={"has_paid_once": has_paid_once})


@router.get("/options/capabilities", response_model=ResponseModel[Dict[str, Any]])
async def get_options_capabilities(
    image_tool: Optional[str] = Query(None, description="当前选中的图像生成工具，如 nano_banana_2 或 auto"),
    video_tool: Optional[str] = Query(None, description="当前选中的视频生成工具，如 pollo_seedance"),
    lipsync_tool: Optional[str] = Query(None, description="当前选中的口型视频工具，如 ltx_2_3、kling_v2_ai_avatar_pro、wan_2_2_speech_to_video、wan_2_6_flash 或 auto"),
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """按当前选中的 image_tool / video_tool / lipsync_tool 返回支持的 aspect_ratio、resolution、lipsync_video_tools 等，供前端展示或置灰。"""
    it = None
    if image_tool:
        try:
            it = ImageGenerationTool(image_tool)
        except ValueError:
            pass
    vt = None
    if video_tool:
        try:
            vt = VideoGenerationTool(video_tool)
        except ValueError:
            pass
    lt = None
    if lipsync_tool:
        try:
            lt = VideoGenerationTool(lipsync_tool)
        except ValueError:
            pass
    data = await get_tool_capabilities(image_tool=it, video_tool=vt, lipsync_tool=lt)
    return ResponseModel.success(data=data)


@router.post("/task/status", response_model=ResponseModel[Dict[str, Any]])
async def get_task_status(
    request: TaskStatusRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """获取任务状态（使用 run_id）。状态来源：先 Redis 再 DB，统一走 crud async_get_task_status_cached。"""
    from ...crud.conversation import async_get_conversation_run_by_run_id, async_get_task_status_cached

    run_id = request.run_id

    # 验证权限
    run = await async_get_conversation_run_by_run_id(run_id)
    if not run or run.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权访问此任务"
        )

    # 先 Redis 再 DB 的统一点（crud）
    task_status = await async_get_task_status_cached(run_id)
    if not task_status:
        task_status = {
            "run_id": run_id,
            "status": run.status,
            "created_at": utc_isoformat(run.created_at),
            "completed_at": utc_isoformat(run.completed_at),
            "error_message": run.error_message,
        }
    # 从 run 的 additional_data 带出 language 给前端
    run_lang = (run.additional_data or {}).get("language") if getattr(run, "additional_data", None) else None
    if run_lang is not None:
        task_status["language"] = run_lang
    # 终态：不再生成、不显示进度条；interrupted 为暂停等待用户点「继续」
    from ...models.task_status import TaskStatus
    terminal_statuses = (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value, TaskStatus.INTERRUPTED.value)
    task_status["is_terminal"] = (task_status.get("status") or "") in terminal_statuses
    return ResponseModel.success(data=task_status)


@router.post("/cancel", response_model=ResponseModel[CancelTaskResponse])
async def cancel_task(
    request: CancelRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """取消指定的任务（支持Worker模式）"""
    from ...services.redis.connection import get_redis_stream_service
    from ...crud.conversation import (
        async_get_runs_by_thread_id_and_status, 
        async_update_conversation_run_status,
        async_get_conversation_run_by_run_id
    )
    from ...models.task_status import TaskStatus
    
    thread_id = request.thread_id
    redis_service = await get_redis_stream_service()
    
    # 1. 从Redis获取该thread_id的所有运行中/队列中的任务
    running_tasks = await redis_service.get_running_tasks_by_thread_id(thread_id)
    
    # 2. 从DB获取该thread_id的所有运行中/队列中的任务（作为补充，确保不遗漏）
    from ...crud.conversation import async_get_runs_by_thread_id_and_status
    db_tasks = await async_get_runs_by_thread_id_and_status(
        thread_id=thread_id,
        statuses=[TaskStatus.RUNNING.value, TaskStatus.QUEUED.value, TaskStatus.RESUME_QUEUED.value]
    )
    
    # 3. 合并任务列表（去重）
    all_run_ids = set()
    for task in running_tasks:
        rid = task.get("run_id") if isinstance(task, dict) else getattr(task, "run_id", None)
        if rid:
            all_run_ids.add(rid)
    for task in db_tasks:
        rid = task.get("run_id") if isinstance(task, dict) else getattr(task, "run_id", None)
        if rid:
            all_run_ids.add(rid)
    
    if not all_run_ids:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            f"任务 {thread_id} 未找到或已完成"
        )
    
    # 4. 取消所有任务（先更新DB，再更新Redis，最后取消正在运行的任务）
    from ...main import get_task_worker
    
    cancelled_count = 0
    cancelled_at = datetime.utcnow()
    worker = get_task_worker()
    
    for run_id in all_run_ids:
        # 获取 run 对象以获取 conversation_uuid
        run = await async_get_conversation_run_by_run_id(run_id)
        conversation_uuid = run.conversation_uuid if run else None
        
        # 先更新DB状态（主数据源，确保持久化）
        await async_update_conversation_run_status(
            run_id=run_id,
            status=TaskStatus.CANCELLED.value,
            completed_at=cancelled_at
        )
        
        # 再更新Redis状态（缓存，用于快速查询）
        await redis_service.update_task_status(
            run_id,
            status=TaskStatus.CANCELLED.value,
            conversation_uuid=conversation_uuid,
            cancelled_at=utc_isoformat(cancelled_at)
        )
        
        # 发布Pub/Sub消息（跨机器/跨进程通知）
        await redis_service.publish_cancel(run_id)
        
        # 最后取消正在运行的任务（如果存在，本地worker）
        if worker:
            await worker.cancel_running_task(run_id)
        
        cancelled_count += 1
        logger.info(f"✅ 任务已取消: run_id={run_id}, thread_id={thread_id}")
    
    
    result = CancelTaskResponse(
        thread_id=thread_id,
        cancelled=True
    )
    return ResponseModel.success(
        data=result,
        message=f"已成功取消 {cancelled_count} 个任务"
    )


@router.post("/dismiss-interrupt-auto-resume", response_model=ResponseModel[DismissInterruptAutoResumeResponse])
async def dismiss_interrupt_auto_resume(
    request: DismissInterruptAutoResumeRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """用户关闭本次门闩的倒计时自动继续：前端停表 + Redis 标记，worker 收到 delayed auto_resume 时跳过 prepare_resume。
    不写新表字段：仅在既有 conversation_messages.event_data（JSON）内 merge 键，与 continued 等一致。"""
    from ...services.redis.connection import get_redis_stream_service

    conversation = await async_get_conversation_by_thread_id(request.thread_id)
    if not conversation or conversation.user_id != user_id:
        raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "无权访问此对话")

    run = await async_get_conversation_run_by_run_id(request.interrupted_run_id)
    if not run or run.user_id != user_id:
        raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "无权访问该任务")
    if str(run.thread_id) != str(request.thread_id):
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "run 与 thread_id 不匹配")
    if str(run.conversation_id) != str(conversation.id):
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "run 不属于该对话")

    if run.status != TaskStatus.INTERRUPTED.value:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER,
            "当前任务不在 interrupt 暂停态，无需取消自动继续",
        )

    row = await async_get_message_event_type_and_data_for_conversation(
        request.interrupt_msgid,
        int(conversation.id),
    )
    if not row:
        raise BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "未找到该消息")
    event_type, event_data = row
    if (event_type or "") != "interrupt":
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "该消息不是 interrupt 门闩")
    if event_data.get("continued"):
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER,
            "已继续，请使用任务取消等能力",
        )

    redis_service = await get_redis_stream_service()
    await redis_service.set_auto_resume_dismissed(
        request.interrupted_run_id,
        str(request.interrupt_msgid),
        ttl_seconds=120,
    )
    await async_update_message_event_data_partial(
        request.interrupt_msgid,
        {
            "auto_resume_dismissed": True,
            "auto_resume_dismissed_at": utc_isoformat(datetime.utcnow()),
        },
    )
    return ResponseModel.success(
        data=DismissInterruptAutoResumeResponse(ok=True),
        message="已关闭本次自动继续，仍可手动点击继续",
    )


@router.post("/running-tasks", response_model=ResponseModel[RunningTasksResponse])
async def get_running_tasks(
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """获取正在运行的任务列表（从Redis获取，使用 run_id）"""
    from ...services.redis.connection import get_redis_stream_service
    
    redis_service = await get_redis_stream_service()
    running_tasks = await redis_service.get_user_running_tasks(user_id)
    
    result = RunningTasksResponse(
        running_tasks=running_tasks,
        count=len(running_tasks)
    )
    return ResponseModel.success(data=result)


# ===== 视频编辑相关端点 =====

_ARTIFACT_SUGGEST_PRESET_PROMPT: Dict[str, tuple[PromptName, str]] = {
    "character": (
        PromptName.VIDEO_ARTIFACT_SUGGEST_PRESETS_CHARACTER,
        "suggest-presets-character-director",
    ),
    "keyframe": (
        PromptName.VIDEO_ARTIFACT_SUGGEST_PRESETS_KEYFRAME,
        "suggest-presets-keyframe-director",
    ),
    "video": (
        PromptName.VIDEO_ARTIFACT_SUGGEST_PRESETS_VIDEO,
        "suggest-presets-video-director",
    ),
}

_SUGGEST_PRESETS_CACHE_TTL_SEC = 120


def _suggest_presets_cache_key(
    *,
    locale: str,
    artifact_kind: str,
    prompt: str,
    image_url: Optional[str],
    video_url: Optional[str],
) -> str:
    body = f"{artifact_kind}\n{locale}\n{prompt or ''}\n{image_url or ''}\n{video_url or ''}"
    h = hashlib.sha256(body.encode("utf-8")).hexdigest()[:40]
    safe_locale = "".join(c if c.isalnum() or c in "-_" else "_" for c in (locale or "en")[:24])
    return f"vagent:suggest_prompt_presets:v1:{safe_locale}:{artifact_kind}:{h}"


@router.post("/video-editing/suggest-prompt-edit-presets")
async def suggest_prompt_edit_presets(
    request: SuggestPromptEditPresetsRequest,
    http_request: Request,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """
    为「编辑 Prompt / AI 融合」弹窗生成建议指令芯片（角色 / 关键帧 / 视频）。
    Craft in kit/skills suggest-presets-*-director；语言由 prompt_utils 注入；结果短 TTL 缓存。
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.orchestration.skills.prompt_context import (
        facts_human_message,
        skill_system_message,
    )

    await validate_thread_id(request.thread_id, user_id)
    locale = (
        http_request.headers.get("X-App-Language")
        or http_request.headers.get("X-Language")
        or "en"
    ).strip()
    excerpt = (request.prompt or "")[:16000]
    img = (request.image_url or "").strip() or None
    vid = (request.video_url or "").strip() or None
    cache_key = _suggest_presets_cache_key(
        locale=locale,
        artifact_kind=request.artifact_kind,
        prompt=excerpt,
        image_url=img,
        video_url=vid,
    )
    try:
        from ...services.redis.connection import get_redis_client

        rcli = await get_redis_client(decode_responses=True)
        cached_raw = await rcli.get(cache_key)
        if cached_raw:
            data = json.loads(cached_raw)
            if isinstance(data.get("presets"), list):
                return ResponseModel.success(data=data)
    except Exception:
        logger.debug("suggest_prompt_edit_presets cache read skipped", exc_info=True)

    prompt_name, skill_name = _ARTIFACT_SUGGEST_PRESET_PROMPT[request.artifact_kind]
    system = skill_system_message(
        skill_name,
        lead=f"Follow {skill_name}. Output structured presets only.",
    )
    kind_label = {
        "character": "角色主图 t2i_prompt",
        "keyframe": "关键帧 t2i_prompt",
        "video": "镜头视频 motion / I2V prompt",
    }.get(request.artifact_kind, request.artifact_kind)
    human_text = facts_human_message({
        "artifact_kind": request.artifact_kind,
        "kind_label": kind_label,
        "prompt": excerpt,
        "has_image": bool(img),
        "has_video": bool(vid and request.artifact_kind == "video"),
    })

    content: Any = human_text
    parts: List[Dict[str, Any]] = [{"type": "text", "text": human_text}]
    if img:
        parts.append({"type": "image_url", "image_url": {"url": img}})
    if vid and request.artifact_kind == "video":
        try:
            from app.utils.file_utils import prepare_video_for_llm
            vc = await prepare_video_for_llm(
                video_url=vid if "://" in vid else None,
                video_path=vid if "://" not in vid else None,
                mime_type="video/mp4",
                max_wait_time=180,
                max_base64_size_mb=20.0,
            )
            parts.append(vc.to_media_content())
        except Exception:
            logger.warning("suggest_presets: prepare_video_for_llm failed, text+image only", exc_info=True)
    if len(parts) > 1:
        content = parts
    messages = [SystemMessage(content=system), HumanMessage(content=content)]

    from ...services.agent.utils.llm_resilience import (
        StructuredResilienceKind,
        ainvoke_structured_resilient,
    )

    try:
        apply_language_suffix_to_system_message_in_messages(messages, locale)
        raw = await ainvoke_structured_resilient(
            prompt_entry=PROMPTS_CONFIG[prompt_name],
            kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
            structured_chat_messages=messages,
            include_raw=False,
            log_context={
                "caller": "suggest_prompt_edit_presets",
                "artifact_kind": request.artifact_kind,
                "thread_id": request.thread_id,
            },
        )
    except Exception as e:
        logger.exception("suggest_prompt_edit_presets LLM failed: %s", e)
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            f"生成建议指令失败: {str(e)}",
        )

    out = cast(PromptEditPresetsOutput, raw)
    presets = list(out.presets or [])[:8]
    data = {"presets": [p.model_dump() for p in presets]}
    try:
        from ...services.redis.connection import get_redis_client

        rcli = await get_redis_client(decode_responses=True)
        await rcli.setex(cache_key, _SUGGEST_PRESETS_CACHE_TTL_SEC, json.dumps(data, ensure_ascii=False))
    except Exception:
        logger.debug("suggest_prompt_edit_presets cache write skipped", exc_info=True)
    return ResponseModel.success(data=data)


@router.post("/video-editing/regenerate-keyframes")
async def regenerate_keyframes(
    request: RegenerateKeyframesRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    # ✅ 修复：不注入 db，让 Service 内部按需创建连接
    # 避免整个 regenerate 执行期间（2分钟）连接一直被占用
):
    """
    重新生成关键帧。与 Smart Testing 共用 execute_regenerate_keyframes（建 run、计费、调 service、回写 billing）。
    """
    run_id = str(uuid.uuid4())
    thread_id = request.thread_id
    await validate_thread_id(thread_id, user_id)
    logger.info(f"🎯 Regenerate Keyframes Request: keyframes_count={len(request.keyframes)}, user_option={request.user_option}, thread_id={thread_id}")

    from ...utils.credit_deduction_utils import check_credits_before_task
    check_ok, _, check_err = await check_credits_before_task(user_id)
    if not check_ok:
        raise BusinessException(BusinessExceptionCode.INSUFFICIENT_CREDITS, check_err)

    from ...services.task_enqueue_service import execute_regenerate_keyframes
    try:
        result = await execute_regenerate_keyframes(
            thread_id=thread_id or "",
            run_id=run_id,
            user_id=user_id,
            keyframes=request.keyframes,
            user_option=request.user_option,
            regenerate_source="panel",
        )
        return ResponseModel.success(data=result)
    except BusinessException:
        raise
    except Exception as e:
        logger.error("重新生成关键帧失败: %s", e)
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            f"重新生成关键帧失败: {str(e)}"
        )


@router.post("/video-editing/regenerate-videos")
async def regenerate_videos(
    request: RegenerateVideosRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    # ✅ 修复：不注入 db，让 Service 内部按需创建连接
):
    """
    重新生成视频片段。与 Smart Testing 共用 execute_regenerate_videos（建 run、计费、调 service、回写 billing）。
    """
    run_id = str(uuid.uuid4())
    thread_id = request.thread_id
    await validate_thread_id(thread_id, user_id)
    logger.info(f"🎯 Regenerate Videos Request: videos_count={len(request.videos)}, user_option={request.user_option}, thread_id={thread_id}")

    from ...utils.credit_deduction_utils import check_credits_before_task
    check_ok, _, check_err = await check_credits_before_task(user_id)
    if not check_ok:
        raise BusinessException(BusinessExceptionCode.INSUFFICIENT_CREDITS, check_err)

    from ...services.task_enqueue_service import execute_regenerate_videos
    try:
        result = await execute_regenerate_videos(
            thread_id=thread_id or "",
            run_id=run_id,
            user_id=user_id,
            videos=request.videos,
            user_option=request.user_option,
            regenerate_source="panel",
        )
        return ResponseModel.success(data=result)
    except BusinessException:
        raise
    except Exception as e:
        logger.error("重新生成视频片段失败: %s", e)
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            f"重新生成视频片段失败: {str(e)}"
        )


@router.post("/video-editing/regenerate-characters")
async def regenerate_characters(
    request: RegenerateCharactersRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    # ✅ 修复：不注入 db，让 Service 内部按需创建连接
):
    """
    重新生成角色。与 Smart Testing 共用 execute_regenerate_characters（建 run、计费、调 service、回写 billing）。
    """
    run_id = str(uuid.uuid4())
    thread_id = request.thread_id
    await validate_thread_id(thread_id, user_id)
    logger.info(f"🎯 Regenerate Characters Request: characters_count={len(request.characters)}, user_option={request.user_option}, thread_id={thread_id}")

    from ...utils.credit_deduction_utils import check_credits_before_task
    check_ok, _, check_err = await check_credits_before_task(user_id)
    if not check_ok:
        raise BusinessException(BusinessExceptionCode.INSUFFICIENT_CREDITS, check_err)

    from ...services.task_enqueue_service import execute_regenerate_characters
    try:
        result = await execute_regenerate_characters(
            thread_id=thread_id or "",
            run_id=run_id,
            user_id=user_id,
            characters=request.characters,
            user_option=request.user_option,
            regenerate_source="panel",
        )
        return ResponseModel.success(data=result)
    except BusinessException:
        raise
    except Exception as e:
        logger.error("重新生成角色失败: %s", e)
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            f"重新生成角色失败: {str(e)}"
        )


class PostRegenerateActionRequest(BaseModel):
    """post_regenerate：对话卡片上的继续编辑 / 同步下游。"""
    conversation_id: int = Field(..., description="conversations.id")
    message_id: int = Field(..., description="interaction_post_regenerate 消息的 id")
    action: str = Field(..., description="continue_edit | sync_downstream")


@router.post("/video-editing/post-regenerate-action")
async def video_editing_post_regenerate_action(
    request: PostRegenerateActionRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """校验 interaction 消息并执行 propagate（选用 + 下一层 regenerate）。"""
    from ...services.post_regenerate_action_service import run_post_regenerate_action

    data = await run_post_regenerate_action(
        user_id=user_id,
        conversation_id=request.conversation_id,
        message_id=request.message_id,
        action=request.action,
    )
    return ResponseModel.success(data=data)


@router.post("/video-editing/video-assembly")
async def video_assembly(
    request: VideoAssemblyRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """
    视频合成 - 按 thread 组装：使用该 thread 下当前全部资源（视频/旁白/音效/音乐等）合成最终视频。必传 thread_id。
    """
    try:
        thread_id = request.thread_id
        await validate_thread_id(thread_id, user_id)

        segment_versions = request.segment_versions or []
        logger.info(f"🎯 Video Assembly Request: thread_id={thread_id}, segment_versions_count={len(segment_versions)}, videos_count={len(request.videos or [])}, user_option={request.user_option}")
        
        from ...services.agent.video_agent_service import get_video_agent_service
        video_agent_service = get_video_agent_service()
        
        result = await video_agent_service.video_assembly_by_request(
            thread_id=thread_id,
            segment_versions=segment_versions,
            user_id=user_id,
            user_option=request.user_option,
            videos=request.videos
        )
        
        return ResponseModel.success(data=result)
        
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"视频合成失败: {e}")
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            f"视频合成失败: {str(e)}"
        )


@router.put("/music-editing/update-prompt/{music_generation_uuid}/{version_uuid}")
async def update_music_prompt(
    music_generation_uuid: str,
    version_uuid: str,
    request: MusicPromptUpdateRequest
):
    """
    更新音乐生成版本的提示词
    
    Args:
        music_generation_uuid: 音乐生成UUID
        version_uuid: 版本UUID
        request: 包含新提示词的请求体
    """
    try:
        from ...crud.video.video_audio import update_music_generation_version_prompt
        
        music_prompt = request.music_prompt.strip()
        
        # 更新数据库（crud/video 使用 asyncpg，无需 db）
        success = await update_music_generation_version_prompt(
            version_uuid, music_prompt
        )
        
        if not success:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                f"音乐版本不存在: {version_uuid}"
            )
        
        logger.info(f"✅ 音乐提示词更新成功: {version_uuid}")
        return ResponseModel.success(data={
            "music_generation_uuid": music_generation_uuid,
            "version_uuid": version_uuid,
            "music_prompt": music_prompt
        })
        
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"更新音乐提示词失败: {e}")
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            f"更新音乐提示词失败: {str(e)}"
        )


@router.post("/characters/{character_uuid}/select-version", response_model=ResponseModel[Dict[str, str]])
async def select_character_version(
    character_uuid: str,
    request: SelectCharacterVersionRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """设置角色选中的版本"""
    from ...crud.video.video_character import (
        get_character_by_uuid,
        get_character_version_by_uuid,
        update_character_selected_version
    )
    
    # 1. 验证角色存在且属于用户
    character = await get_character_by_uuid(character_uuid)
    if not character or character.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权限访问此角色"
        )
    
    # 2. 验证版本存在且属于该角色
    version = await get_character_version_by_uuid(request.version_uuid)
    if not version or version.video_character_id != character_uuid:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "角色版本不存在或不属于该角色"
        )
    
    # 3. 更新选中状态（crud/video 使用 asyncpg，无需 db）
    success = await update_character_selected_version(character_uuid, request.version_uuid)
    if not success:
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_ERROR,
            "更新选中状态失败"
        )
    
    return ResponseModel.success(data={
        "character_uuid": character_uuid,
        "selected_version_id": request.version_uuid
    })


@router.post("/keyframes/{keyframe_uuid}/select-version", response_model=ResponseModel[Dict[str, Any]])
async def select_keyframe_version(
    keyframe_uuid: str,
    request: SelectKeyframeVersionRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """设置关键帧选中的版本（持久化 current_version_index）"""
    from ...crud.video.video_keyframe import (
        get_keyframe_by_uuid,
        get_keyframe_versions_by_keyframe_ids,
        update_keyframe_current_version_index,
    )

    keyframe = await get_keyframe_by_uuid(keyframe_uuid)
    if not keyframe or keyframe.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权限访问此关键帧"
        )

    versions = await get_keyframe_versions_by_keyframe_ids([keyframe_uuid])
    versions_for_kf = sorted(
        [v for v in versions if v.keyframe_id == keyframe_uuid],
        key=lambda v: getattr(v, "version_number", 0),
    )
    version_index = next((i for i, v in enumerate(versions_for_kf) if v.uuid == request.version_uuid), None)
    if version_index is None:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "关键帧版本不存在或不属于该关键帧"
        )

    success = await update_keyframe_current_version_index(keyframe_uuid, version_index)
    if not success:
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_ERROR,
            "更新关键帧选中版本失败"
        )

    return ResponseModel.success(data={
        "keyframe_uuid": keyframe_uuid,
        "current_version_index": version_index,
        "version_uuid": request.version_uuid,
    })


@router.post("/videos/{video_uuid}/select-version", response_model=ResponseModel[Dict[str, Any]])
async def select_video_version(
    video_uuid: str,
    request: SelectVideoVersionRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """设置视频片段选中的版本（持久化 current_version_index）"""
    from ...crud.video.video_generation import (
        get_video_generation_by_uuid,
        get_video_generation_versions_by_video_generation_ids,
        batch_update_video_generation_current_version_index,
    )

    video = await get_video_generation_by_uuid(video_uuid)
    if not video or video.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权限访问此视频"
        )

    versions = await get_video_generation_versions_by_video_generation_ids([video_uuid])
    version_index = next((i for i, v in enumerate(versions) if v.uuid == request.version_uuid), None)
    if version_index is None:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "视频版本不存在或不属于该视频"
        )

    success = await batch_update_video_generation_current_version_index([
        {"uuid": video_uuid, "current_version_index": version_index}
    ])
    if not success:
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_ERROR,
            "更新视频选中版本失败"
        )

    return ResponseModel.success(data={
        "video_uuid": video_uuid,
        "current_version_index": version_index,
        "version_uuid": request.version_uuid,
    })

