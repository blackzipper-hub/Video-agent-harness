"""
音乐生成功能模块
负责音乐生成节点的实现和相关功能
"""
import logging
import json
import re
import time
from enum import Enum
from typing import List, Optional, Dict, Any, Union, cast, Tuple
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from datetime import datetime

from ....models.video_state import VideoAgentState, MusicVersion, GenerationConfig
from ....models.user_options import VideoGenerationTool, UserOption
from ....exceptions import BusinessException, BusinessExceptionCode
from ....services.agent.base_agent import MessageType
from ..schemas import VideoContextSchema
from .stage_failure import build_stage_failure, emit_stage_failure_event
from ....utils.error_classification import classify_failure
from ....services.tool_service import ToolService
from ....utils.i18n import get_i18n_message_async
from ..utils.message_utils import extract_ai_message_json
from ....services.agent.utils.prompt_utils import attach_images_to_messages
from ....crud.video.video_audio import (
    create_music_generation,
    create_music_generation_version,
    update_music_generation_additional_data,
)
from ....models.image_result import MusicProvider
from prompts.prompt_config import PROMPTS_CONFIG, PromptName
from ..utils.llm_resilience import StructuredResilienceKind, ainvoke_structured_resilient

logger = logging.getLogger(__name__)


# Suno 生成端落库到 S3 时的文件名固定为 `suno_<task_id>_clip_<idx>_<clip_id>.mp3`
# （见 app/llm/suno_service.py 中 generation_id 字段构造）。
# 这里严格只匹配 `_clip_<idx>_<UUID>`，避免误命中其他 URL。
_SUNO_CLIP_ID_FROM_FILENAME = re.compile(
    r"_clip_\d+_([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _recover_suno_clip_id_from_url(audio_url: Optional[str]) -> Optional[str]:
    """从 Suno 生成端的 S3 URL 文件名回解 clip_id（LLM Agent 偶发丢字段的兜底）。"""
    if not audio_url:
        return None
    m = _SUNO_CLIP_ID_FROM_FILENAME.search(audio_url)
    return m.group(1) if m else None


def _music_version_from_suno_result(
    structured_response: Any,
    *,
    target_duration: int,
    tool_duration_sec: float,
    ai_messages_json: Any = None,
) -> MusicVersion:
    """Build MusicVersion from programmatic Suno MusicGenerationResult."""
    from ....models.tool_enums import ToolProvider

    if structured_response.success and structured_response.clips:
        first_clip = structured_response.clips[0]
        lyrics = first_clip.lyrics
        is_instrumental = not lyrics or lyrics.strip() == "[Instrumental]"
        provider = structured_response.provider or ToolProvider.SUNO.value
        return MusicVersion(
            version_number=1,
            music_url=first_clip.audio_url,
            original_audio_url=first_clip.audio_url,
            music_prompt=structured_response.generated_prompt or "",
            provider=provider,
            duration=first_clip.duration,
            is_instrumental=is_instrumental,
            success=True,
            error_msg="",
            additional_data={
                "clips": [clip.model_dump() for clip in structured_response.clips],
                "clips_count": structured_response.clips_count,
                "task_id": structured_response.task_id,
                "tool_duration_sec": tool_duration_sec,
                "target_duration": target_duration,
            },
            created_at=datetime.now().isoformat(),
            ai_messages_json=ai_messages_json,
        )
    error_msg = structured_response.error or structured_response.message or "音乐生成失败"
    provider = structured_response.provider or ToolProvider.SUNO.value
    return MusicVersion(
        version_number=1,
        music_url="",
        original_audio_url="",
        music_prompt=structured_response.generated_prompt or "",
        provider=provider,
        duration=0.0,
        is_instrumental=True,
        success=False,
        error_msg=error_msg,
        created_at=datetime.now().isoformat(),
        ai_messages_json=ai_messages_json,
    )


async def _generate_bgm_prompt_and_call_suno(
    *,
    user_input: str,
    target_duration: int,
    images: Optional[List] = None,
    detected_language: Optional[str] = None,
) -> tuple[List[BaseMessage], MusicVersion]:
    """BGM: deep-agent crafts suno_prompt package; Suno tool runs programmatically."""
    import uuid as _uuid
    from app.services.agent.video.music_stage import (
        export_music_bgm_inputs,
        generate_music_bgm_prompt_via_deep_agent,
    )
    from app.tools.music.suno import _generate_music_with_suno_impl

    tid = f"bgm_{_uuid.uuid4().hex[:8]}"
    rid = f"run_{_uuid.uuid4().hex[:8]}"
    image_urls = [image.url for image in images] if images else []
    paths = export_music_bgm_inputs(
        thread_id=tid,
        run_id=rid,
        user_input=user_input or "",
        target_duration=float(target_duration),
        image_urls=image_urls,
    )
    art, da_msgs = await generate_music_bgm_prompt_via_deep_agent(
        thread_id=tid,
        run_id=rid,
        input_paths=paths,
        detected_language=detected_language,
    )
    logger.info(
        "🎵 BGM prompt (deep-agent): suno_prompt_len=%s tags=%r",
        len(art.suno_prompt or ""),
        art.tags,
    )
    _music_t0 = time.perf_counter()
    structured_response = await _generate_music_with_suno_impl(
        prompt=art.suno_prompt,
        has_lyrics=False,
        tags=(art.tags or None),
        target_duration=target_duration,
    )
    tool_duration_sec = round(time.perf_counter() - _music_t0, 3)
    logger.info("🎵 [music_timing] bgm suno tool_duration_sec=%.3f", tool_duration_sec)
    music_version = _music_version_from_suno_result(
        structured_response,
        target_duration=target_duration,
        tool_duration_sec=tool_duration_sec,
    )
    if music_version.success and not music_version.music_prompt:
        music_version = music_version.model_copy(update={"music_prompt": art.suno_prompt})
    return list(da_msgs or []), music_version


def determine_generation_config(
    has_audio: bool, 
    user_option: Optional[UserOption]
) -> GenerationConfig:
    """根据音频状态和用户选项决定内容生成配置
    
    规则：
    1. Audio-driven模式（有音频）：不需要生成音效和旁白（用户已上传音频）
    2. Sora2/Sora2Pro视频工具：不需要生成BGM、音效和旁白（视频自带声音）
    3. 其他情况：正常生成所有内容
    
    Args:
        has_audio: 是否有音频输入
        user_option: 用户选项配置
        
    Returns:
        GenerationConfig: 生成配置
    """
    # 情况1: Audio-driven模式
    if has_audio:
        config = GenerationConfig.for_audio_driven()
        logger.info(f"🎵 {config.reason}")
        return config
    
    # 情况2: Sora2/Sora2Pro视频工具（视频自带声音）
    if user_option:
        video_tool = user_option.video_generation_tool
        if video_tool in [VideoGenerationTool.OPENAI_SORA, VideoGenerationTool.OPENAI_SORA_PRO]:
            config = GenerationConfig.for_video_with_sound()
            logger.info(f"🎬 {config.reason}")
            return config
        from ....models.tool_enums import ContentCategory
        if user_option.content_category == ContentCategory.SHORT_DRAMA:
            config = GenerationConfig.for_short_drama()
            logger.info(f"🎭 {config.reason}")
            return config
    
    # 情况3: 默认模式
    config = GenerationConfig.default()
    logger.info(f"✅ {config.reason}")
    return config


class MusicIntentType(str, Enum):
    """无音频时，用户希望如何生成音乐（仅当未上传音频时使用）"""
    LYRICS_PROVIDED = "lyrics_provided"       # 用户提供了歌词/要唱的内容 → 按歌词+时长生成歌曲，再转录
    AUTO_LYRICS_SONG = "auto_lyrics_song"    # 用户只描述要什么歌，未提供歌词 → 用 auto lyrics + 时长生成歌曲，再转录
    INSTRUMENTAL_BGM = "instrumental_bgm"    # 用户只要 BGM，或上述失败时的兜底 → 视频为主，音乐为辅，并行生成纯音乐


class MusicIntentAnalysis(BaseModel):
    """音乐意图分析结果"""
    intent: MusicIntentType = Field(description="音乐生成意图：lyrics_provided | auto_lyrics_song | instrumental_bgm")
    reasoning: str = Field(description="判断理由")


async def analyze_music_intent(user_input: str, history_messages: Optional[List[BaseMessage]] = None) -> Tuple[MusicIntentType, List[BaseMessage]]:
    """判断用户无音频时的音乐生成意图（三选一）
    
    Args:
        user_input: 用户输入
        history_messages: 历史对话消息（可选，仅用于上下文，不包含在返回的messages中）
        
    Returns:
        Tuple[MusicIntentType, List[BaseMessage]]: (intent, messages)
            - intent: lyrics_provided | auto_lyrics_song | instrumental_bgm
            - messages: 本次LLM调用的input messages + output messages（不包含history_messages）
    """
    from app.config import settings as app_settings
    import uuid as _uuid
    from app.services.agent.video.music_stage import (
        export_music_intent_inputs,
        generate_music_intent_via_deep_agent,
    )
    tid = f"music_{_uuid.uuid4().hex[:8]}"
    rid = f"run_{_uuid.uuid4().hex[:8]}"
    history_summary = ""
    if history_messages:
        from app.services.agent.utils.prompt_utils import llm_chunk_content_to_text

        parts = []
        for m in history_messages[-12:]:
            role = getattr(m, "type", None) or m.__class__.__name__
            text = llm_chunk_content_to_text(getattr(m, "content", None)).strip()
            if text:
                parts.append(f"{role}: {text[:500]}")
        history_summary = "\n".join(parts)
    paths = export_music_intent_inputs(
        thread_id=tid, run_id=rid, user_input=user_input or "", history_summary=history_summary,
    )
    art, msgs = await generate_music_intent_via_deep_agent(
        thread_id=tid, run_id=rid, input_paths=paths,
    )
    intent = MusicIntentType(art.intent)
    logger.info(f"🎵 音乐意图(deep-agent): intent={intent.value}, 理由: {art.reasoning}")
    return intent, list(msgs or [])


def _workflow_path_entry(step_id: str) -> Dict[str, str]:
    return {"id": step_id, "label_key": f"workflow.node.{step_id}"}


# shot 视觉管线：与 music_workflow_mode 同款——path 对齐前端，节点内 early-skip 真正跳过执行
SHOT_WORKFLOW_KEYFRAME_I2V = "keyframe_i2v"
SHOT_WORKFLOW_REFERENCE_T2V = "reference_t2v"


_WORKFLOW_TAIL_AFTER_ANALYSIS: List[Dict[str, str]] = [
    _workflow_path_entry("story_style"),
    _workflow_path_entry("visual"),
    _workflow_path_entry("scenes"),
    _workflow_path_entry("storyboards"),
    _workflow_path_entry("shots"),
    _workflow_path_entry("final"),
]


def resolve_shot_workflow_mode(user_option: Optional[UserOption]) -> str:
    """按有效视频工具决定 shot 管线：Seedance2 → reference_t2v（无 keyframe）。"""
    from ....models.user_options import should_skip_keyframe_pipeline

    if should_skip_keyframe_pipeline(user_option):
        return SHOT_WORKFLOW_REFERENCE_T2V
    return SHOT_WORKFLOW_KEYFRAME_I2V


def strip_storyboards_from_workflow_path(
    path: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """reference_t2v 时从底部进度 path 去掉 storyboards（关键帧步）。"""
    return [e for e in path if not (isinstance(e, dict) and e.get("id") == "storyboards")]


def apply_shot_workflow_to_path(
    path: List[Dict[str, str]],
    shot_mode: str,
) -> List[Dict[str, str]]:
    if shot_mode == SHOT_WORKFLOW_REFERENCE_T2V:
        return strip_storyboards_from_workflow_path(path)
    return path


def should_skip_keyframe_pipeline_from_state(state: VideoAgentState) -> bool:
    """与 should_skip_gate_after_music 同款：优先读 state.shot_workflow_mode，缺省回退 user_option。"""
    mode = (state.get("shot_workflow_mode") or "").strip()
    if mode == SHOT_WORKFLOW_REFERENCE_T2V:
        return True
    if mode == SHOT_WORKFLOW_KEYFRAME_I2V:
        return False
    user_input_data = state.get("user_input_data")
    user_option = user_input_data.user_option if user_input_data else None
    from ....models.user_options import should_skip_keyframe_pipeline

    return should_skip_keyframe_pipeline(user_option)


def _is_product_launch(user_option: Optional[UserOption]) -> bool:
    if not user_option or not user_option.content_category:
        return False
    from ....models.tool_enums import ContentCategory
    return user_option.content_category == ContentCategory.PRODUCT_LAUNCH


def _is_short_drama(user_option: Optional[UserOption]) -> bool:
    if not user_option or not user_option.content_category:
        return False
    from ....models.tool_enums import ContentCategory
    return user_option.content_category == ContentCategory.SHORT_DRAMA


def build_product_launch_workflow_path() -> Tuple[List[Dict[str, str]], str]:
    """Product Launch：无 music 步；path 顺序为 场景→旁白→分镜→镜头。

    执行上 storyboard_detail 写旁白稿 → gate 后 narration TTS 与 keyframe 分镜**并行**（见 graph）。
    path 顺序表达内容阶段；TTS 与出图可同时跑以省时。
    """
    analysis = _workflow_path_entry("analysis")
    tail = [
        _workflow_path_entry("story_style"),
        _workflow_path_entry("visual"),
        _workflow_path_entry("scenes"),
        _workflow_path_entry("narration"),
        _workflow_path_entry("storyboards"),
        _workflow_path_entry("shots"),
        _workflow_path_entry("final"),
    ]
    return [analysis, *tail], "product_launch"


def build_video_workflow_path(
    *,
    has_audio: bool,
    music_intent: Optional[MusicIntentType],
    include_music: bool,
) -> Tuple[List[Dict[str, str]], str]:
    """按 run 上下文生成底部进度 path 与 music_workflow_mode（与 LangGraph 主链路对齐）。"""
    analysis = _workflow_path_entry("analysis")
    music = _workflow_path_entry("music")
    tail = [analysis, *_WORKFLOW_TAIL_AFTER_ANALYSIS]

    if not include_music:
        return tail, "none_sora"
    if has_audio:
        return [music, *tail], "user_upload"
    if music_intent == MusicIntentType.INSTRUMENTAL_BGM:
        return [
            analysis,
            _workflow_path_entry("story_style"),
            _workflow_path_entry("visual"),
            _workflow_path_entry("scenes"),
            music,
            _workflow_path_entry("storyboards"),
            _workflow_path_entry("shots"),
            _workflow_path_entry("final"),
        ], "bgm_parallel"
    return [music, *tail], "suno_then_transcribe"


def _is_sora_video_tool(user_option: Optional[UserOption]) -> bool:
    if not user_option:
        return False
    tool = user_option.video_generation_tool
    return tool in (VideoGenerationTool.OPENAI_SORA, VideoGenerationTool.OPENAI_SORA_PRO)


def _effective_user_input_data(
    state: VideoAgentState,
    analysis_result: Optional[Dict[str, Any]],
):
    if isinstance(analysis_result, dict) and analysis_result.get("user_input_data") is not None:
        return analysis_result["user_input_data"]
    return state.get("user_input_data")


def music_intent_from_state(state: VideoAgentState) -> Optional[MusicIntentType]:
    raw = state.get("music_intent")
    if not raw:
        return None
    try:
        return MusicIntentType(str(raw))
    except ValueError:
        logger.warning("无效 music_intent=%s，将忽略", raw)
        return None


def _finalize_workflow_update(
    out: Dict[str, Any],
    *,
    user_input_data: Any,
    user_option: Optional[UserOption],
) -> Dict[str, Any]:
    """统一附加 shot_workflow_mode / 调整 path / Short Drama 默认视频工具。"""
    from ....models.user_options import apply_short_drama_default_video_tool

    new_uo = apply_short_drama_default_video_tool(user_option)
    if new_uo is not None and new_uo is not user_option and user_input_data is not None:
        user_input_data = user_input_data.model_copy(update={"user_option": new_uo})
        out["user_input_data"] = user_input_data
        user_option = new_uo
        logger.info(
            "🧭 Short Drama 默认视频工具 → %s",
            getattr(new_uo.video_generation_tool, "value", new_uo.video_generation_tool),
        )

    shot_mode = resolve_shot_workflow_mode(user_option)
    path = out.get("workflow_path") or []
    if isinstance(path, list):
        out["workflow_path"] = apply_shot_workflow_to_path(path, shot_mode)
    out["shot_workflow_mode"] = shot_mode
    logger.info(
        "🧭 shot_workflow_mode=%s path_ids=%s",
        shot_mode,
        [e.get("id") for e in (out.get("workflow_path") or []) if isinstance(e, dict)],
    )
    return out


async def resolve_video_workflow_at_user_input(
    state: VideoAgentState,
    analysis_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """user_input_analysis 结束后：前移音乐意图分析并生成 workflow path（供 workflow_state 与 music_generation 复用）。"""
    user_input_data = _effective_user_input_data(state, analysis_result)
    audio_files = list(user_input_data.audio_files or []) if user_input_data else []
    user_option = user_input_data.user_option if user_input_data else None
    user_input = (user_input_data.user_input or "") if user_input_data else ""

    if _is_product_launch(user_option):
        from ....models.tool_enums import DefaultValues
        from ....models.video_state import GenerationConfig
        path, mode = build_product_launch_workflow_path()
        target_duration = user_option.duration if user_option else DefaultValues.VIDEO_DURATION
        logger.info("🧭 workflow: Product Launch，path 不含 music (mode=%s)", mode)
        return _finalize_workflow_update(
            {
                "music_intent": None,
                "music_workflow_mode": mode,
                "workflow_path": path,
                "generation_config": GenerationConfig.for_product_launch(),
                "actual_target_duration": target_duration,
            },
            user_input_data=user_input_data,
            user_option=user_option,
        )

    if _is_short_drama(user_option):
        from ....models.tool_enums import DefaultValues
        from ....models.video_state import GenerationConfig
        # 与 Product Launch 同 path（无 music 步），但声画合同不同：片内对白 + 稀疏旁白
        path, mode = build_product_launch_workflow_path()
        target_duration = user_option.duration if user_option else DefaultValues.VIDEO_DURATION
        logger.info("🧭 workflow: Short Drama，path 不含 music (mode=%s)", mode)
        return _finalize_workflow_update(
            {
                "music_intent": None,
                "music_workflow_mode": "short_drama",
                "workflow_path": path,
                "generation_config": GenerationConfig.for_short_drama(),
                "actual_target_duration": target_duration,
            },
            user_input_data=user_input_data,
            user_option=user_option,
        )

    if _is_sora_video_tool(user_option):
        path, mode = build_video_workflow_path(
            has_audio=bool(audio_files),
            music_intent=None,
            include_music=False,
        )
        logger.info("🧭 workflow: Sora 视频自带声音，path 不含 music (mode=%s)", mode)
        return _finalize_workflow_update(
            {
                "music_intent": None,
                "music_workflow_mode": mode,
                "workflow_path": path,
            },
            user_input_data=user_input_data,
            user_option=user_option,
        )

    if audio_files:
        path, mode = build_video_workflow_path(
            has_audio=True,
            music_intent=None,
            include_music=True,
        )
        logger.info(
            "🧭 workflow: 用户已提供音频 %d 个，music 在 analysis 前 (mode=%s)",
            len(audio_files),
            mode,
        )
        return _finalize_workflow_update(
            {
                "music_intent": None,
                "music_workflow_mode": mode,
                "workflow_path": path,
            },
            user_input_data=user_input_data,
            user_option=user_option,
        )

    intent, intent_messages = await analyze_music_intent(
        user_input,
        history_messages=state.get("messages", []),
    )
    path, mode = build_video_workflow_path(
        has_audio=False,
        music_intent=intent,
        include_music=True,
    )
    logger.info(
        "🧭 workflow: 无上传音频，意图=%s → path len=%d (mode=%s)",
        intent.value,
        len(path),
        mode,
    )
    return _finalize_workflow_update(
        {
            "music_intent": intent.value,
            "music_workflow_mode": mode,
            "workflow_path": path,
            "messages": intent_messages,
        },
        user_input_data=user_input_data,
        user_option=user_option,
    )


def should_skip_gate_after_music(state: VideoAgentState) -> bool:
    """与 workflow path 对齐：无配乐或 BGM 并行（音乐在场景后生成）或 Product Launch / Short Drama 时不弹 after_music 门控。"""
    mode = (state.get("music_workflow_mode") or "").strip()
    return mode in ("none_sora", "bgm_parallel", "product_launch", "short_drama")


def build_gate_after_music_interrupt_payload(
    state: VideoAgentState,
    *,
    credit_estimate: Dict[str, Any],
    smart_clip_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """after_music 门控文案与 workflow path 的 music_workflow_mode 对齐（图仍在 music_generation 后暂停）。"""
    mode = (state.get("music_workflow_mode") or "").strip() or "suno_then_transcribe"
    if mode == "bgm_parallel":
        message_key = "video.pause.after_music_bgm_parallel"
        message_default = "将开始视频策划，背景音乐将在场景生成后自动添加。是否确认继续？"
        paused_intent_key = "interrupt.pausedIntent.after_music_bgm_parallel"
    elif mode == "user_upload":
        message_key = "video.pause.after_music_user_upload"
        message_default = "上传音频已就绪，是否确认继续？"
        paused_intent_key = "interrupt.pausedIntent.after_music_user_upload"
    else:
        message_key = "video.pause.after_music"
        message_default = "音乐已生成，是否确认继续？"
        paused_intent_key = "interrupt.pausedIntent.after_music"
    payload: Dict[str, Any] = {
        "step": "after_music",
        "message_key": message_key,
        "message_default": message_default,
        "paused_intent_key": paused_intent_key,
        "music_mode": mode,
        "music_intent": state.get("music_intent"),
        "credit_estimate": credit_estimate,
    }
    if smart_clip_payload is not None:
        payload["smart_clip"] = smart_clip_payload
    return payload


def build_gate_after_bgm_ready_interrupt_payload(
    state: VideoAgentState,
    *,
    credit_estimate: Dict[str, Any],
) -> Dict[str, Any]:
    """BGM 并行：场景后 Suno BGM 已生成，在关键帧门控处试听/确认（非 after_music 规划文案）。"""
    return {
        "step": "after_keyframe_reflection",
        "message_key": "video.pause.after_music",
        "message_default": "背景音乐已生成，可以试听后决定是否继续，也可以裁切到合适的片段后再生成镜头视频。是否确认继续？",
        "paused_intent_key": "interrupt.pausedIntent.after_music",
        "music_mode": "bgm_parallel",
        "music_intent": state.get("music_intent"),
        "credit_estimate": credit_estimate,
    }


async def build_prompt_for_music_generation(
    user_input: str,
    target_duration: int,
    needs_lyrics: bool,
    prioritize_duration: bool = True,
    images: Optional[List] = None,
    history_messages: Optional[List[BaseMessage]] = None,
    use_auto_lyrics: bool = False,
    design_lyrics: bool = False,
    detected_language: Optional[str] = None,
) -> Tuple[List[BaseMessage], PromptName]:
    """构建音乐生成的提示词（前置阶段，无 story_outline）
    
    Args:
        user_input: 用户输入
        target_duration: 目标时长
        needs_lyrics: 是否走带歌词的歌曲路线（True=歌词 custom 路线，False=纯音乐BGM）
        prioritize_duration: 时长优先模式（True=时长优先调整歌词，False=歌词优先不调整）
        images: 参考图片列表（可选），用于理解用户意图和风格
        history_messages: 历史对话消息（可选），用于多轮对话上下文
        use_auto_lyrics: 【已废弃】旧的 Suno 自动写词路线；为兼容旧调用，等价于 design_lyrics
        design_lyrics: 用户未提供歌词时，由 LLM 按描述+时长设计歌词（仍走歌词 custom 路线，以字数控时长）
        detected_language: 检测到的界面/对话语言码（如 zh、en），传入模板由 LLM 自行组织「目标演唱语言」表述；不传则模板不展示该块
    
    Returns:
        Tuple[List[BaseMessage], PromptName]: (messages, 对应 PROMPTS_CONFIG 条目)
    
    重要：
    - 纯音乐BGM：使用英文描述（Suno API对英文支持更好）
    - 歌词创作：保持用户输入的语言一致；用户未给歌词时由 LLM 设计（design_lyrics）
    - 图片参考：如有提供，可从图片中获取风格、情绪等灵感
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    from app.orchestration.skills.prompt_context import (
        facts_human_message,
        skill_system_message,
    )
    from ....services.agent.utils.prompt_utils import attach_images_to_messages

    _dl = (detected_language or "").strip()

    # use_auto_lyrics 已废弃：旧调用语义即「用户未提供歌词，需由 LLM 设计歌词」，并入 design_lyrics 走歌词路线
    design_lyrics = design_lyrics or use_auto_lyrics

    # 二选一：带歌词的歌曲（用户提供 or LLM 设计） | 纯BGM
    if needs_lyrics or design_lyrics:
        _music_prompt_key = PromptName.VIDEO_MUSIC_GENERATION

        system = skill_system_message(
            "music-lyrics-director",
            lead="Follow music-lyrics-director.",
        )
        facts = {
            "music_type": "lyrics_song",
            "target_duration": target_duration,
            "user_input": user_input,
            "design_lyrics": bool(design_lyrics),
            "prioritize_duration": bool(prioritize_duration),
            "detected_language": _dl,
            "has_detected_language": bool(_dl),
            "reference_image_count": len(images) if images else 0,
            "duration_bucket": (
                "very_short" if target_duration < 30
                else "short" if target_duration < 90
                else "standard" if target_duration < 180
                else "long"
            ),
            "is_very_short": target_duration < 30,
            "is_short": 30 <= target_duration < 90,
            "is_standard": 90 <= target_duration < 180,
            "is_long": target_duration >= 180,
        }
        prompt_messages: List[BaseMessage] = [
            SystemMessage(content=system),
            HumanMessage(content=facts_human_message(facts)),
        ]
    else:
        raise ValueError(
            "BGM prompt craft uses music_bgm deep-agent; call _generate_bgm_prompt_and_call_suno instead"
        )
    
    # 不追加全局 language_suffix
    # 如有图片，使用 attach_images_to_messages 添加
    if images:
        image_urls = [image.url for image in images]
        prompt_messages = attach_images_to_messages(prompt_messages, image_urls)
    
    # 如果有历史消息，合并（历史消息在前，新prompt在后）
    if history_messages:
        prompt_messages = history_messages + prompt_messages
    
    return prompt_messages, _music_prompt_key


async def generate_single_suno_music(
    user_input: str,
    target_duration: int,
    needs_lyrics: bool,
    prioritize_duration: bool = True,
    images: Optional[List] = None,
    history_messages: Optional[List[BaseMessage]] = None,
    send_event_func: Optional[Any] = None,
    conversation_id: Optional[str] = None,
    use_auto_lyrics: bool = False,
    design_lyrics: bool = False,
    detected_language: Optional[str] = None,
) -> tuple[List[BaseMessage], Any]:
    """生成单个Suno音乐的独立方法（前置阶段，无 story_outline）
    
    Args:
        user_input: 用户输入（歌词或音乐描述）
        target_duration: 目标时长（秒）
        needs_lyrics: 是否需要用户提供的歌词（True=用户歌词歌曲，False=BGM 或 auto lyrics）
        prioritize_duration: 是否优先匹配时长（True=调整歌词匹配时长，False=保持歌词不变）
        images: 参考图片列表（可选）
        history_messages: 历史对话消息（可选）
        send_event_func: 发送事件函数（可选）
        conversation_id: 会话ID（可选）
        use_auto_lyrics: 【已废弃】旧的 Suno 自动写词路线，保留参数仅为兼容旧调用，不再选用专用模板
        design_lyrics: 用户未提供歌词时，由 LLM 按描述+时长设计歌词，再走歌词 custom 路线（以歌词字数控时长）
        detected_language: 语言码传入音乐生成模板，由 LLM 组织目标演唱/歌词语言表述，见 build_prompt_for_music_generation
        
    Returns:
        (messages, music_version): 消息列表和音乐版本对象
    """
    all_messages = []
    ai_messages_json = None
    try:
        logger.info(f"🎵 开始Suno音乐生成（前置阶段）...")

        # BGM：deep-agent 写 suno_prompt 包，Suno 走 Program 直调
        if not needs_lyrics and not design_lyrics:
            if send_event_func and conversation_id:
                await send_event_func(
                    event_type=MessageType.MUSIC_GENERATION_PROGRESS,
                    conversation_id=conversation_id,
                )
            return await _generate_bgm_prompt_and_call_suno(
                user_input=user_input,
                target_duration=target_duration,
                images=images,
                detected_language=detected_language,
            )
        
        # 发送音乐生成进度事件
        if send_event_func and conversation_id:
            await send_event_func(
                event_type=MessageType.MUSIC_GENERATION_PROGRESS,
                conversation_id=conversation_id
            )
        
        # 获取音乐生成工具
        music_tools_info = ToolService.get_music_generation_tools()
        music_tools = music_tools_info.tool_objects  # 获取工具对象列表
        
        from ....models.image_result import MusicGenerationResult
        from prompts.prompt_config import PROMPTS_CONFIG
        from ....services.agent.utils.llm_resilience import (
            StructuredResilienceKind,
            ainvoke_structured_resilient,
        )

        # 构建音乐生成提示词（包含图片和历史消息）
        music_prompt_messages, music_prompt_key = await build_prompt_for_music_generation(
            user_input=user_input,
            target_duration=target_duration,
            needs_lyrics=needs_lyrics,
            prioritize_duration=prioritize_duration,
            images=images,
            history_messages=history_messages,
            use_auto_lyrics=use_auto_lyrics,
            design_lyrics=design_lyrics,
            detected_language=detected_language,
        )
        logger.info(f"🎵 音乐创作走 llm_resilience（{music_prompt_key.value}）")

        # 配置递归限制，防止工具无限循环调用
        from langchain_core.runnables import RunnableConfig
        config = RunnableConfig(recursion_limit=11)

        inputs = {"messages": music_prompt_messages}
        _music_t0 = time.perf_counter()
        result = await ainvoke_structured_resilient(
            kind=StructuredResilienceKind.CREATE_AGENT,
            prompt_entry=PROMPTS_CONFIG[music_prompt_key],
            agent_inputs=inputs,
            agent_tools=music_tools,
            wrap_agent_parse_fallback=True,
            llm_invoke_config=config,
            log_context={"phase": "suno_music_generation"},
        )
        # 音乐工具墙钟（写入 additional_data，无需新增 DB 列；后续聚合 p50 校准 timing_profile）
        music_tool_duration_sec = round(time.perf_counter() - _music_t0, 3)
        logger.info("🎵 [music_timing] suno tool_duration_sec=%.3f target_duration=%s", music_tool_duration_sec, target_duration)
        
        # 获取结构化响应
        structured_response : MusicGenerationResult = cast(MusicGenerationResult, result.get("structured_response"))
        all_messages = result.get("messages", []) if result.get("messages") else []

        # 保存AIMessage（包含工具调用参数）
        ai_messages_json = extract_ai_message_json(result)
        
        # 创建音乐版本对象
        if structured_response.success and structured_response.clips:
            # 成功生成，使用第一个 clip 的信息
            first_clip = structured_response.clips[0]
            
            # 判断是否纯音乐
            lyrics = first_clip.lyrics
            is_instrumental = not lyrics or lyrics.strip() == "[Instrumental]"
            
            # 使用 ToolProvider enum 作为默认值
            from ....models.tool_enums import ToolProvider
            provider = structured_response.provider or ToolProvider.SUNO.value
            
            return all_messages, MusicVersion(
                version_number=1,
                music_url=first_clip.audio_url,
                original_audio_url=first_clip.audio_url,
                music_prompt=structured_response.generated_prompt or "",
                provider=provider,
                duration=first_clip.duration,  # 使用第一个 clip 的时长
                is_instrumental=is_instrumental,
                success=True,
                error_msg="",
                additional_data={
                    "clips": [clip.model_dump() for clip in structured_response.clips],
                    "clips_count": structured_response.clips_count,
                    "task_id": structured_response.task_id,
                    "tool_duration_sec": music_tool_duration_sec,
                    "target_duration": target_duration,
                },
                created_at=datetime.now().isoformat(),
                ai_messages_json=ai_messages_json
            )
        else:
            error_msg = structured_response.error or structured_response.message or "音乐生成失败"
            logger.warning(f"🎵 音乐生成失败: {error_msg}")
            # 使用 ToolProvider enum 作为默认值
            from ....models.tool_enums import ToolProvider
            provider = structured_response.provider or ToolProvider.SUNO.value
            
            return all_messages, MusicVersion(
                version_number=1,
                music_url="",
                original_audio_url="",
                music_prompt=structured_response.generated_prompt or "",
                provider=provider,
                duration=0.0,
                is_instrumental=True,
                success=False,
                error_msg=error_msg,
                created_at=datetime.now().isoformat(),
                ai_messages_json=ai_messages_json
            )
        
    except Exception as e:
        logger.error(f"❌ Suno音乐生成异常: {e}")
        # 返回失败的音乐版本对象
        # 使用 ToolProvider enum 作为默认值
        from ....models.tool_enums import ToolProvider
        
        return all_messages, MusicVersion(
            version_number=1,
            music_url="",
            original_audio_url="",
            music_prompt="",
            provider=ToolProvider.SUNO.value,
            duration=0.0,
            is_instrumental=True,
            success=False,
            error_msg=f"音乐生成异常: {str(e)}",
            created_at=datetime.now().isoformat(),
            ai_messages_json=ai_messages_json
        )


async def _notify_and_raise_music_failure(send_event_func: Any, music_version: Any, detected_language: Optional[str], conversation_id: Optional[Any] = None) -> None:
    """音乐生成失败：发 GENERATION_FAILED 对话事件（官方原因，不泄露内部信息）并抛出干净的 BusinessException。"""
    category = classify_failure(getattr(music_version, "error_msg", None))
    failure = build_stage_failure(
        "music",
        total=1,
        failed_items=[{"index": None, "category": category.value}],
        lang=detected_language,
    )
    await emit_stage_failure_event(send_event_func, failure, conversation_id=conversation_id)
    raise BusinessException(BusinessExceptionCode.BUSINESS_ERROR, failure["message"])


async def music_generation_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any
) -> Union[VideoAgentState, Dict[str, Any]]:
    """音乐生成节点（前置版本：处理 transcribe + 音乐生成）
    
    简化逻辑：
    1. 如果没有音频，判断是否需要生成音乐（歌词/BGM）
    2. 如果需要生成，生成后把URL当作 audio_files
    3. 统一走 Audio-driven 流程：转录 → 保存 → 创建 music_generation
    """
    try:
        # 获取用户输入数据
        user_input_data = state.get("user_input_data")
        user_input = user_input_data.user_input if user_input_data else ""
        audio_files = user_input_data.audio_files if user_input_data else []
        user_option = user_input_data.user_option if user_input_data else None
        from ....models.tool_enums import DefaultValues
        detected_language = state.get("detected_language", DefaultValues.DEFAULT_LANGUAGE)

        user_option = user_input_data.user_option if user_input_data else None
        if _is_product_launch(user_option):
            from ....models.tool_enums import DefaultValues as DV
            from ....models.video_state import GenerationConfig
            target_duration = user_option.duration if user_option else DV.VIDEO_DURATION
            generation_config = GenerationConfig.for_product_launch()
            logger.info("🎙️ Product Launch：跳过 Suno/转录，TTS 旁白驱动 (duration=%ss)", target_duration)
            return {
                "actual_target_duration": target_duration,
                "generation_config": generation_config,
            }

        if _is_short_drama(user_option):
            from ....models.tool_enums import DefaultValues as DV
            from ....models.video_state import GenerationConfig
            target_duration = user_option.duration if user_option else DV.VIDEO_DURATION
            generation_config = GenerationConfig.for_short_drama()
            logger.info("🎭 Short Drama：跳过 Suno/转录，片内对白 + 稀疏旁白 (duration=%ss)", target_duration)
            return {
                "actual_target_duration": target_duration,
                "generation_config": generation_config,
            }

        # ✅ 修复：按需创建连接，不依赖 context
        from ....models.database import AsyncSessionLocal

        # generation_config = determine_generation_config(has_audio=False, user_option=user_option)
        # return {
        #     "actual_target_duration": user_option.duration if user_option else 30,  # 使用用户指定时长（BGM不需要精确匹配）
        #     "generation_config": generation_config
        # }
        
        # ========================================
        # 前置：如果没有音频，按音乐意图三选一：lyrics_provided | auto_lyrics_song | instrumental_bgm
        # ========================================
        intent_analysis_messages = []  # 收集音乐意图分析的 messages
        # 是否要在转录后做 smart_clip 分析（只对 lyrics_provided / auto_lyrics_song 路径开启；
        # 用户上传音频或纯 BGM 都不做）
        smart_clip_eligible = False
        if not audio_files:
            precomputed = music_intent_from_state(state)
            if precomputed is not None:
                intent = precomputed
                intent_analysis_messages = []
                logger.info(
                    "🎵 没有音频文件，复用 user_input 阶段音乐意图: %s (mode=%s)",
                    intent.value,
                    state.get("music_workflow_mode"),
                )
            else:
                logger.info(f"🎵 没有音频文件，检测音乐生成意图")
                intent, analysis_messages = await analyze_music_intent(
                    user_input, history_messages=state.get("messages", [])
                )
                intent_analysis_messages = analysis_messages
            if intent in (MusicIntentType.LYRICS_PROVIDED, MusicIntentType.AUTO_LYRICS_SONG):
                smart_clip_eligible = True
            
            if intent == MusicIntentType.LYRICS_PROVIDED:
                logger.info(f"🎵 意图=lyrics_provided，按用户歌词+时长生成歌曲")
                from ....models.tool_enums import DefaultValues
                target_duration = user_option.duration if user_option else DefaultValues.VIDEO_DURATION
                music_messages, music_version = await generate_single_suno_music(
                    user_input=user_input,
                    target_duration=target_duration,
                    needs_lyrics=True,
                    prioritize_duration=True,
                    images=user_input_data.images if user_input_data else [],
                    send_event_func=send_event_func,
                    conversation_id=str(state.get("conversation_id")),
                    detected_language=detected_language,
                )
                intent_analysis_messages.extend(music_messages)
                if not music_version.success:
                    logger.error(f"❌ 音乐生成失败: {music_version.error_msg}")
                    await _notify_and_raise_music_failure(send_event_func, music_version, detected_language, conversation_id=state.get("conversation_id"))
                logger.info(f"✅ 歌曲生成成功: {music_version.music_url}, 时长 {music_version.duration}秒")
                generated_lyrics = None
                suno_clip_id = None
                if music_version.additional_data and music_version.additional_data.get("clips"):
                    first_clip_data = music_version.additional_data["clips"][0]
                    generated_lyrics = first_clip_data.get("lyrics")
                    suno_clip_id = first_clip_data.get("clip_id")
                    if generated_lyrics:
                        logger.info(f"🎵 提取到生成的歌词，将作为转录参考: {generated_lyrics[:100]}...")
                # LLM Agent 结构化响应偶发会丢掉 Optional 的 clip_id，导致 hybrid 误走 upload 路径（撞 Suno 5xx）。
                # 从 Suno 落库的 S3 文件名 `suno_<task>_clip_<i>_<clip_id>.mp3` 严格回解兜底。
                if not suno_clip_id:
                    suno_clip_id = _recover_suno_clip_id_from_url(music_version.music_url)
                    if suno_clip_id:
                        logger.info("🎵 [clip_id_fallback] 从音频URL文件名回解 clip_id=%s", suno_clip_id)
                from ....models.video_state import AudioFileUserInput
                generated_audio = AudioFileUserInput(
                    url=music_version.music_url,
                    filename=f"suno_lyrics_{state['run_id']}.mp3",
                    generated_lyrics=generated_lyrics,
                    suno_clip_id=suno_clip_id,
                )
                audio_files = [generated_audio]
                logger.info(f"🎵 将生成的歌曲添加到 audio_files，转为 Audio-driven 流程")
            elif intent == MusicIntentType.AUTO_LYRICS_SONG:
                logger.info(f"🎵 意图=auto_lyrics_song，用户未提供歌词：由 LLM 设计歌词后走歌词 custom 路线（以歌词字数控时长），再转录")
                from ....models.tool_enums import DefaultValues
                target_duration = user_option.duration if user_option else DefaultValues.VIDEO_DURATION
                music_messages, music_version = await generate_single_suno_music(
                    user_input=user_input,
                    target_duration=target_duration,
                    needs_lyrics=True,
                    prioritize_duration=True,
                    images=user_input_data.images if user_input_data else [],
                    send_event_func=send_event_func,
                    conversation_id=str(state.get("conversation_id")),
                    design_lyrics=True,
                    detected_language=detected_language,
                )
                intent_analysis_messages.extend(music_messages)
                if not music_version.success:
                    logger.error(f"❌ Auto lyrics 音乐生成失败: {music_version.error_msg}")
                    await _notify_and_raise_music_failure(send_event_func, music_version, detected_language, conversation_id=state.get("conversation_id"))
                logger.info(f"✅ Auto lyrics 歌曲生成成功: {music_version.music_url}, 时长 {music_version.duration}秒")
                generated_lyrics = None
                suno_clip_id = None
                if music_version.additional_data and music_version.additional_data.get("clips"):
                    first_clip_data = music_version.additional_data["clips"][0]
                    generated_lyrics = first_clip_data.get("lyrics")
                    suno_clip_id = first_clip_data.get("clip_id")
                    if generated_lyrics:
                        logger.info(f"🎵 提取到生成的歌词，将作为转录参考: {generated_lyrics[:100]}...")
                # LLM Agent 结构化响应偶发会丢掉 Optional 的 clip_id，导致 hybrid 误走 upload 路径（撞 Suno 5xx）。
                # 从 Suno 落库的 S3 文件名 `suno_<task>_clip_<i>_<clip_id>.mp3` 严格回解兜底。
                if not suno_clip_id:
                    suno_clip_id = _recover_suno_clip_id_from_url(music_version.music_url)
                    if suno_clip_id:
                        logger.info("🎵 [clip_id_fallback] 从音频URL文件名回解 clip_id=%s", suno_clip_id)
                from ....models.video_state import AudioFileUserInput
                generated_audio = AudioFileUserInput(
                    url=music_version.music_url,
                    filename=f"suno_auto_lyrics_{state['run_id']}.mp3",
                    generated_lyrics=generated_lyrics,
                    suno_clip_id=suno_clip_id,
                )
                audio_files = [generated_audio]
                logger.info(f"🎵 将生成的歌曲添加到 audio_files，转为 Audio-driven 流程")
            # intent == INSTRUMENTAL_BGM：不在此处生成，下面会走「无音频」分支，标记并行 BGM
        
        # ========================================
        # 统一的 Audio-driven 流程
        # ========================================
        if audio_files:
            logger.info(f"🎵 Audio-driven - 开始音频转录")
            
            # 默认使用第一个音频文件
            first_audio = audio_files[0]
            
            # transcribe audio（从 video_analysis 移过来）
            from .smart_clip_flow import transcribe_audio_for_analysis

            # 获取生成的歌词（如果有）
            generated_lyrics = getattr(first_audio, 'generated_lyrics', None)
            suno_clip_id = getattr(first_audio, 'suno_clip_id', None)

            audio_transcription = await transcribe_audio_for_analysis(
                first_audio.url,
                filename=first_audio.filename,
                suno_clip_id=suno_clip_id,
                user_option=user_option,
                user_input=user_input,
                generated_lyrics=generated_lyrics,
                music_intent=state.get("music_intent"),
                music_workflow_mode=state.get("music_workflow_mode"),
                fast_path=False,
            )
            
            if not audio_transcription:
                raise BusinessException(
                    BusinessExceptionCode.BUSINESS_ERROR,
                    "音频转录失败"
                )
            
            logger.info(f"🎵 音频转录成功: {len(audio_transcription.segments)} 个片段, 时长 {audio_transcription.duration}秒")
            
            # 保存音频转录结果到数据库
            from ....crud.video.video_audio import (
                create_video_audio_transcription,
                create_video_audio_segment,
                create_video_audio_section,
            )
            from ....crud.video.video_audio import create_music_from_audio_transcription_segments
            
            extra = (audio_transcription.additional_data or {}) if isinstance(audio_transcription.additional_data, dict) else {}
            transcription_db = await create_video_audio_transcription(
                run_id=state.get("run_id", ""),
                task=audio_transcription.task,
                language=audio_transcription.language,
                duration=audio_transcription.duration,
                text=audio_transcription.text,
                audio_url=audio_transcription.audio_url,
                user_id=state.get("user_id", ""),
                conversation_id=str(state.get("conversation_id", "")),
                thread_id=state.get("thread_id", ""),
                filename=audio_transcription.filename,
                is_instrumental=audio_transcription.is_instrumental,
                additional_data=audio_transcription.additional_data,
                song_name=extra.get("song_name"),
                global_bpm=extra.get("global_bpm"),
                genre=extra.get("genre"),
                global_emotion=extra.get("global_emotion"),
                suggested_global_theme=extra.get("suggested_global_theme"),
                suggested_color_palette=extra.get("suggested_color_palette"),
            )
            audio_transcription_uuid = transcription_db.uuid
            logger.info(f"💾 音频转录已保存到数据库: {audio_transcription_uuid}")
            
            # 保存音频片段（含 section_index 时写入 additional_data，便于与段落对齐）
            gemini_segments = (extra.get("gemini_segments") or []) if isinstance(extra.get("gemini_segments"), list) else []
            for i, segment in enumerate(audio_transcription.segments):
                seg_meta = gemini_segments[i] if i < len(gemini_segments) else {}
                section_index = seg_meta.get("section_index") if isinstance(seg_meta, dict) else None
                vocal_presence = seg_meta.get("vocal_presence") if isinstance(seg_meta, dict) else None
                if vocal_presence is None:
                    vocal_presence = bool((segment.text or "").strip())
                seg_additional = None
                if section_index is not None:
                    seg_additional = {"section_index": section_index}
                vocal_gender = getattr(segment, "vocal_gender", None) if getattr(segment, "vocal_gender", None) in ("f", "m") else None
                await create_video_audio_segment(
                    run_id=state.get("run_id", ""),
                    transcription_uuid=audio_transcription_uuid,
                    segment_id=segment.id,
                    start=segment.start,
                    end=segment.end,
                    duration=segment.duration,
                    text=segment.text,
                    emotion=segment.emotion,
                    tempo=segment.tempo,
                    user_id=state.get("user_id", ""),
                    conversation_id=str(state.get("conversation_id", "")),
                    thread_id=state.get("thread_id", ""),
                    vocal_presence=vocal_presence,
                    vocal_gender=vocal_gender,
                    additional_data=seg_additional,
                )
            
            # 若转录结果含 Song Structure（sections），写入 video_audio_section
            sections_raw = extra.get("sections") if isinstance(extra.get("sections"), list) else None
            if sections_raw:
                for sec in sections_raw:
                    if not isinstance(sec, dict) or sec.get("section_type") is None:
                        continue
                    st = sec.get("start_time")
                    et = sec.get("end_time")
                    if st is None or et is None:
                        continue
                    try:
                        await create_video_audio_section(
                            transcription_uuid=audio_transcription_uuid,
                            section_type=str(sec["section_type"]),
                            start_time=float(st),
                            end_time=float(et),
                            user_id=state.get("user_id", ""),
                            conversation_id=str(state.get("conversation_id", "")),
                            thread_id=state.get("thread_id", ""),
                            run_id=state.get("run_id", ""),
                            musical_features=sec.get("musical_features"),
                            section_emotion=sec.get("section_emotion"),
                            suggested_visual_intensity=sec.get("suggested_visual_intensity"),
                            suggested_rhythmic_strategy=sec.get("suggested_rhythmic_strategy"),
                            suggested_visual_theme=sec.get("suggested_visual_theme"),
                            suggested_context=sec.get("suggested_context"),
                        )
                    except Exception as e:
                        logger.warning(f"创建 video_audio_section 失败（跳过该段）: {e}")
                logger.info(f"💾 已写入 {len(sections_raw)} 个 Song Structure 段落到 video_audio_section")
            
            # 插入 music_generation 记录（注意：此时还没有 story_outline_uuid）
            music_generation_ids = await create_music_from_audio_transcription_segments(
                    audio_transcription_id=audio_transcription_uuid,
                    conversation_id=str(state["conversation_id"]),
                    thread_id=str(state["thread_id"]),
                    run_id=state["run_id"],
                    user_id=state["user_id"],
                    story_outline_id=None
                )

            # ========================================
            # ✨ Smart Clip：仅 lyrics_provided / auto_lyrics_song 路径触发
            #    用户上传音频或纯 BGM 都不做
            # ========================================
            if smart_clip_eligible and music_generation_ids:
                try:
                    from .smart_clip_flow import (
                        build_smart_clip_ready_payload,
                        fetch_smart_clip_peaks,
                        run_smart_clip_analysis,
                    )

                    user_target_duration = float(user_option.duration) if user_option and user_option.duration else float(audio_transcription.duration)
                    logger.info(
                        "🎵 [smart_clip] 开始分析：audio=%s, duration=%.2fs, target=%.2fs",
                        first_audio.url, audio_transcription.duration, user_target_duration,
                    )
                    sc_analysis = await run_smart_clip_analysis(
                        audio_url=first_audio.url,
                        target_duration_sec=user_target_duration,
                        transcription=audio_transcription,
                        audio_duration_sec=float(audio_transcription.duration),
                    )

                    peaks_payload = await fetch_smart_clip_peaks(
                        first_audio.url,
                        state.get("run_id", ""),
                    )

                    smart_clip_payload = build_smart_clip_ready_payload(
                        sc_analysis,
                        first_audio.url,
                        peaks=peaks_payload,
                    )

                    # 把 smart_clip 挂到第一条 music_generation 上（gate_after_music_node 后续会拉它）
                    target_mg_uuid = music_generation_ids[0]
                    from ....crud.video.video_audio import get_music_generation_by_uuid
                    target_mg = await get_music_generation_by_uuid(target_mg_uuid)
                    base_ad: Dict[str, Any] = dict(getattr(target_mg, "additional_data", None) or {})
                    base_ad["smart_clip"] = smart_clip_payload
                    await update_music_generation_additional_data(target_mg_uuid, base_ad)
                    logger.info(
                        "🎵 [smart_clip] 已落库 music_generation=%s, method=%s, recommended=[%.2f-%.2f]s",
                        target_mg_uuid, sc_analysis.method,
                        sc_analysis.recommended.start_sec, sc_analysis.recommended.end_sec,
                    )
                except Exception as e:
                    logger.warning("🎵 [smart_clip] 分析失败（不阻塞主链路）：%s", e)

            # 发送音乐生成事件
            user_message = await get_i18n_message_async(
                "music.generated",
                default="Background music is ready — preview it and continue, or trim it to a tighter segment before moving on to storyboarding",
                lang=detected_language,
            )
            await send_event_func(
                conversation_id=state["conversation_id"],
                event_type=MessageType.MUSIC_GENERATED,
                message=user_message,
                extra_data={
                    "music_generation_ids": music_generation_ids,
                    "music_count": len(music_generation_ids),
                    "run_id": state.get("run_id"),
                    "thread_id": state.get("thread_id")
                }
            )
            
            # 返回结果 - 使用 determine_generation_config 决定生成配置
            generation_config = determine_generation_config(has_audio=True, user_option=user_option)
            return {
                "audio_transcription_uuids": [audio_transcription_uuid],
                "music_generation_uuids": music_generation_ids,
                "actual_target_duration": audio_transcription.duration,  # ✅ 关键：实际音乐时长
                "generation_config": generation_config,
                "messages": intent_analysis_messages  # 返回本次调用的 messages（不包含 history_messages）
            }
        
        # ========================================
        # 无音频（needs_lyrics=False）：纯BGM模式（标记为并行生成）
        # ========================================
        logger.info(f"🎵 无音频，纯BGM模式（标记为并行生成）")
        from ....models.tool_enums import DefaultValues
        target_duration = user_option.duration if user_option else DefaultValues.VIDEO_DURATION
        
        # 不生成，返回配置（标记 generate_music=True）
        generation_config = determine_generation_config(has_audio=False, user_option=user_option)
        logger.info(f"✅ 音乐配置完成: generate_music={generation_config.generate_music}, 将在后续并行生成")
        
        return {
            "actual_target_duration": target_duration,
            "generation_config": generation_config,
            "messages": intent_analysis_messages  # 返回本次调用的 messages（不包含 history_messages）
        }
            
    except BusinessException as e:
        logger.error(f"music_generation_node:业务异常 - {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"music_generation_node:未知异常 - {str(e)}")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"音乐生成失败: {str(e)}"
        )


async def music_bgm_generation_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any
) -> Union[VideoAgentState, Dict[str, Any]]:
    """音乐BGM生成节点（并行）：生成纯音乐BGM
    
    只在 generation_config.generate_music=True 时执行
    与 video_generation 并行执行，节省时间
    
    ⚠️ 并发安全：创建独立的数据库会话，避免与其他并行任务冲突
    """
    # 检查是否需要生成BGM
    generation_config = state.get("generation_config")
    if not generation_config or not generation_config.generate_music:
        logger.info("🎵 跳过BGM生成（不需要或已有音频）")
        return {}
    
    logger.info("🎵 开始并行生成BGM...")
    
    # 从 state 获取所有需要的参数
    user_input_data = state.get("user_input_data")
    user_input = user_input_data.user_input if user_input_data else ""
    user_option = user_input_data.user_option if user_input_data else None
    images = user_input_data.images if user_input_data else []
    from ....models.tool_enums import DefaultValues
    detected_language = state.get("detected_language", DefaultValues.DEFAULT_LANGUAGE)
    from ....models.tool_enums import DefaultValues
    target_duration = state.get("actual_target_duration", DefaultValues.VIDEO_DURATION)
    
    # ✅ 修复：按需创建连接，不依赖 context
    # 生成音乐（不占用数据库连接）
    all_messages, music_version = await generate_single_suno_music(
        user_input=user_input,
        target_duration=target_duration,
        needs_lyrics=False,
        prioritize_duration=True,
        images=images,
        send_event_func=send_event_func,
        conversation_id=state.get("conversation_id")
    )
    
    if not music_version.success:
        logger.warning(f"⚠️ BGM生成失败: {music_version.error_msg}")
        return {}
    
    logger.info(f"✅ BGM生成成功: {music_version.music_url}, 时长 {music_version.duration}秒")
    
    serialized_additional_data = None
    if music_version.additional_data:
        try:
            serialized_additional_data = json.loads(
                json.dumps(music_version.additional_data, default=lambda o: o.model_dump() if hasattr(o, 'model_dump') else str(o))
            )
        except Exception as e:
            logger.warning(f"序列化 additional_data 失败: {e}")
            serialized_additional_data = {"source": "suno_bgm"}

    # 创建音乐生成主记录：整段 Suno BGM，is_full_story_music=True，后续合成用 music_url
    music_uuid = await create_music_generation(
        run_id=state["run_id"],
        user_id=state["user_id"],
        conversation_id=str(state["conversation_id"]),
        thread_id=str(state["thread_id"]),
        is_full_story_music=True,
        is_instrumental=True,
        additional_data=serialized_additional_data
    )

    # 创建音乐版本记录（provider 用枚举）；确保有 URL：music_url 与 original_audio_url 均存为生成结果 URL
    await create_music_generation_version(
        music_generation_id=music_uuid,
        version_number=1,
        music_url=music_version.music_url,
        provider=MusicProvider.SUNO,
        model="",
        prompt=music_version.music_prompt or "",
        duration=music_version.duration,
        success=music_version.success,
        user_id=state["user_id"],
        conversation_id=str(state["conversation_id"]),
        thread_id=str(state["thread_id"]),
        run_id=state["run_id"],
        error_msg=music_version.error_msg,
        raw_error_msg=None,
        style=None,
        mood=None,
        tags=None,
        additional_data=None,
        original_audio_url=music_version.original_audio_url or music_version.music_url,
    )
    
    # 发送事件
    user_message = await get_i18n_message_async(
        "music.generated",
        default="Background music is ready — preview it and continue, or trim it to a tighter segment before moving on to storyboarding",
        lang=detected_language,
    )
    await send_event_func(
        conversation_id=state["conversation_id"],
        event_type=MessageType.MUSIC_GENERATED,
        message=user_message,
        extra_data={
            "music_generation_ids": [music_uuid],
            "run_id": state.get("run_id"),
            "thread_id": state.get("thread_id")
        }
    )
    
    return {
        "music_generation_uuids": [music_uuid]
    }

