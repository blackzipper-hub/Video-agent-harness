"""
视频生成功能模块
负责视频生成节点的实现和相关功能
"""
import logging
import math
import asyncio
import random
import re
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union, cast, Tuple
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langgraph.runtime import Runtime
from datetime import datetime
from pydantic import BaseModel, Field

# 导入 schemas（统一的 Pydantic models）
from ....schemas.video import (
    VideoPromptResult,
    BatchVideoPromptResult,
    VideoGenerationPrompt,
    StyleDetectionResult,
    BatchPromptEvaluationResult,
)

from ....models.video_state import VideoAgentState, KeyframeVersion, VideoGenerationVersion, DetailedShot, ContentCategory
from ....models.user_options import UserOption, VideoGenerationTool, should_use_reference_to_video, build_reference_to_video_images
from ....models.image_result import VideoProvider
from ....models.tool_enums import ToolProvider, ToolType, GenerationMode
from ....exceptions import BusinessException, BusinessExceptionCode
from ....utils import media_service_client as msc

from ....services.agent.base_agent import MessageType
from ..schemas import VideoContextSchema
from .stage_failure import detect_stage_failure, emit_stage_failure_event
from ....utils.error_classification import (
    classify_failure,
    user_facing_reason,
    sanitize_user_facing_reason,
)


def _resolve_user_video_error(
    raw: Optional[str],
    *,
    lang: Optional[str] = None,
) -> tuple[str, str]:
    """返回 (user_facing_error_msg, raw_error_msg)。"""
    raw_text = (raw or "").strip() or "视频生成失败"
    cat = classify_failure(raw_text)
    fallback = user_facing_reason(category=cat, lang=lang)
    return sanitize_user_facing_reason(raw_text, fallback=fallback), raw_text


def _shot_to_dict(shot: Any) -> Optional[Dict[str, Any]]:
    """将 DetailedShot (Pydantic) 或 msgspec.Struct 转为 dict，避免 msgspec.structs.asdict 对非 Struct 报错。"""
    if shot is None:
        return None
    if hasattr(shot, "model_dump"):
        return shot.model_dump()
    if hasattr(shot, "__struct_fields__"):
        return msgspec.structs.asdict(shot)
    return None
from ....services.agent.utils.database_utils import get_keyframes_from_db, get_detailed_shots_from_db, save_video_generation_to_db, get_completed_video_uuids_by_shot_number
from app.agent_config.duration import (
    get_audio_driven_duration_values,
    get_video_driven_duration_values,
)
from ....services.agent.utils.prompt_utils import (
    STYLE_NAMES,
    log_video_spin_prompt_trace,
)
from ..utils.message_utils import extract_ai_message_json, patch_tool_metrics_from_last_tool_message
from ..utils.cancellation import raise_if_cancelled

logger = logging.getLogger(__name__)


def _video_provider_from_tools_info(tools_info: Optional[Any]) -> str:
    """从 ToolsInfo 得到 VideoGenerationVersion 用的 provider 字符串（openai_sora / wavespeed 等），不用 tool name。"""
    if not tools_info or not getattr(tools_info, "provider", None):
        return VideoProvider.WAVESPEED.value
    tp = tools_info.provider
    if tp == ToolProvider.OPENAI:
        return VideoProvider.OPENAI_SORA.value
    return getattr(tp, "value", VideoProvider.WAVESPEED.value)


# 常量配置
VIDEO_PROMPT_LENGTH_RANGE = "500-1000"     # 视频提示词字数要求（tool-call human 约束）


# ---------------------------------------------------------------------------
# Shared execution context — created once per generation run, reused for all shots
# ---------------------------------------------------------------------------

@dataclass
class VideoExecutionContext:
    """All state shared across shot executions. Avoids deep closures."""
    semaphore: asyncio.Semaphore
    agents_cache: Dict[str, Any] = field(default_factory=dict)
    user_option: Optional[Any] = None
    user_input: str = ""
    lipsync_audio_url_map: Dict[int, str] = field(default_factory=dict)
    detected_language: Optional[str] = None
    shot_character_ref_urls: Dict[int, List[str]] = field(default_factory=dict)
    shot_character_ref_labels: Dict[int, List[dict]] = field(default_factory=dict)
    skip_consistency_check: bool = False


async def init_execution_context(
    user_option: Optional[Any] = None,
    user_input: str = "",
    shared_semaphore: Optional[asyncio.Semaphore] = None,
    lipsync_audio_url_map: Optional[Dict[int, str]] = None,
    detected_language: Optional[str] = None,
    shot_character_ref_urls: Optional[Dict[int, List[str]]] = None,
    shot_character_ref_labels: Optional[Dict[int, List[dict]]] = None,
    skip_consistency_check: bool = False,
) -> VideoExecutionContext:
    """Build a reusable execution context (semaphore + shared state)."""
    from ..utils.prompt_utils import get_concurrency_limit

    if shared_semaphore is None:
        limit = get_concurrency_limit("video_generation")
        shared_semaphore = asyncio.Semaphore(limit)

    return VideoExecutionContext(
        semaphore=shared_semaphore,
        user_option=user_option,
        user_input=user_input,
        lipsync_audio_url_map=lipsync_audio_url_map or {},
        detected_language=detected_language,
        shot_character_ref_urls=shot_character_ref_urls or {},
        shot_character_ref_labels=shot_character_ref_labels or {},
        skip_consistency_check=skip_consistency_check,
    )


async def execute_single_video(
    keyframe: KeyframeVersion,
    shot: DetailedShot,
    generation_prompt: 'VideoGenerationPrompt',
    ctx: VideoExecutionContext,
) -> Tuple[VideoGenerationVersion, List[BaseMessage]]:
    """Execute a single video generation shot.

    Shared entry-point for both initial generation and regeneration.
    Uses *ctx* for all shared state (semaphore, LLM, caches, user_option, …).
    """
    from ..utils.prompt_utils import add_random_delay
    from ....services.tool_service import ToolService
    from ....models.image_result import VideoGenerationResult
    from ....tools.context_schemas import VideoGenerationContext
    from ....models.tool_enums import ToolMode, Resolution, DefaultValues, GenerationMode, AspectRatio, ContentCategory
    from prompts.prompt_config import PROMPTS_CONFIG, PromptName
    from ....services.agent.utils.llm_resilience import (
        StructuredResilienceKind,
        ainvoke_structured_resilient,
    )
    from ....utils.video_utils import align_video_to_reference_audio

    user_option = ctx.user_option

    # ---- extract shot / keyframe fields early (for failure branches) ----
    shot_number = shot.shot_number
    is_bridge = keyframe.is_bridge if keyframe else False
    keyframe_url = keyframe.keyframe_url if keyframe else ""
    keyframe_version_id = keyframe.version_id if keyframe else ""
    keyframe_version_ids = [keyframe.version_id] if keyframe and keyframe.version_id else []
    duration = shot.duration if shot else 0
    i2v_prompt = generation_prompt.i2v_prompt if generation_prompt else ""
    audio_segment_ids = keyframe.audio_segment_ids if keyframe else None
    detailed_shot_id = shot.uuid if shot else ""

    has_keyframe = keyframe and keyframe.keyframe_url
    _requested_lipsync = shot.generation_mode == GenerationMode.LIPSYNC.value

    # lipsync 音轨须先于工具选择解析：无 audio_url 时降级 normal，避免 Kling 等报 requires audio_url
    _shot_audio_url = None
    if _requested_lipsync and ctx.lipsync_audio_url_map:
        _shot_audio_url = ctx.lipsync_audio_url_map.get(shot_number)
    _is_lipsync_shot = _requested_lipsync and bool(_shot_audio_url)
    if _requested_lipsync and not _shot_audio_url:
        logger.warning(
            "🎭 镜头%d: 标记为 lipsync 但无 audio_url（旁白为空或 TTS 未生成），降级为 normal I2V",
            shot_number,
        )

    resolution = (
        Resolution(user_option.resolution.value)
        if user_option and user_option.resolution
        else DefaultValues.VIDEO_RESOLUTION
    )
    has_end_image = generation_prompt.needs_end_image if generation_prompt else False

    from .per_shot_generation_routing_service import resolve_user_option_for_shot

    eff_user_option = user_option
    if user_option and shot:
        if shot.generation_mode == GenerationMode.LIPSYNC.value:
            eff_user_option = resolve_user_option_for_shot(
                user_option, shot, "lipsync_video"
            )
        else:
            eff_user_option = resolve_user_option_for_shot(
                user_option, shot, "normal_video"
            )

    use_ref_t2v = (
        not _is_lipsync_shot
        and should_use_reference_to_video(eff_user_option)
    )
    mode = ToolMode.T2V if use_ref_t2v else (ToolMode.I2V if has_keyframe else ToolMode.T2V)
    has_end_image_for_tools = False if use_ref_t2v else has_end_image

    if _is_lipsync_shot and user_option:
        _gr = getattr(shot, "generation_routing", None) or {}
        logger.info(
            "[lipsync_tool_diag] execute_single_video shot_number=%s shot_uuid=%s "
            "routing.lipsync_video_tool=%r routing_keys=%s "
            "global_lipsync_video_tool=%s eff_lipsync_video_tool=%s",
            shot_number,
            getattr(shot, "uuid", None),
            _gr.get("lipsync_video_tool"),
            list(_gr.keys()) if isinstance(_gr, dict) else None,
            getattr(user_option.lipsync_video_tool, "value", user_option.lipsync_video_tool),
            getattr(eff_user_option.lipsync_video_tool, "value", eff_user_option.lipsync_video_tool)
            if eff_user_option
            else None,
        )

    tools_info = ToolService.get_video_generation_tools(
        eff_user_option, mode=mode, resolution=resolution,
        has_end_image=has_end_image_for_tools,
        generation_mode=(
            GenerationMode.LIPSYNC.value
            if _is_lipsync_shot
            else (
                GenerationMode.NORMAL.value
                if _requested_lipsync
                else shot.generation_mode
            )
        ),
    )
    default_provider = _video_provider_from_tools_info(tools_info)

    _ar = user_option.aspect_ratio.value if user_option and getattr(user_option, "aspect_ratio", None) else None
    _res = user_option.resolution.value if user_option and getattr(user_option, "resolution", None) else None

    def _make_version(success: bool, video_url: str = "", error_msg: str = None,
                      provider: str = None, extra: dict = None) -> VideoGenerationVersion:
        """Helper to build VideoGenerationVersion with common fields."""
        extra = extra or {}
        _resolved = (extra.get("resolved_i2v_prompt") or "").strip()
        _i2v_db = _resolved if _resolved else i2v_prompt
        return VideoGenerationVersion(
            shot_number=shot_number, is_bridge=is_bridge,
            keyframe_url=keyframe_url, keyframe_version_id=keyframe_version_id,
            keyframe_version_ids=keyframe_version_ids,
            video_url=video_url,
            duration=int(math.ceil(extra.get("dur", duration))) if extra.get("dur", duration) else 0,
            i2v_prompt=_i2v_db,
            provider=provider or default_provider,
            success=success, error_msg=error_msg,
            raw_error_msg=extra.get("raw_error_msg"),
            audio_segment_ids=audio_segment_ids,
            detailed_shot_id=detailed_shot_id,
            generation_mode=shot.generation_mode,
            audio_url=_shot_audio_url,
            aspect_ratio=_ar, resolution=_res,
            video_generation_tool=tools_info.user_option_tool if tools_info else None,
            seed=extra.get("seed"),
            model=extra.get("model"),
            video_tool_metrics=extra.get("video_tool_metrics"),
            tool_duration_sec=extra.get("tool_duration_sec"),
            tool_cost=extra.get("tool_cost"),
            consistency_reason=generation_prompt.consistency_reason if generation_prompt else None,
            preview_video_url=extra.get("preview_video_url"),
            reference_image_urls=extra.get("reference_image_urls"),
        )

    logger.info(f"🎬 镜头{shot_number}: 等待获取semaphore...")

    async with ctx.semaphore:
        await add_random_delay()
        logger.info(f"🎬 镜头{shot_number}: 已获取semaphore，开始生成")

        if not i2v_prompt:
            return (_make_version(False, error_msg="prompt生成失败"), [])

        try:
            if generation_prompt.needs_end_image and not use_ref_t2v:
                logger.info(f"🔗 镜头{shot_number}: 使用首尾帧模式, 原因: {generation_prompt.consistency_reason}")
            if use_ref_t2v:
                logger.info(f"🎬 镜头{shot_number}: reference-to-video 模式（T2V + reference_images）")

            video_tools = tools_info.tool_objects
            if not video_tools:
                raise Exception(f"无法获取{mode.value}模式的视频生成工具")

            start_image_url = generation_prompt.start_image_url or None
            end_image_url = generation_prompt.end_image_url or None
            if _is_lipsync_shot and _shot_audio_url:
                logger.info(f"🎭 镜头{shot_number}: lipsync 模式，audio_url={_shot_audio_url[:60]}...")
                end_image_url = None

            character_ref_urls = ctx.shot_character_ref_urls.get(shot_number)
            character_ref_lbls = ctx.shot_character_ref_labels.get(shot_number)

            reference_images = None
            if use_ref_t2v:
                from ....services.agent.video.agent_video_constants import MAX_REFERENCE_IMAGES_FOR_VIDEO
                reference_images = build_reference_to_video_images(
                    start_image_url,
                    end_image_url if generation_prompt.needs_end_image else None,
                    character_ref_urls,
                    MAX_REFERENCE_IMAGES_FOR_VIDEO,
                )
                logger.info(
                    "🎬 镜头%d: reference_images n=%d urls=%s",
                    shot_number,
                    len(reference_images),
                    [u[:48] + "..." if u and len(u) > 48 else u for u in reference_images],
                )

            _cc = getattr(user_option, "content_category", None) if user_option else None
            _cc_val = getattr(_cc, "value", None) or _cc
            _is_product_launch = (
                _cc == ContentCategory.PRODUCT_LAUNCH
                or _cc_val == ContentCategory.PRODUCT_LAUNCH.value
            )
            # Seedance 片内声（OM：generate_audio 默认开 —— 对白 + 特效/环境音）。
            # Short Drama：每镜都开（空镜也要 SFX）。其他品类：有对白才开。
            # lipsync / Product Launch 除外（外挂音轨或 TTS）。
            from .voice_delivery_contract import should_use_seedance_in_clip_dialogue
            _is_short_drama = (
                _cc == ContentCategory.SHORT_DRAMA
                or _cc_val == ContentCategory.SHORT_DRAMA.value
            )
            _generate_audio = (
                not _is_lipsync_shot
                and not _is_product_launch
                and (
                    _is_short_drama
                    or should_use_seedance_in_clip_dialogue(shot)
                )
            )

            tool_prompt_messages = await _build_single_video_tool_call_prompt(
                keyframe, shot, generation_prompt.i2v_prompt,
                tools_info.primary_tool_name,
                user_option,
                generation_prompt.end_image_url if generation_prompt.needs_end_image else None,
                ctx.user_input,
                generation_mode=shot.generation_mode,
                user_regenerate_instruction=getattr(generation_prompt, "user_regenerate_instruction", None),
                use_reference_to_video=use_ref_t2v,
                reference_images=reference_images,
            )
            log_video_spin_prompt_trace(
                "video_i2v_tool_execution",
                messages=tool_prompt_messages,
                extra_lines=[
                    f"shot_number={shot_number}",
                    f"i2v_prompt_sent_to_tool=\n{generation_prompt.i2v_prompt}",
                ],
            )

            _context_duration = math.ceil(duration) if (_is_lipsync_shot and duration) else None
            if _is_lipsync_shot and duration:
                logger.info(
                    "[时长诊断] 镜头%d: shot.duration(分镜/DB)=%.6f → context.duration=math.ceil=%s "
                    "(写入 VideoGenerationContext；LTX/Kling/WAN22 等计费优先用该整数，非对 audio_url 的实测时长)",
                    shot_number, float(duration), _context_duration,
                )

            context = VideoGenerationContext(
                aspect_ratio=(
                    AspectRatio(user_option.aspect_ratio.value)
                    if user_option and user_option.aspect_ratio
                    else DefaultValues.VIDEO_ASPECT_RATIO
                ),
                resolution=resolution,
                start_image_url=None if use_ref_t2v else start_image_url,
                end_image_url=None if use_ref_t2v else end_image_url,
                reference_images=reference_images,
                audio_url=_shot_audio_url,
                duration=_context_duration,
                language=ctx.detected_language,
                character_ref_image_urls=character_ref_urls,
                character_ref_labels=character_ref_lbls,
                skip_consistency_check=ctx.skip_consistency_check,
                generate_audio=_generate_audio,
            )

            inputs = {"messages": tool_prompt_messages}
            result = await ainvoke_structured_resilient(
                kind=StructuredResilienceKind.CREATE_AGENT,
                prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_VIDEO_GENERATION_TOOL_EXECUTION],
                agent_inputs=inputs,
                agent_tools=video_tools,
                context_schema=VideoGenerationContext,
                agent_invoke_context=context,
                wrap_agent_parse_fallback=True,
                log_context={"shot_number": shot_number, "phase": "video_tool_execution"},
            )

            _sr = result.get("structured_response")
            logger.info(
                "[video_agent] ainvoke 后 structured_response: tool_duration_sec=%s, tool_cost=%s, has_sr=%s",
                getattr(_sr, "tool_duration_sec", None) if _sr else None,
                getattr(_sr, "tool_cost", None) if _sr else None,
                _sr is not None,
            )

            agent_full_messages = result.get("messages", [])
            input_count = len(tool_prompt_messages)
            output_messages = agent_full_messages[input_count:] if len(agent_full_messages) > input_count else []
            all_messages = tool_prompt_messages + output_messages

            structured_response: VideoGenerationResult = cast(VideoGenerationResult, result.get("structured_response"))
            structured_response = patch_tool_metrics_from_last_tool_message(
                result.get("messages", []), structured_response,
            )

            if structured_response and structured_response.success and structured_response.video_url:
                logger.info(f"✅ 镜头{shot_number}: 视频生成成功, provider={structured_response.provider}")
                duration_to_use = float(duration) if _is_lipsync_shot and duration else (structured_response.duration or duration)
                provider = structured_response.provider or default_provider
                final_url = structured_response.video_url

                _trim_run = f"shot_trim_{shot_number}_{uuid.uuid4().hex[:6]}"
                if _shot_audio_url:
                    try:
                        final_url = await align_video_to_reference_audio(
                            final_url,
                            _shot_audio_url,
                            allow_speed_adjust=False,
                            run_id=_trim_run,
                        )
                        _vi = await msc.video_info(final_url)
                        duration_to_use = float(_vi.get("duration") or duration_to_use)
                        logger.info(
                            "✂️ 镜头%d: trim 至 reference audio → %.2fs",
                            shot_number, duration_to_use,
                        )
                    except Exception as e:
                        logger.warning("⚠️ 镜头%d: post-gen trim 失败 (%s), 使用原始视频", shot_number, e)
                elif duration > 0 and audio_segment_ids:
                    try:
                        final_url = await align_video_to_reference_audio(
                            final_url,
                            target_duration=float(duration),
                            allow_speed_adjust=False,
                            run_id=_trim_run,
                        )
                        logger.info("✂️ 镜头%d: trim → %.2fs (%s)", shot_number, duration, final_url)
                    except Exception as e:
                        logger.warning("⚠️ 镜头%d: post-gen trim 失败 (%s), 使用原始视频", shot_number, e)

                _gp_vid = (getattr(structured_response, "generated_prompt", None) or "").strip()
                _preview_url = getattr(structured_response, "preview_video_url", None)
                return (_make_version(
                    True, video_url=final_url, provider=provider,
                    extra={
                        "dur": duration_to_use,
                        "seed": getattr(structured_response, "seed", None),
                        "model": getattr(structured_response, "model", None),
                        "video_tool_metrics": getattr(structured_response, "video_tool_metrics", None),
                        "tool_duration_sec": getattr(structured_response, "tool_duration_sec", None),
                        "tool_cost": getattr(structured_response, "tool_cost", None),
                        "resolved_i2v_prompt": _gp_vid or None,
                        "preview_video_url": _preview_url,
                        "reference_image_urls": list(reference_images) if reference_images else None,
                    },
                ), all_messages)
            else:
                raw_err = (
                    (getattr(structured_response, "raw_error_msg", None) or structured_response.message)
                    if structured_response and (getattr(structured_response, "raw_error_msg", None) or structured_response.message)
                    else "视频生成失败"
                )
                user_err, raw_stored = _resolve_user_video_error(
                    raw_err, lang=ctx.detected_language,
                )
                logger.error(f"❌ 镜头{shot_number}: 视频生成失败 - {raw_stored}")
                _model_on_fail = getattr(structured_response, "model", None) or (
                    tools_info.tool_type.value if tools_info and getattr(tools_info, "tool_type", None) else None
                )
                return (_make_version(
                    False, error_msg=user_err,
                    extra={
                        "raw_error_msg": raw_stored,
                        "model": _model_on_fail,
                        "video_tool_metrics": getattr(structured_response, "video_tool_metrics", None),
                        "tool_duration_sec": getattr(structured_response, "tool_duration_sec", None),
                        "tool_cost": getattr(structured_response, "tool_cost", None),
                    },
                ), all_messages)

        except Exception as e:
            user_err, raw_stored = _resolve_user_video_error(str(e), lang=ctx.detected_language)
            logger.error(f"❌ 镜头{shot_number}: 视频生成异常 - {raw_stored}")
            _model_on_exc = tools_info.tool_type.value if tools_info and getattr(tools_info, "tool_type", None) else None
            return (_make_version(False, error_msg=user_err, extra={"raw_error_msg": raw_stored, "model": _model_on_exc}), [])


async def detect_video_style(
    user_input: str,
    story_outline: Optional[Any],
    llm: Any,
    log_context: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """使用LLM检测视频风格类型，返回多个相关风格"""
    available_styles = list(STYLE_NAMES)
    
    # 构建内容描述
    content_parts = [f"用户输入: {user_input}"]
    
    if story_outline:
        if story_outline.structure and story_outline.structure.chapters:
            chapter_descriptions = []
            for chapter in story_outline.structure.chapters:
                chapter_descriptions.append(f"章节: {chapter.title} - {chapter.description}")
            content_parts.append(f"故事章节: {' '.join(chapter_descriptions)}")
        
        if story_outline.theme:
            content_parts.append(f"主题: {story_outline.theme}")
            
        if story_outline.style_guide:
            content_parts.append(f"风格指南: {story_outline.style_guide}")
    
    combined_content = '\n'.join(content_parts)
    
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        from prompts.prompt_config import PROMPTS_CONFIG, PromptName
        from app.orchestration.skills.prompt_context import skill_system_message
        from ....services.agent.utils.llm_resilience import StructuredResilienceKind, ainvoke_structured_resilient

        from app.orchestration.skills.prompt_context import facts_human_message

        system = skill_system_message(
            "video-style-detection-director",
            lead="Follow video-style-detection-director.",
        )
        style_messages = [
            SystemMessage(content=system),
            HumanMessage(content=facts_human_message({
                "available_styles": list(available_styles),
                "video_content": combined_content,
            })),
        ]
        result = await ainvoke_structured_resilient(
            prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_VIDEO_GENERATION_STYLE_DETECTION],
            kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
            structured_chat_messages=style_messages,
            include_raw=True,
            log_context=log_context,
        )
        
        # 提取 parsed 和 raw
        detection_result = result["parsed"]
        raw_message = result.get("raw")
        
        # TODO: raw_message 会在后续保存到 messages 中使用
        # 目前先提取出来，确保不丢失
        
        # 验证并过滤有效的风格
        valid_styles = []
        for style in detection_result.detected_styles:
            if style in available_styles:
                valid_styles.append(style)
        
        # 如果没有有效风格或为空，返回general
        if not valid_styles:
            valid_styles = ["general"]
        
        logger.info(f"🎨 检测到视频风格: {valid_styles} (置信度: {detection_result.confidence:.2f})")
        return valid_styles
                
    except Exception as e:
        logger.warning(f"LLM风格检测失败: {e}")
    
    return ["general"]


async def _build_single_video_tool_call_prompt(
    keyframe: KeyframeVersion,
    shot: DetailedShot,
    i2v_prompt: str,
    tool_name: str,
    user_option: Optional[UserOption] = None,
    end_image_url: Optional[str] = None,
    user_input: str = "",
    generation_mode: Optional[str] = None,
    user_regenerate_instruction: Optional[str] = None,
    use_reference_to_video: bool = False,
    reference_images: Optional[List[str]] = None,
) -> List[BaseMessage]:
    """Facts-only Human JSON + video-tool-director skill (craft in SKILL.md)."""
    from langchain_core.messages import SystemMessage, HumanMessage
    from app.orchestration.skills.prompt_context import (
        facts_human_message,
        skill_system_message,
    )

    has_keyframe = keyframe and keyframe.keyframe_url
    mode = "i2v" if has_keyframe else "t2v"

    supported_durations = (
        get_audio_driven_duration_values(user_option)
        if generation_mode == GenerationMode.LIPSYNC.value
        else get_video_driven_duration_values(user_option)
    )
    shot_dur = getattr(shot, "duration", None)
    if shot_dur is not None and shot_dur > 0:
        candidates = [d for d in supported_durations if d >= shot_dur]
        target_duration = min(candidates) if candidates else max(supported_durations)
        logger.info(
            "镜头%d duration: shot.duration=%.2fs 支持范围=%s → target_duration=%ds (不小于 shot 的最小支持值)",
            shot.shot_number, shot_dur, supported_durations, target_duration,
        )
    else:
        target_duration = min(supported_durations, key=lambda x: abs(x - (shot_dur or 0))) if supported_durations else 3
        logger.info(
            "镜头%d duration: shot.duration=%s 无有效时长 → target_duration=%ds (最接近支持值)",
            shot.shot_number, shot_dur, target_duration,
        )

    regen = (user_regenerate_instruction or "").strip() or None
    start_url = (keyframe.keyframe_url if keyframe and keyframe.keyframe_url else None)
    facts = {
        "tool_name": tool_name,
        "mode": mode,
        "shot_number": shot.shot_number,
        "is_bridge": bool(getattr(keyframe, "is_bridge", False) or shot.is_bridge),
        "duration": shot.duration,
        "target_duration": target_duration,
        "shot_type": shot.shot_type,
        "camera_position": shot.camera_position or "",
        "camera_angle": shot.camera_angle or "",
        "subject_angle": shot.subject_angle or "",
        "subject_pose": shot.subject_pose or "",
        "scene_description": shot.scene_description or "",
        "camera_movement": shot.camera_movement or "",
        "lighting": shot.lighting or "",
        "visual_effects": shot.visual_effects or "",
        "dialogue": shot.dialogue or "",
        "narration": shot.narration or "",
        "sound_effects": shot.sound_effects or "",
        "transition": shot.transition or "",
        "user_input": user_input or "",
        "i2v_prompt": i2v_prompt or "",
        "user_regenerate_instruction": regen,
        "start_image_url": start_url,
        "end_image_url": end_image_url or None,
        "use_reference_to_video": bool(use_reference_to_video),
        "reference_images_count": len(reference_images or []),
        "prompt_length_range": VIDEO_PROMPT_LENGTH_RANGE,
        "generation_mode": generation_mode or "",
    }
    system = skill_system_message("video-tool-director", lead="Follow video-tool-director.")
    return [SystemMessage(content=system), HumanMessage(content=facts_human_message(facts))]


async def generate_batch_video_prompts(
    keyframes_batch: List[KeyframeVersion],
    shots_batch: List[DetailedShot],
    user_input: str,
    prev_shot: Optional[DetailedShot] = None,
    next_shot: Optional[DetailedShot] = None,
    user_option: Optional[UserOption] = None,
    llm: Any = None,
    story_outline: Optional[Any] = None,
    content_category: Optional[str] = None,
    detected_language: Optional[str] = None,
    hidden_style_description: Optional[str] = None,
    log_context: Optional[Dict[str, Any]] = None,
) -> Tuple[List[BaseMessage], List[VideoGenerationPrompt]]:
    """第一步：批量生成视频prompts（使用LLM批量生成，确保上下文连贯）
    
    Returns:
        Tuple[List[BaseMessage], List[VideoGenerationPrompt]]: 
            - 第一个元素是消息列表
            - 第二个元素是VideoGenerationPrompt列表，包含prompt、首帧等完整信息
    """
    logger.info(f"🎬 第一步：批量生成{len(keyframes_batch)}个视频prompt")
    
    try:
        from ....services.tool_service import ToolService
        
        logger.info(f"🎬 批量生成{len(keyframes_batch)}个视频prompts（deep-agent / video-director）")

        # Deep-agent path: craft in video-director SKILL.md (no mustache assembly).
        import uuid as _uuid
        from app.services.agent.video.video_stage import (
            export_video_prompt_inputs,
            generate_video_prompts_via_deep_agent,
        )
        from ....models.video_state import ContentCategory as _ContentCategory
        _is_lip = content_category == _ContentCategory.LIP_SYNC_MV.value if content_category else False
        _has_lipsync = any(
            getattr(s, "generation_mode", None) == GenerationMode.LIPSYNC.value for s in shots_batch
        )
        tid = f"vid_{_uuid.uuid4().hex[:8]}"
        rid = f"run_{_uuid.uuid4().hex[:8]}"
        shot_rows = []
        for shot, kf in zip(shots_batch, keyframes_batch):
            shot_rows.append({
                "shot_number": shot.shot_number,
                "duration": shot.duration,
                "shot_type": shot.shot_type,
                "camera_position": shot.camera_position or "",
                "camera_angle": shot.camera_angle or "",
                "subject_angle": shot.subject_angle or "",
                "subject_pose": shot.subject_pose or "",
                "scene_description": shot.scene_description or "",
                "camera_movement": shot.camera_movement or "",
                "lighting": shot.lighting or "",
                "visual_effects": shot.visual_effects or "",
                "transition": shot.transition or "",
                "dialogue": shot.dialogue or "",
                "sound_effects": shot.sound_effects or "",
                "narration": shot.narration or "",
                "style_guide": shot.style_guide or "",
                "t2i_prompt": kf.t2i_prompt or "",
                "keyframe_url": kf.keyframe_url or "",
                "generation_mode": getattr(shot, "generation_mode", None) or "",
            })
        _paths = export_video_prompt_inputs(
            thread_id=tid,
            run_id=rid,
            batch_id="b0",
            shots=shot_rows,
            style_guide=(hidden_style_description or ""),
            user_input=user_input or "",
            keyframe_image_urls=[kf.keyframe_url for kf in keyframes_batch if kf.keyframe_url],
            prev_shot=_shot_to_dict(prev_shot),
            next_shot=_shot_to_dict(next_shot),
            is_lip_sync_mv=_is_lip,
            has_lipsync_shots=_has_lipsync,
        )
        try:
            art, da_msgs = await generate_video_prompts_via_deep_agent(
                thread_id=tid, run_id=rid, input_paths=_paths, detected_language=detected_language,
            )
        except Exception as da_err:
            # Transient workspace write races must not wipe a successful artifact into scene_description.
            from app.contracts.artifacts.video import VideoArtifact
            from app.services.agent.stage_runtime.workspace import (
                artifact_exists,
                read_artifact_json,
            )
            aname = _paths.get("artifact_name") or "video_prompts_b0.json"
            if artifact_exists(tid, rid, aname):
                logger.warning(
                    "🎬 video deep-agent raised after artifact write (%s); recovering %s/%s/%s",
                    da_err,
                    tid,
                    rid,
                    aname,
                )
                art = VideoArtifact.model_validate(read_artifact_json(tid, rid, aname))
                da_msgs = []
            else:
                raise
        batch_prompt_result = BatchVideoPromptResult(
            prompts=[VideoPromptResult(shot_number=p.shot_number, i2v_prompt=p.i2v_prompt) for p in art.prompts]
        )
        raw_result = {"parsed": batch_prompt_result, "raw": None}
        _da_messages = list(da_msgs or [])
        
        if not raw_result or "parsed" not in raw_result:
            raise Exception("LLM prompt生成失败：无法解析结果")
        
        batch_prompt_result = raw_result["parsed"]
        raw_message = raw_result.get("raw")
        
        logger.info(f"🎬 批量prompt生成完成：获得{len(batch_prompt_result.prompts)}个prompt结果")
        
        # ⭐ 将VideoPromptResult转换为VideoGenerationPrompt，添加首尾帧信息（与 shots_batch 对齐，遗漏则用场景描述补齐）
        # 按shot_number分组keyframes（每个shot可能有首帧和尾帧）
        keyframes_by_shot = {}
        for kf in keyframes_batch:
            if hasattr(kf, 'shot_number'):
                if kf.shot_number not in keyframes_by_shot:
                    keyframes_by_shot[kf.shot_number] = {}
                keyframes_by_shot[kf.shot_number][kf.frame_index] = kf

        ordered_shot_numbers: List[int] = []
        _seen_sn = set()
        for s in shots_batch:
            if s.shot_number not in _seen_sn:
                _seen_sn.add(s.shot_number)
                ordered_shot_numbers.append(s.shot_number)
        expected_set = set(ordered_shot_numbers)

        def _build_video_generation_prompt(sn: int, i2v_text: str) -> Optional[VideoGenerationPrompt]:
            shot_keyframes = keyframes_by_shot.get(sn, {})
            start_keyframe = shot_keyframes.get(0)
            end_keyframe = shot_keyframes.get(-1)
            if not start_keyframe:
                logger.warning(f"🎬 镜头{sn}: 无首帧，无法组装 VideoGenerationPrompt")
                return None
            _shot = next((s for s in shots_batch if s.shot_number == sn), None)
            return VideoGenerationPrompt(
                shot_number=sn,
                i2v_prompt=i2v_text,
                start_image_url=start_keyframe.keyframe_url,
                end_image_url=end_keyframe.keyframe_url if end_keyframe else None,
                needs_end_image=end_keyframe is not None,
                consistency_reason="使用首尾帧生成以保持一致性" if end_keyframe else None,
                generation_mode=getattr(_shot, "generation_mode", None) if _shot else None,
            )

        llm_by_shot: Dict[int, VideoGenerationPrompt] = {}
        for prompt_result in batch_prompt_result.prompts:
            sn = prompt_result.shot_number
            if sn not in expected_set:
                logger.warning(
                    "🎬 LLM 返回了未在本批次请求的镜头 %s，已忽略（本批次镜头: %s）",
                    sn,
                    ordered_shot_numbers,
                )
                continue
            if sn in llm_by_shot:
                logger.warning("🎬 镜头%s 出现重复的 video prompt，保留首次结果", sn)
                continue
            text = (prompt_result.i2v_prompt or "").strip()
            if not text:
                logger.warning("🎬 镜头%s 的 i2v_prompt 为空，将用场景描述补齐", sn)
                continue
            built = _build_video_generation_prompt(sn, text)
            if built:
                llm_by_shot[sn] = built

        generation_prompts = []
        padded_count = 0
        for sn in ordered_shot_numbers:
            if sn in llm_by_shot:
                generation_prompts.append(llm_by_shot[sn])
                continue
            shot = next((s for s in shots_batch if s.shot_number == sn), None)
            fallback = (
                shot.scene_description
                if shot and shot.scene_description
                else f"镜头{sn}的视频内容"
            )
            logger.warning(
                "🎬 批量 video prompt 遗漏或无效镜头 %s，已用场景描述补齐（长度=%s）",
                sn,
                len(fallback),
            )
            built = _build_video_generation_prompt(sn, fallback)
            if built:
                generation_prompts.append(built)
                padded_count += 1
            else:
                logger.error("🎬 镜头%s 无法补齐 video prompt（缺少首帧）", sn)

        if padded_count:
            logger.info("🎬 本批次为 %s 个镜头补全了缺失/无效的 video prompt", padded_count)

        logger.info(f"🎬 创建了{len(generation_prompts)}个VideoGenerationPrompt对象")
        
        # ✅ 只返回新增的消息（LLM 的输出，不包含输入）
        new_messages = [raw_message] if raw_message else []
        if _da_messages:
            new_messages = list(_da_messages) + new_messages
        return new_messages, generation_prompts
        
    except Exception as e:
        error_msg = f"批量prompt生成异常 - {str(e)}"
        logger.error(error_msg)
        
        # 返回失败结果 - 使用场景描述作为fallback
        failed_prompts = []
        
        # ⭐ 按shot_number分组keyframes
        keyframes_by_shot = {}
        for kf in keyframes_batch:
            if hasattr(kf, 'shot_number'):
                if kf.shot_number not in keyframes_by_shot:
                    keyframes_by_shot[kf.shot_number] = {}
                keyframes_by_shot[kf.shot_number][kf.frame_index] = kf
        
        for shot in shots_batch:
            shot_keyframes = keyframes_by_shot.get(shot.shot_number, {})
            start_keyframe = shot_keyframes.get(0)
            end_keyframe = shot_keyframes.get(-1)
            
            # 使用场景描述作为fallback prompt
            fallback_prompt = shot.scene_description if shot and shot.scene_description else f"镜头{shot.shot_number if shot else 'unknown'}的视频内容"
            failed_generation_prompt = VideoGenerationPrompt(
                shot_number=shot.shot_number if shot else 0,
                i2v_prompt=fallback_prompt,
                start_image_url=start_keyframe.keyframe_url if start_keyframe and start_keyframe.keyframe_url else "",
                end_image_url=end_keyframe.keyframe_url if end_keyframe and end_keyframe.keyframe_url else None,
                needs_end_image=end_keyframe is not None,
                consistency_reason="使用首尾帧生成以保持一致性" if end_keyframe else None,
                generation_mode=getattr(shot, "generation_mode", None) if shot else None,
            )
            failed_prompts.append(failed_generation_prompt)
        
        logger.info(f"🔄 使用场景描述作为fallback prompts: {len(failed_prompts)}个")
        return [], failed_prompts


async def execute_batch_video_generation(
    keyframes_batch: List[KeyframeVersion],
    shots_batch: List[DetailedShot],
    prompts: List[VideoGenerationPrompt],
    user_option: Optional[UserOption] = None,
    llm: Any = None,
    shared_semaphore: Optional[asyncio.Semaphore] = None,
    user_input: str = "",
    lipsync_audio_url_map: Optional[Dict[int, str]] = None,
    detected_language: Optional[str] = None,
    shot_character_ref_urls: Optional[Dict[int, List[str]]] = None,
    shot_character_ref_labels: Optional[Dict[int, List[dict]]] = None,
    execution_context: Optional[VideoExecutionContext] = None,
    skip_consistency_check: bool = False,
) -> Tuple[List[BaseMessage], List[VideoGenerationVersion]]:
    """并发执行视频生成 — 委托给 execute_single_video。

    当 execution_context 已提供时直接复用，否则新建（兼容 regeneration 等旧调用方）。
    """
    if execution_context is None:
        execution_context = await init_execution_context(
            user_option=user_option, user_input=user_input,
            shared_semaphore=shared_semaphore,
            lipsync_audio_url_map=lipsync_audio_url_map,
            detected_language=detected_language,
            shot_character_ref_urls=shot_character_ref_urls,
            shot_character_ref_labels=shot_character_ref_labels,
            skip_consistency_check=skip_consistency_check,
        )

    logger.info(f"🎬 并发执行{len(prompts)}个视频生成（基于prompts数量）")

    shot_map = {shot.shot_number: shot for shot in shots_batch}
    start_keyframe_map = {}
    for kf in keyframes_batch:
        if kf.frame_index == 0:
            start_keyframe_map[kf.shot_number] = kf

    tasks = []
    for generation_prompt in prompts:
        shot_number = generation_prompt.shot_number
        shot = shot_map.get(shot_number)
        start_keyframe = start_keyframe_map.get(shot_number)
        if not shot or not start_keyframe:
            logger.warning(f"⚠️ 镜头{shot_number}缺少shot或首帧数据，跳过")
            continue
        tasks.append(execute_single_video(start_keyframe, shot, generation_prompt, execution_context))

    logger.info(f"🔄 开始并发执行{len(tasks)}个视频生成任务...")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    logger.info(f"✅ 视频生成任务执行完成，获得{len(results)}个结果")

    # 协作式取消：若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
    await raise_if_cancelled()

    prompt_map = {p.shot_number: p for p in prompts}
    video_versions = []
    all_messages = []
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            if i < len(prompts):
                generation_prompt = prompts[i]
                shot_number = generation_prompt.shot_number
                shot = shot_map.get(shot_number)
                start_keyframe = start_keyframe_map.get(shot_number)
                logger.error(f"❌ 镜头{shot_number}: 执行异常 - {result}")
                _ar = user_option.aspect_ratio.value if user_option and getattr(user_option, "aspect_ratio", None) else None
                _res = user_option.resolution.value if user_option and getattr(user_option, "resolution", None) else None
                _vtool = user_option.video_generation_tool.value if user_option and getattr(user_option, "video_generation_tool", None) else None
                _prompt = prompt_map.get(shot_number)
                video_version = VideoGenerationVersion(
                    shot_number=shot_number,
                    is_bridge=start_keyframe.is_bridge if start_keyframe else False,
                    keyframe_url=start_keyframe.keyframe_url if start_keyframe else "",
                    keyframe_version_id=start_keyframe.version_id if start_keyframe else "",
                    keyframe_version_ids=[start_keyframe.version_id] if start_keyframe else [],
                    video_url="", duration=int(math.ceil(shot.duration)) if (shot and shot.duration) else 0,
                    i2v_prompt="", provider="", success=False, error_msg=str(result),
                    audio_segment_ids=start_keyframe.audio_segment_ids if start_keyframe else None,
                    detailed_shot_id=shot.uuid if shot else "",
                    aspect_ratio=_ar, resolution=_res, video_generation_tool=_vtool,
                    generation_mode=shot.generation_mode if shot else None,
                    video_tool_metrics=None, tool_duration_sec=None, tool_cost=None,
                    consistency_reason=_prompt.consistency_reason if _prompt else None,
                )
                messages = []
            else:
                logger.error(f"❌ 未知镜头: 执行异常 - {result}")
                continue
        else:
            video_version, messages = result
            all_messages.extend(messages)
        video_versions.append(video_version)

    logger.info(f"🎬 并发执行完成：{len([v for v in video_versions if v.success])}个成功，{len([v for v in video_versions if not v.success])}个失败")
    return all_messages, video_versions


def _spin_trace_format_video_eval_outcome(prompts: List[VideoGenerationPrompt], evaluation_result: Any) -> str:
    """供 VIDEO_SPIN_PROMPT_TRACE：逐镜输出 eval-fix 前后 i2v 与 issues。"""
    lines: List[str] = []
    evals = getattr(evaluation_result, "evaluations", None) or []
    for op in prompts:
        ed = next((e for e in evals if getattr(e, "shot_number", None) == op.shot_number), None)
        lines.append(f"\n=== shot {op.shot_number} ===")
        lines.append(f"original_i2v:\n{op.i2v_prompt}")
        if ed is not None:
            lines.append(f"needs_content_fix={getattr(ed, 'needs_content_fix', None)}")
            lines.append(f"content_issues_found={getattr(ed, 'content_issues_found', None)}")
            lines.append(f"fixed_i2v:\n{getattr(ed, 'fixed_prompt', '')}")
        else:
            lines.append("(no matching evaluation row; prompt unchanged)")
    return "".join(lines)


def _prompt_has_real_start_image(prompt: VideoGenerationPrompt) -> bool:
    """True when start_image_url is a non-empty URL (not reference_t2v synthetic '')."""
    url = getattr(prompt, "start_image_url", None)
    return bool(url and str(url).strip())


async def evaluate_and_fix_batch_prompts(
    prompts: List[VideoGenerationPrompt],
    keyframes_batch: List[KeyframeVersion],
    user_option: Optional[UserOption] = None,
    llm: Any = None,
    schema: Any = None,
    user_input: str = "",
    detected_language: Optional[str] = None,
) -> Tuple[List[BaseMessage], List[VideoGenerationPrompt]]:
    """第二步：按 I2V 首帧约束评估并修正 i2v_prompt（deep-agent + video-eval-director）。

    首尾帧需求保留上游 generate_batch_video_prompts 的设定，不由本步 LLM 改写。

    Skip when there is no real first frame to enforce against (reference_t2v / empty
    start_image_url), or when the tool path is reference-to-video (not I2V-hard).
    """
    del keyframes_batch, llm, schema, user_input  # unused; images come from prompts

    empty_prompts = [p for p in prompts if not p.i2v_prompt.strip()]
    if empty_prompts:
        logger.warning(f"⚠️ 发现{len(empty_prompts)}个空prompt，跳过评估修正")
        return [], prompts

    no_real_starts = not any(_prompt_has_real_start_image(p) for p in prompts)
    use_ref_t2v = bool(user_option and should_use_reference_to_video(user_option))
    if no_real_starts or use_ref_t2v:
        logger.info(
            "⏭️ 跳过 I2V 首帧 eval（no_real_starts=%s, reference_to_video=%s, shots=%d）",
            no_real_starts,
            use_ref_t2v,
            len(prompts),
        )
        return [], prompts

    logger.info(f"🔍 第二步：评估并修正{len(prompts)}个 video prompts（I2V 首帧约束 / deep-agent）")

    try:
        import uuid as _uuid
        from ....models.tool_enums import GenerationMode as _GenMode
        from app.services.agent.video.video_eval_stage import (
            evaluate_video_prompts_via_deep_agent,
            export_video_eval_inputs,
        )

        prompt_rows: List[Dict[str, Any]] = []
        image_urls: List[str] = []
        has_lipsync_shots = False
        for p in prompts:
            is_lipsync = getattr(p, "generation_mode", None) == _GenMode.LIPSYNC.value
            if is_lipsync:
                has_lipsync_shots = True
            prompt_rows.append({
                "shot_number": p.shot_number,
                "i2v_prompt": p.i2v_prompt,
                "has_start_image": _prompt_has_real_start_image(p),
                "has_end_image": bool(
                    getattr(p, "end_image_url", None)
                    and str(getattr(p, "end_image_url", "") or "").strip()
                ),
                "generation_mode": getattr(p, "generation_mode", None) or "",
            })
            if _prompt_has_real_start_image(p):
                image_urls.append(p.start_image_url)
            end_u = getattr(p, "end_image_url", None)
            if end_u and str(end_u).strip():
                image_urls.append(end_u)

        tid = f"veval_{_uuid.uuid4().hex[:8]}"
        rid = f"run_{_uuid.uuid4().hex[:8]}"
        paths = export_video_eval_inputs(
            thread_id=tid,
            run_id=rid,
            batch_id="b0",
            prompts=prompt_rows,
            detected_language=detected_language,
            has_lipsync_shots=has_lipsync_shots,
            image_urls=image_urls,
        )
        art, da_msgs = await evaluate_video_prompts_via_deep_agent(
            thread_id=tid,
            run_id=rid,
            input_paths=paths,
            detected_language=detected_language,
        )
        all_messages = list(da_msgs or [])
        evaluation_result = art

        evaluated_prompts: List[VideoGenerationPrompt] = []
        content_fixes_applied = 0
        for original_prompt in prompts:
            eval_data = next(
                (e for e in evaluation_result.evaluations if e.shot_number == original_prompt.shot_number),
                None,
            )
            if eval_data:
                end_image_url = original_prompt.end_image_url
                if original_prompt.needs_end_image and not end_image_url:
                    logger.warning(
                        f"⚠️ 镜头{original_prompt.shot_number}: 标记需要首尾帧但无尾帧 URL，将使用首帧模式"
                    )
                updated_prompt = VideoGenerationPrompt(
                    shot_number=original_prompt.shot_number,
                    i2v_prompt=eval_data.fixed_prompt,
                    start_image_url=original_prompt.start_image_url,
                    end_image_url=end_image_url,
                    needs_end_image=original_prompt.needs_end_image,
                    consistency_reason=original_prompt.consistency_reason,
                    generation_mode=getattr(original_prompt, "generation_mode", None),
                )
                evaluated_prompts.append(updated_prompt)
                if eval_data.needs_content_fix:
                    content_fixes_applied += 1
                    logger.info(f"🔧 镜头{original_prompt.shot_number}: 首帧约束修正")
                    logger.info(f"   问题: {', '.join(eval_data.content_issues_found)}")
            else:
                evaluated_prompts.append(original_prompt)

        logger.info(f"✅ Prompt评估完成（首帧约束）：{content_fixes_applied} 个镜头修正")
        log_video_spin_prompt_trace(
            "video_i2v_eval_fix_outcome",
            text=_spin_trace_format_video_eval_outcome(prompts, evaluation_result),
            log_context={"prompts_count": len(prompts), "content_fixes_applied": content_fixes_applied},
        )
        return all_messages, evaluated_prompts

    except Exception as e:
        logger.error(f"❌ Prompt修正过程出错: {e}")
        logger.info("📝 使用原始prompts继续执行")
        return [], prompts


async def generate_batch_videos(
    keyframes_batch: List[KeyframeVersion],
    shots_batch: List[DetailedShot],
    user_input: str,
    prev_shot: Optional[DetailedShot] = None,
    next_shot: Optional[DetailedShot] = None,
    user_option: Optional[UserOption] = None,
    llm: Any = None,
    story_outline: Optional[Any] = None,
    shared_semaphore: Optional[asyncio.Semaphore] = None,
    content_category: Optional[str] = None,
    lipsync_audio_url_map: Optional[Dict[int, str]] = None,
    detected_language: Optional[str] = None,
    hidden_style_description: Optional[str] = None,
    shot_character_ref_urls: Optional[Dict[int, List[str]]] = None,
    shot_character_ref_labels: Optional[Dict[int, List[dict]]] = None,
    skip_consistency_check: bool = False,
    log_context: Optional[Dict[str, Any]] = None,
) -> Tuple[List[BaseMessage], List[VideoGenerationVersion]]:
    """批量生成视频的异步函数（分三步：生成prompt + 评估修正 + 并发调用工具）
    
    Args:
        keyframes_batch: 关键帧列表（包含每个shot的首帧和尾帧）
        shots_batch: 镜头列表
        user_input: 用户输入
        prev_shot: 前一个镜头（用于连贯性）
        next_shot: 后一个镜头（用于连贯性）
        user_option: 用户选项
        llm: LLM模型
        story_outline: 故事大纲
        shared_semaphore: 共享信号量
        
    Returns:
        Tuple[List[BaseMessage], List[VideoGenerationVersion]]: 消息列表和视频生成结果列表
    """
    logger.info(f"🎬 批量生成{len(keyframes_batch)}个视频片段（三步式）")
    
    # 第一步：批量生成视频配置（prompt + 首帧等信息）
    prompt_messages, video_gen_configs = await generate_batch_video_prompts(
        keyframes_batch, shots_batch, user_input, prev_shot, next_shot, user_option, llm, story_outline,
        content_category=content_category,
        detected_language=detected_language,
        hidden_style_description=hidden_style_description,
        log_context=log_context,
    )
    
    # 第二步：I2V 首帧约束评估并修正 i2v_prompt
    fix_messages, evaluated_configs = await evaluate_and_fix_batch_prompts(
        video_gen_configs, keyframes_batch, user_option, llm,
        schema=BatchPromptEvaluationResult,
        user_input=user_input,
        detected_language=detected_language,
    )

    # 第三步：并发执行视频生成（使用共享的并发限制）
    gen_messages, video_versions = await execute_batch_video_generation(
        keyframes_batch, shots_batch, evaluated_configs, user_option, llm, shared_semaphore, user_input,
        lipsync_audio_url_map=lipsync_audio_url_map,
        detected_language=detected_language,
        shot_character_ref_urls=shot_character_ref_urls,
        shot_character_ref_labels=shot_character_ref_labels,
        skip_consistency_check=skip_consistency_check,
    )
    
    # 合并所有 messages（类似 keyframe 的实现）
    all_messages = prompt_messages + fix_messages + gen_messages
    logger.info(f"📨 返回 {len(all_messages)} 条消息（{len(prompt_messages)} 条prompt + {len(fix_messages)} 条fix + {len(gen_messages)} 条gen）")
    
    return all_messages, video_versions



async def _build_lipsync_audio_url_map(
    shots_data: List[DetailedShot],
    audio_transcription_uuids: List[str],
    music_generation_uuids: List[str],
) -> Dict[int, str]:
    """为 lipsync 镜头构建 shot_number → audio_url 映射。
    优先使用 music generation version 的 music_url（与 video_segments 一致）；无音乐时从 full_audio 按 segment 裁切。
    - 有音乐：segment 的 music_url = music_version.music_url（正确来源）
    - 无音乐：trim(full_audio_url, segment.start, segment.duration) 得到该转录片段的整段音频
    - 按 audio_segment_ids[0] 归组：组内为该 segment 对应的全部镜头（任意 generation_mode），按 shot_number
      排序；每镜 duration 推进 offset。仅 lipsync（及日后同类模式，可在此扩展判断）写入 trim 后的 audio_url。
    """
    from ....models.tool_enums import GenerationMode
    from ....utils.video_utils import trim_audio_clip
    from ....services.agent.utils.database_utils import get_audio_transcription_from_db
    from ....crud.video.video_audio import get_music_generation_versions_by_music_generation_ids
    
    audio_url_map: Dict[int, str] = {}
    if not audio_transcription_uuids:
        return audio_url_map
    
    audio_transcription = await get_audio_transcription_from_db(audio_transcription_uuids)
    if not audio_transcription:
        return audio_url_map
    
    seg_uuid_to_music_url: Dict[str, str] = {}
    if music_generation_uuids:
        all_versions = await get_music_generation_versions_by_music_generation_ids(music_generation_uuids)
        by_music_id: Dict[str, List] = {}
        for v in all_versions:
            by_music_id.setdefault(v.music_generation_id, []).append(v)
        for vers in by_music_id.values():
            latest = max(vers, key=lambda x: x.version_number)
            if latest.music_url and latest.audio_segment_ids:
                seg_uuid_to_music_url[latest.audio_segment_ids[0]] = latest.music_url
    
    seg_by_uuid = {seg.uuid: seg for seg in audio_transcription.segments}
    full_audio_url = audio_transcription.audio_url
    
    seg_to_shots: Dict[str, List[DetailedShot]] = {}
    for shot in shots_data:
        if not shot.audio_segment_ids:
            continue
        seg_to_shots.setdefault(shot.audio_segment_ids[0], []).append(shot)
    
    for seg_uuid, shots_in_seg in seg_to_shots.items():
        segment = seg_by_uuid.get(seg_uuid)
        if not segment:
            continue
        shots_in_seg.sort(key=lambda s: s.shot_number)
        if not any(s.generation_mode == GenerationMode.LIPSYNC.value for s in shots_in_seg):
            continue
        segment_audio_url = seg_uuid_to_music_url.get(seg_uuid)
        if not segment_audio_url:
            try:
                segment_audio_url = await trim_audio_clip(
                    full_audio_url, start_time=segment.start, duration=segment.duration
                )
            except Exception as e:
                logger.error(f"🎭 片段 {seg_uuid} 裁切失败: {e}")
                continue
        # lipsync 诊断：标记本段音频来源（suno music_url 时长由模型决定，不保证等于镜头总时长）
        # 并探测「段音频实际时长」，与转录段.duration / 组内 shot.duration 之和三方对比，
        # 用于定位 1对多 切割对不上（音频比镜头总时长短 → 靠后镜头切到空白）。
        _audio_src = "music_url" if seg_uuid_to_music_url.get(seg_uuid) else "full_audio_trim"
        _seg_audio_actual = -1.0
        try:
            _seg_audio_info = await msc.audio_info(segment_audio_url)
            _seg_audio_actual = float(_seg_audio_info.get("duration") or 0.0)
        except Exception as _e:
            logger.warning(
                "[lipsync音频诊断] 段 %s: audio_info 探测失败（不影响切割）: %s",
                str(seg_uuid)[:8], _e,
            )
        offset = 0.0
        _seg_dur = float(segment.duration) if segment.duration else 0.0
        _shots_dur_sum = sum(float(s.duration or 0) for s in shots_in_seg)
        logger.info(
            "[时长诊断] 转录段 %s: video_audio_segment.duration=%.6f | 组内 shot.duration 之和=%.6f | Δ(和−段)=%+.6f | 镜数=%d",
            str(seg_uuid)[:8] + "...",
            _seg_dur,
            _shots_dur_sum,
            _shots_dur_sum - _seg_dur,
            len(shots_in_seg),
        )
        logger.info(
            "[lipsync音频诊断] 段 %s 来源=%s | 段音频实际时长=%.3f | 转录段.duration=%.3f | 组内 shot.duration 之和=%.3f | Δ(实际−镜头和)=%+.3f%s",
            str(seg_uuid)[:8] + "...",
            _audio_src,
            _seg_audio_actual,
            _seg_dur,
            _shots_dur_sum,
            (_seg_audio_actual - _shots_dur_sum) if _seg_audio_actual >= 0 else 0.0,
            " ⚠️段音频比镜头总时长短，靠后镜头将切到空白/静音"
            if (_seg_audio_actual >= 0 and _seg_audio_actual + 0.05 < _shots_dur_sum)
            else "",
        )
        for shot in shots_in_seg:
            if shot.generation_mode == GenerationMode.LIPSYNC.value:
                try:
                    audio_url_map[shot.shot_number] = await trim_audio_clip(
                        segment_audio_url, start_time=offset, duration=shot.duration
                    )
                    logger.info(
                        "[时长诊断] 镜头%d: per-shot 音频 trim 请求 start=%.6f duration=%.6f (shot.duration) → %s",
                        shot.shot_number,
                        offset,
                        float(shot.duration or 0),
                        (audio_url_map[shot.shot_number] or "").split("/")[-1][:48],
                    )
                    # lipsync 诊断：探测切片后实际时长，并判断请求区间是否超出段音频边界
                    _req_end = offset + float(shot.duration or 0)
                    _shot_audio_actual = -1.0
                    try:
                        _shot_audio_info = await msc.audio_info(audio_url_map[shot.shot_number])
                        _shot_audio_actual = float(_shot_audio_info.get("duration") or 0.0)
                    except Exception:
                        pass
                    logger.info(
                        "[lipsync音频诊断] 镜头%d: 来源=%s 切片请求[start=%.3f dur=%.3f end=%.3f] | 段音频实际总长=%.3f | 切后实际=%.3f | Δ(切后−请求)=%+.3f%s",
                        shot.shot_number,
                        _audio_src,
                        offset,
                        float(shot.duration or 0),
                        _req_end,
                        _seg_audio_actual,
                        _shot_audio_actual,
                        (_shot_audio_actual - float(shot.duration or 0)) if _shot_audio_actual >= 0 else 0.0,
                        " ⚠️请求区间超出段音频，实际切片音频不足"
                        if (_seg_audio_actual >= 0 and _req_end > _seg_audio_actual + 0.05)
                        else "",
                    )
                except Exception as e:
                    logger.error(f"🎭 镜头{shot.shot_number}: 音频裁切失败: {e}")
            offset += shot.duration
    
    return audio_url_map


async def _build_lipsync_audio_url_map_from_narrations(
    shots_data: List[DetailedShot],
    narration_uuids: List[str],
) -> Dict[int, str]:
    """Product Launch / narration-driven：从 TTS 旁白构建 lipsync 音轨映射。"""
    from ....models.tool_enums import GenerationMode
    from ....crud.video.video_audio import get_narration_versions_by_narration_ids

    audio_url_map: Dict[int, str] = {}
    if not narration_uuids:
        return audio_url_map

    versions = await get_narration_versions_by_narration_ids(narration_uuids)
    if not versions:
        return audio_url_map

    shot_num_to_uuid = {
        s.shot_number: s.uuid for s in shots_data if getattr(s, "shot_number", None) is not None
    }
    latest_by_shot: Dict[int, Any] = {}
    for v in versions:
        if not getattr(v, "success", False) or not getattr(v, "audio_url", None):
            continue
        sn = getattr(v, "shot_number", None)
        if sn is None:
            continue
        prev = latest_by_shot.get(sn)
        if prev is None or getattr(v, "version_number", 0) >= getattr(prev, "version_number", 0):
            latest_by_shot[sn] = v

    for shot in shots_data:
        if shot.generation_mode != GenerationMode.LIPSYNC.value:
            continue
        ver = latest_by_shot.get(shot.shot_number)
        if ver and ver.audio_url:
            audio_url_map[shot.shot_number] = ver.audio_url
            logger.info(
                "🎭 [narration lipsync] 镜头%d: audio_url=%s duration=%s",
                shot.shot_number,
                (ver.audio_url or "")[:60],
                getattr(ver, "duration", None),
            )
    return audio_url_map


async def video_generation_node(
    state: VideoAgentState, 
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any
) -> Union[VideoAgentState, Dict[str, Any]]:
    """视频片段生成节点（批量处理，支持上下文）
    
    ⚠️ 并发安全：创建独立的数据库会话，避免与其他并行任务冲突
    """
    try:
        # 获取必要的UUID
        story_outline_uuid = state.get("story_outline_uuid")
        keyframe_uuids = state.get("keyframe_uuids", [])
        shot_uuids = state.get("shot_uuids", [])
        user_input_data = state.get("user_input_data")
        user_input = user_input_data.user_input if user_input_data else ""
        
        if not story_outline_uuid:
            raise BusinessException(
                BusinessExceptionCode.VIDEO_ANALYSIS_UUID_MISSING,
                "缺少故事梗概UUID"
            )

        from .music_generation_service import should_skip_keyframe_pipeline_from_state
        from ....models.video_state import KeyframeVersion

        skip_keyframe_pipeline = should_skip_keyframe_pipeline_from_state(state)

        if not keyframe_uuids and not skip_keyframe_pipeline:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "缺少关键帧数据，无法生成视频"
            )
        if not keyframe_uuids and skip_keyframe_pipeline and not shot_uuids:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "缺少详细镜头数据，无法生成视频"
            )
        
        # ✅ 修复：按需创建连接，不依赖 context
        # 只读取数据时使用连接，读取完立即释放
        from ....models.database import AsyncSessionLocal
        
        # 读取数据（使用asyncpg CRUD）
        shots_data = await get_detailed_shots_from_db(shot_uuids) if shot_uuids else []
        if keyframe_uuids:
            keyframes_data = await get_keyframes_from_db(keyframe_uuids)
        else:
            # reference_t2v：无关键帧，用镜头合成占位 KeyframeVersion（空 URL），供现有 batch 逻辑复用
            def _synthetic_kf(shot) -> KeyframeVersion:
                return KeyframeVersion(
                    shot_number=shot.shot_number,
                    keyframe_url="",
                    t2i_prompt=getattr(shot, "scene_description", None) or "",
                    provider="none",
                    frame_index=0,
                    detailed_shot_id=getattr(shot, "uuid", None) or "",
                    scene_id=getattr(shot, "scene_id", None) or "",
                    storyboard_detail_id=getattr(shot, "storyboard_detail_id", None) or "",
                    audio_segment_ids=getattr(shot, "audio_segment_ids", None),
                )

            keyframes_data = [_synthetic_kf(s) for s in shots_data]
            logger.info(
                "🎥 reference_t2v：无关键帧，按 %d 个镜头合成占位 keyframe 后走 T2V+参考图",
                len(keyframes_data),
            )
        
        # 获取story_outline用于风格检测
        from ....services.agent.utils.database_utils import get_story_outline_from_db
        story_outline = await get_story_outline_from_db(story_outline_uuid)
        # 精选风格描述（电影写实等从 analysis 带出，供视频 prompt 使用）
        hidden_style_description = None
        analysis_uuid = state.get("analysis_uuid")
        if analysis_uuid:
            try:
                from ....services.agent.utils.database_utils import get_video_analysis_from_db
                _analysis = await get_video_analysis_from_db(analysis_uuid)
                hidden_style_description = _analysis.hidden_style_description
            except Exception:
                pass
        
        # generation_mode 已在 per_shot_generation_routing 节点（首帧修订之后、关键帧之前）分配并写回 DB
        
        # 创建shot mapping以提高查找效率
        shots_mapping = {shot.uuid: shot for shot in shots_data if hasattr(shot, 'uuid')}
        
        # 🎭 构建 lipsync audio URL 映射（shot_number → audio_url）
        from ....models.tool_enums import GenerationMode
        lipsync_audio_url_map: Dict[int, str] = {}
        _has_lipsync_shots = any(
            s.generation_mode == GenerationMode.LIPSYNC.value for s in shots_data
        )
        if _has_lipsync_shots:
            _at_uuids = state.get("audio_transcription_uuids", []) or []
            if _at_uuids:
                lipsync_audio_url_map = await _build_lipsync_audio_url_map(
                    shots_data,
                    _at_uuids,
                    state.get("music_generation_uuids", []),
                )
            else:
                lipsync_audio_url_map = await _build_lipsync_audio_url_map_from_narrations(
                    shots_data,
                    state.get("narration_uuids", []) or [],
                )
            logger.info(f"🎭 Lipsync 音频映射已构建: {len(lipsync_audio_url_map)} 个镜头有 audio_url")
        
        # 构建 shot_number → 角色参考图 URL 列表及 labels（name/type_label/description/appearance 等），供视频一致性校验
        shot_character_ref_urls: Dict[int, List[str]] = {}
        shot_character_ref_labels: Dict[int, List[dict]] = {}  # 与 shot_character_ref_urls 同 key，value 为每镜头的角色 label 列表
        if shots_data:
            from .keyframe_generation_service import _build_character_images_dict, get_character_ref_images, build_character_labels_for_shot
            user_id = state["user_id"]
            all_character_ids = set()
            for shot in shots_data:
                all_character_ids.update(shot.character_ids or [])
            if all_character_ids and user_id:
                character_images = await _build_character_images_dict(all_character_ids, user_id)
                # 视频参考图始终带 location。关键帧文生图默认关 venue（防部分模型把场所当巨人），
                # 但 Seedance T2V / 一致性校验需要场所锚点，否则场景会对不上。
                for shot in shots_data:
                    urls = await get_character_ref_images(
                        shot,
                        character_images,
                        db=None,
                        user_id=user_id,
                        model_limit=99,
                        use_venue_ref_image=True,
                    )
                    if urls:
                        shot_character_ref_urls[shot.shot_number] = urls
                    labels = build_character_labels_for_shot(
                        shot, character_images, exclude_location=False
                    )
                    if labels:
                        shot_character_ref_labels[shot.shot_number] = labels
                logger.info(
                    "🎭 角色参考图已构建: %s 个镜头有 character_ref_urls（含 location）",
                    len(shot_character_ref_urls),
                )
        
        logger.info(f"🎥 开始基于{len(keyframes_data)}个关键帧生成视频片段")
        
        # 批量处理策略：每次处理多个关键帧，使用LLM的parallel tool calling
        total_keyframes = len(keyframes_data)
        total_shots = 0  # 尽早赋值，供闭包 process_video_batch 使用，避免 UnboundLocalError
        
        # 从配置中获取批处理大小
        from ..utils.prompt_utils import CONCURRENCY_LIMITS
        batch_size = CONCURRENCY_LIMITS.get("video_batch_size", 4)
        
        logger.info(f"🎥 开始处理{total_keyframes}个关键帧的视频生成，每批{batch_size}个（批量prompt生成），必须全部处理完成")
        
        # ⭐ 按shot分组keyframes（每个shot可能有首帧和尾帧），并先算出 total_shots 供进度与分批使用
        shots_with_keyframes = {}
        for keyframe in keyframes_data:
            shot_num = keyframe.shot_number
            if shot_num not in shots_with_keyframes:
                shots_with_keyframes[shot_num] = []
            shots_with_keyframes[shot_num].append(keyframe)
        unique_shots = sorted(set(kf.shot_number for kf in keyframes_data))
        total_shots = len(unique_shots)
        logger.info(f"🎥 总共{total_shots}个镜头，{total_keyframes}个关键帧")
        
        # ♻️ 断点续跑幂等：跳过本 outline 下已成功生成视频的镜头，避免取消/恢复后重复生成
        already_done_video_uuids: List[str] = []
        pending_shot_numbers = unique_shots
        try:
            _done_videos = await get_completed_video_uuids_by_shot_number(
                story_outline_uuid, str(state.get("thread_id") or "")
            )
            if _done_videos:
                pending_shot_numbers = [sn for sn in unique_shots if sn not in _done_videos]
                already_done_video_uuids = [_done_videos[sn] for sn in unique_shots if sn in _done_videos]
                if len(pending_shot_numbers) < len(unique_shots):
                    logger.info(f"♻️ 视频断点续跑：跳过{len(unique_shots) - len(pending_shot_numbers)}个已完成镜头，剩余{len(pending_shot_numbers)}个待生成")
        except Exception as _skip_err:
            logger.warning(f"视频 skip-if-done 检查失败，按全量生成: {_skip_err}")
            already_done_video_uuids = []
            pending_shot_numbers = unique_shots
        already_done_video_shot_count = len(unique_shots) - len(pending_shot_numbers)
        
        # 批量生成视频
        all_video_generation_uuids = []
        all_messages = []
        processed_keyframes = 0
        iteration = 0
        
        # 🎯 报告初始进度（0%）：前端「镜头」步骤按镜头数展示，故用 total_shots；带 thread_id 供前端拉 video 接口展示部分结果
        await send_event_func(
            event_type=MessageType.VIDEO_GENERATION_PROGRESS,
            conversation_id=state["conversation_id"],
            extra_data={
                "completed": 0,
                "total": total_shots,
                "thread_id": state.get("thread_id"),
            },
            hidden=True,
            save_to_db=False
        )

        # 内容类别：从 state 获取（video_analysis 节点已写入）
        # video_analysis 已写入 user_option.content_category（enum），批次里传 str 用 .value
        content_category_for_video = state["user_input_data"].user_option.content_category.value

        # 将所有镜头分批（按shot分批，而不是按keyframe分批；仅分批待生成镜头）
        batches = []
        for i in range(0, len(pending_shot_numbers), batch_size):
            current_shot_numbers = pending_shot_numbers[i:i + batch_size]
            
            # 收集这批shot的所有keyframes
            current_keyframes_batch = []
            current_shots_batch = []
            for shot_num in current_shot_numbers:
                # 获取这个shot的所有keyframes（首帧+尾帧）
                shot_keyframes = shots_with_keyframes.get(shot_num, [])
                current_keyframes_batch.extend(shot_keyframes)
                # 获取shot信息（无 keyframe 时 shot 为 None，避免 UnboundLocalError）
                shot = shots_mapping.get(shot_keyframes[0].detailed_shot_id) if shot_keyframes else None
                current_shots_batch.append(shot)
            
            logger.info(f"📦 创建批次{i // batch_size + 1}: {len(current_shot_numbers)}个镜头, {len(current_keyframes_batch)}个关键帧")
            logger.info(f"   镜头编号: {current_shot_numbers}")
            
            # 获取上下文镜头（前一个和后一个）
            prev_shot: Optional[DetailedShot] = None
            next_shot: Optional[DetailedShot] = None
            
            # 前一个镜头（用于第一个镜头的连贯性）
            if i > 0:
                prev_shot_num = pending_shot_numbers[i - 1]
                prev_keyframes = shots_with_keyframes.get(prev_shot_num, [])
                if prev_keyframes:
                    prev_shot = shots_mapping.get(prev_keyframes[0].detailed_shot_id)
                logger.info(f"   prev_shot: 镜头{prev_shot.shot_number if prev_shot else 'None'}")
            
            # 后一个镜头（用于最后一个镜头的连贯性）
            if i + batch_size < len(pending_shot_numbers):
                next_shot_num = pending_shot_numbers[i + batch_size]
                next_keyframes = shots_with_keyframes.get(next_shot_num, [])
                if next_keyframes:
                    next_shot = shots_mapping.get(next_keyframes[0].detailed_shot_id)
                logger.info(f"   next_shot: 镜头{next_shot.shot_number if next_shot else 'None'}")
            else:
                logger.info(f"   next_shot: None (最后一个批次)")
            
            batches.append({
                'keyframes': current_keyframes_batch,
                'shots': current_shots_batch,
                'prev_shot': prev_shot,
                'next_shot': next_shot,
                # ⭐ 移除 next_keyframe：每个shot的尾帧已经在数据库中
                'batch_index': i // batch_size,
                'content_category': content_category_for_video,
                'detected_language': state.get("detected_language"),
                'hidden_style_description': hidden_style_description,
            })
        
        logger.info(f"🚀 创建了{len(batches)}个批次，总计{total_keyframes}个关键帧，开始并发处理")
        
        # 创建共享的信号量用于跨批次并发控制
        from ..utils.prompt_utils import get_concurrency_limit, add_random_delay
        shared_video_semaphore_limit = get_concurrency_limit("video_generation")
        shared_video_semaphore = asyncio.Semaphore(shared_video_semaphore_limit)
        
        logger.info(f"🎬 使用共享semaphore控制总并发：最多{shared_video_semaphore_limit}个视频同时生成")

        # ---- 初始化 VideoExecutionContext（预加载 prompt 模板 + LLM，全 shot 共享） ----
        user_option: Optional[UserOption] = user_input_data.user_option
        exec_ctx = await init_execution_context(
            user_option=user_option,
            user_input=user_input,
            shared_semaphore=shared_video_semaphore,
            lipsync_audio_url_map=lipsync_audio_url_map,
            detected_language=state.get("detected_language"),
            shot_character_ref_urls=shot_character_ref_urls,
            shot_character_ref_labels=shot_character_ref_labels,
        )

        # 用于跟踪已完成的镜头数量（asyncio.Lock 避免阻塞事件循环；已完成镜头计入起点，进度条不回退）
        completed_shots_lock = asyncio.Lock()
        completed_shots_count = already_done_video_shot_count

        # ---- 构建 keyframe 查找表（供 DB 保存使用） ----
        all_keyframes_by_shot: Dict[int, KeyframeVersion] = {}
        all_keyframe_ids_by_shot: Dict[int, List[str]] = {}
        for kf in keyframes_data:
            sn = kf.shot_number
            if sn not in all_keyframe_ids_by_shot:
                all_keyframe_ids_by_shot[sn] = []
            if kf.keyframe_uuid:  # reference_t2v 合成占位无 uuid，勿写入空 id
                all_keyframe_ids_by_shot[sn].append(kf.keyframe_uuid)
            if kf.frame_index == 0:
                all_keyframes_by_shot[sn] = kf

        _resilience_log_ctx_base: Dict[str, Any] = {
            "run_id": state.get("run_id"),
            "thread_id": state.get("thread_id"),
            "conversation_id": state.get("conversation_id"),
            "caller": "video_generation_batch_prompts",
        }

        # ================================================================
        # Phase A — 准备（prompt 生成 + 评估修正），按 batch 并发，as_completed
        # ================================================================
        async def prepare_batch(batch_info) -> Tuple[List[Tuple[KeyframeVersion, DetailedShot, 'VideoGenerationPrompt']], List[BaseMessage]]:
            """Steps 1+2: 批量 prompt 生成 + 评估修正，返回 shot configs + messages."""
            await add_random_delay()
            batch_index = batch_info['batch_index']
            current_keyframes_batch = batch_info['keyframes']
            current_shots_batch = batch_info['shots']
            prev_shot = batch_info['prev_shot']
            next_shot = batch_info['next_shot']

            logger.info(f"🎥 批次{batch_index + 1}: 准备阶段 — {len(current_shots_batch)}个镜头")

            _batch_resilience_ctx = {**_resilience_log_ctx_base, "batch_index": batch_index + 1}
            prompt_messages, video_gen_configs = await generate_batch_video_prompts(
                current_keyframes_batch, current_shots_batch,
                user_input, prev_shot, next_shot, user_option, llm=None,
                story_outline=story_outline,
                content_category=batch_info.get("content_category"),
                detected_language=batch_info.get("detected_language"),
                hidden_style_description=batch_info.get("hidden_style_description"),
                log_context=_batch_resilience_ctx,
            )

            fix_messages, evaluated_configs = await evaluate_and_fix_batch_prompts(
                video_gen_configs, current_keyframes_batch, user_option, llm=None,
                schema=BatchPromptEvaluationResult,
                user_input=user_input,
                detected_language=batch_info.get("detected_language"),
            )

            shot_map = {s.shot_number: s for s in current_shots_batch if s}
            start_kf_map = {kf.shot_number: kf for kf in current_keyframes_batch if kf.frame_index == 0}

            shot_configs = []
            for prompt_cfg in evaluated_configs:
                sn = prompt_cfg.shot_number
                shot = shot_map.get(sn)
                kf = start_kf_map.get(sn)
                if shot and kf:
                    shot_configs.append((kf, shot, prompt_cfg))
                else:
                    logger.warning(f"⚠️ 批次{batch_index + 1} 镜头{sn}缺少shot或首帧，跳过")

            logger.info(f"✅ 批次{batch_index + 1}: 准备完成 — {len(shot_configs)}个 shot configs")
            return shot_configs, prompt_messages + fix_messages

        # ================================================================
        # Phase B — 执行 + 落库 + 进度上报（per shot，as_completed）
        # ================================================================
        async def execute_and_save_shot(
            kf: KeyframeVersion, shot: DetailedShot, prompt: 'VideoGenerationPrompt',
        ) -> Tuple[str, VideoGenerationVersion, List[BaseMessage]]:
            """Execute one shot, save to DB, send progress event. Returns (video_uuid, version, messages)."""
            version, messages = await execute_single_video(kf, shot, prompt, exec_ctx)

            keyframe_ids = all_keyframe_ids_by_shot.get(shot.shot_number, [])
            first_kf = all_keyframes_by_shot.get(shot.shot_number, kf)

            video_uuid = await save_video_generation_to_db(
                video_segment_version=version, keyframe=first_kf,
                story_outline_uuid=story_outline_uuid,
                conversation_id=str(state["conversation_id"]),
                thread_id=str(state["thread_id"]),
                run_id=state["run_id"], user_id=state["user_id"],
                keyframe_ids=keyframe_ids,
            )

            status = "✅ 成功" if version.success else "⚠️ 失败"
            logger.info(f"{status} 镜头{shot.shot_number}: {video_uuid}")

            # 立即更新进度
            async with completed_shots_lock:
                nonlocal completed_shots_count
                completed_shots_count += 1
                current = completed_shots_count

            await send_event_func(
                event_type=MessageType.VIDEO_GENERATION_PROGRESS,
                conversation_id=state["conversation_id"],
                extra_data={"completed": current, "total": total_shots, "thread_id": state.get("thread_id")},
                hidden=True, save_to_db=False,
            )
            return video_uuid, version, messages

        # ---- Phase A: 并发准备所有批次，as_completed 流式输出 ----
        logger.info(f"🚀 Phase A: 开始准备{len(batches)}个批次（prompt+eval）")
        prepare_tasks = [asyncio.create_task(prepare_batch(b)) for b in batches]

        all_shot_tasks = []
        all_messages = []
        for coro in asyncio.as_completed(prepare_tasks):
            shot_configs, prep_messages = await coro
            all_messages.extend(prep_messages)
            for kf, shot, prompt in shot_configs:
                task = asyncio.create_task(execute_and_save_shot(kf, shot, prompt))
                all_shot_tasks.append(task)

        # ---- Phase B: 执行阶段，as_completed 逐 shot 落库+上报 ----
        logger.info(f"🚀 Phase B: {len(all_shot_tasks)}个 shot 已进入执行池")
        # 已完成镜头的视频uuid一并带上，保证下游拿到完整列表
        all_video_generation_uuids = list(already_done_video_uuids)
        all_video_versions = []
        for coro in asyncio.as_completed(all_shot_tasks):
            try:
                video_uuid, version, shot_messages = await coro
                all_video_generation_uuids.append(video_uuid)
                all_video_versions.append(version)
                all_messages.extend(shot_messages)
            except Exception as e:
                logger.error(f"❌ shot 执行异常: {e}")
                all_video_generation_uuids.append(None)
        
        logger.info(f"🎥 并发批量处理完成: 总共{len(batches)}个批次，{total_keyframes}个关键帧")
        
        # 使用LLM根据收集的messages生成完成消息
        from ....services.agent.utils.prompt_utils import generate_completion_message_stream
        
        detected_language = state.get("detected_language")
        user_message, completion_message = await generate_completion_message_stream(
            event_type=MessageType.VIDEO_SEGMENTS_GENERATED,
            messages=all_messages,  # 传入收集的react agent messages
            send_event_func=send_event_func,
            conversation_id=state.get("conversation_id"),
            lang=detected_language
        )
        
        # 收集completion message
        if completion_message:
            all_messages.append(completion_message)
        
        logger.info(f"📝 生成的用户消息: {user_message}")
        
        # 发送视频生成完成事件
        success_count = len([uuid for uuid in all_video_generation_uuids if uuid])
        await send_event_func(
            conversation_id=state["conversation_id"],
            event_type=MessageType.VIDEO_SEGMENTS_GENERATED,
            message=user_message,  # 使用LLM生成的消息
            extra_data={
                "video_generation_uuids": all_video_generation_uuids,
                "total_keyframes": total_keyframes,
                "success_count": success_count,
                "run_id": state.get("run_id"),
                "thread_id": state.get("thread_id")
            }
        )
        
        # 失败兜底：逐镜头检测，有失败即暂停并在对话告知（官方原因，不泄密）
        failed_items = []
        for v in all_video_versions:
            if v is not None and not getattr(v, "success", True):
                failed_items.append({
                    "index": getattr(v, "shot_number", None),
                    "raw_error": getattr(v, "raw_error_msg", None) or getattr(v, "error_msg", None),
                })
        # 执行异常导致的 None（无 version）也计为失败
        exception_failures = len([u for u in all_video_generation_uuids if u is None])
        for _ in range(exception_failures):
            failed_items.append({"index": None, "category": None})
        stage_failure = detect_stage_failure(
            "video",
            total=total_keyframes,
            failed_items=failed_items,
            lang=detected_language,
        )
        if stage_failure:
            await emit_stage_failure_event(send_event_func, stage_failure, conversation_id=state.get("conversation_id"))

        return {
            "video_generation_uuids": all_video_generation_uuids,
            "messages": all_messages,
            "stage_failure": stage_failure,
        }
        
    except BusinessException as e:
        logger.error(f"video_generation_node:业务异常 - {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"video_generation_node:未知异常 - {str(e)}")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"视频片段生成失败: {str(e)}"
        )
    # ✅ 连接已在 async with 块中自动释放，不需要手动关闭


