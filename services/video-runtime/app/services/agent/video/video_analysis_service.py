"""
视频分析功能模块
负责视频需求分析节点的实现和相关功能
"""
import logging
import json
from typing import List, Optional, Dict, Any, Union, cast, Tuple
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from ....models.video_state import VideoAgentState, VideoAnalysisResult, AudioTranscription, AudioSegment, GenerationConfig, ContentCategory
from ....models.user_options import VideoGenerationTool, UserOption
from ....exceptions import BusinessException, BusinessExceptionCode
from ....crud.video.video_other import create_video_analysis
from ....crud.video.video_audio import (
    create_video_audio_transcription,
    create_video_audio_segment
)
from ....services.agent.base_agent import MessageType
from ..schemas import VideoContextSchema

from ....services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages, attach_images_to_messages
from prompts.prompt_config import PromptName, PROMPTS_CONFIG

from ....services.agent.utils.llm_resilience import StructuredResilienceKind, ainvoke_structured_resilient

logger = logging.getLogger(__name__)


def build_audio_info_section_for_analysis(
    audio_transcription: Optional[AudioTranscription],
    sections: Optional[List[Any]] = None,
) -> str:
    """构建音频信息部分用于视频分析（使用三层：Global + Section + Segment，与 build_audio_context_for_prompts 对齐）。"""
    if not audio_transcription:
        return ""
    from ....services.agent.utils.database_utils import build_audio_context_for_prompts
    ctx = build_audio_context_for_prompts(audio_transcription, sections)
    audio_type = "纯音乐（无歌词）" if audio_transcription.is_instrumental else "有歌词音频"
    content_label = "音乐整体描述" if audio_transcription.is_instrumental else "完整歌词"
    parts = [
        "**音频输入信息：**",
        f"- 音频类型: {audio_type}",
        f"- 总时长: {audio_transcription.duration:.1f}秒",
        f"- 识别语言: {audio_transcription.language}",
        f"- {content_label}: \"{audio_transcription.text}\"",
    ]
    if ctx["global_block"]:
        parts.append("")
        parts.append(ctx["global_block"])
    # 与 outline 一致：有段落时用「曲式/段落与所属音频片段」层级，无段落时用扁平片段列表
    sections_with_segments = ctx.get("sections_with_segments_block", "").strip()
    if sections_with_segments:
        parts.append("")
        parts.append(sections_with_segments)
    else:
        parts.append("")
        segments_label = "音乐段落详情" if audio_transcription.is_instrumental else "音频片段详情"
        parts.append(f"- {segments_label}:")
        parts.append(ctx["segments_block"] if ctx["segments_block"] else "  （无）")
    return "\n".join(parts)


async def _analyze_video_requirements(
    user_input: str,
    images: list,
    audio_transcription: Optional[AudioTranscription],
    target_duration: Optional[int],
    detected_language: Optional[str] = None,
    history_messages: Optional[List[BaseMessage]] = None,
    user_content_category: Optional[str] = None,
    sections: Optional[List[Any]] = None,
    log_context: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Tuple[VideoAnalysisResult, List[BaseMessage]]:
    """分析视频需求的核心逻辑。user_content_category 为前端选择，LLM 可结合 user_input 覆盖后输出。sections 为曲式段落列表，与 transcription 一起构成完整音乐线。"""
    logger.info(f"🚀 开始分析视频需求")
    logger.info(f"📝 用户输入: {user_input}")
    logger.info(f"🖼️  图片数量: {len(images)}")
    logger.info(f"🎵 音频转录: {'有' if audio_transcription else '无'}")
    if sections:
        logger.info(f"🎵 曲式段落: {len(sections)} 个")
    logger.info(f"⏱️  目标时长: {target_duration}秒" if target_duration else "⏱️  目标时长: 未指定")
    if user_content_category:
        logger.info(f"📋 用户当前内容类别: {user_content_category}")

    if not (thread_id and run_id):
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "缺少 thread_id/run_id，无法走 analysis deep-agent",
        )

    from app.services.agent.video.analysis_stage import (
        export_analysis_inputs,
        generate_analysis_via_deep_agent,
    )
    from ....crud.video.curated_style_prompt import get_distinct_curated_style_categories

    style_cats = await get_distinct_curated_style_categories()
    history_summary = ""
    if history_messages:
        parts = []
        for m in history_messages[-12:]:
            role = getattr(m, "type", None) or m.__class__.__name__
            content = getattr(m, "content", "")
            if isinstance(content, list):
                content = " ".join(
                    (p.get("text") if isinstance(p, dict) else str(p)) for p in content
                )
            text = str(content or "").strip()
            if text:
                parts.append(f"{role}: {text[:500]}")
        history_summary = "\n".join(parts)
    input_paths = export_analysis_inputs(
        thread_id=thread_id,
        run_id=run_id,
        user_input=user_input,
        images=images,
        audio_transcription=audio_transcription,
        sections=sections,
        target_duration=float(target_duration) if target_duration else None,
        user_content_category=user_content_category,
        available_style_categories=style_cats,
        history_summary=history_summary,
    )
    _artifact, analysis_data, agent_messages = await generate_analysis_via_deep_agent(
        thread_id=thread_id,
        run_id=run_id,
        input_paths=input_paths,
        detected_language=detected_language,
    )
    logger.info("✅ analysis deep-agent 完成")
    return analysis_data, list(agent_messages or [])


async def video_analysis_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func
) -> Union[VideoAgentState, Dict[str, Any]]:
    """视频需求分析节点"""
    # 从 state 获取已识别的语言（在 agent_router 的 _analyze_input 中已识别）
    from ....models.tool_enums import DefaultValues
    detected_language = state.get("detected_language", DefaultValues.DEFAULT_LANGUAGE)
    user_input_data = state.get("user_input_data")
    user_input = user_input_data.user_input if user_input_data else ""
    images = user_input_data.images if user_input_data else []
    user_option = user_input_data.user_option if user_input_data else None
    
    # ✅ 从前置的 music_generation_node 获取实际音乐时长和 audio_transcription
    actual_target_duration = state.get("actual_target_duration")
    audio_transcription_uuids = state.get("audio_transcription_uuids", [])
    
    # ✅ 若有音频转录，加载转录 + 段落（三层信息供 analysis prompt 使用）
    audio_transcription = None
    audio_sections = []
    if audio_transcription_uuids:
        from ....services.agent.utils.database_utils import get_audio_transcription_with_sections
        audio_transcription, audio_sections = await get_audio_transcription_with_sections(audio_transcription_uuids)
        if audio_transcription:
            logger.info(f"🎵 使用前置阶段转录的音频: {len(audio_transcription.segments)} 个片段, {len(audio_sections)} 个段落")
    
    # 使用实际音乐时长（如果有），否则使用用户指定时长
    target_duration = actual_target_duration or (user_option.duration if user_option else None)
    logger.info(f"🎯 视频分析目标时长: {target_duration}秒 (来源: {'音乐实际时长' if actual_target_duration else '用户指定'})")
    
    # 不传入 state.messages：前置 music 等节点的内部 LLM 轨迹（含 GPT Responses
    # reasoning 块）与 Gemini 不兼容，且分析所需信息已在 user_input/转录/图里。
    user_content_category_str = (user_option.content_category.value if user_option and user_option.content_category else None)
    analysis_data, all_messages = await _analyze_video_requirements(
        user_input=user_input,
        images=images,
        audio_transcription=audio_transcription,
        sections=audio_sections,
        target_duration=target_duration,
        detected_language=detected_language,
        history_messages=None,
        user_content_category=user_content_category_str,
        thread_id=str(state.get("thread_id") or ""),
        run_id=str(state.get("run_id") or ""),
        log_context={
            "run_id": state.get("run_id"),
            "thread_id": state.get("thread_id"),
            "conversation_id": state.get("conversation_id"),
            "caller": "video_analysis",
        },
    )
    
    # content_category：LLM 可能返回 str 或 enum，统一转 enum 写入 user_option，后续节点直接用 enum（需要 str 时 .value）
    cc_enum = ContentCategory.from_value(analysis_data.content_category)
    # 无音频时禁止使用 Lip-Sync MV（没有音乐无法对嘴）；Product Launch / Short Drama 允许无 BGM
    if not audio_transcription or not getattr(audio_transcription, "segments", None):
        if cc_enum == ContentCategory.LIP_SYNC_MV:
            cc_enum = ContentCategory.DEFAULT
            logger.info("📋 无音频输入，content_category 强制为 Default（不使用 Lip-Sync MV）")
        elif cc_enum in (ContentCategory.PRODUCT_LAUNCH, ContentCategory.SHORT_DRAMA):
            logger.info("📋 %s：无 music 转录，保留 content_category", cc_enum.value)
    logger.info(f"📋 content_category: {cc_enum.value}")

    from ....models.user_options import UserOption
    new_uo = (user_input_data.user_option or UserOption.default()).model_copy(update={"content_category": cc_enum})
    updated_user_input_data = user_input_data.model_copy(update={"user_option": new_uo})

    conv_id = state.get("conversation_id")
    if conv_id is not None:
        try:
            from ....crud.conversation import async_merge_conversation_user_option_content_category

            await async_merge_conversation_user_option_content_category(
                int(conv_id),
                cc_enum.value,
                run_id=str(state.get("run_id") or "") or None,
            )
            logger.info("📋 content_category 已同步 DB (video_analysis): %s", cc_enum.value)
        except Exception as e:
            logger.warning("content_category 同步 DB (video_analysis) 失败: %s", e)

    # 🎯 从 state 获取生成配置（已在 music_generation_node 确定）
    generation_config = state.get("generation_config")
    
    # 保存到数据库并获取UUID（使用asyncpg CRUD）
    analysis_uuid = None
    
    # 风格存储约定：style_preferences 仅存 LLM 输出的短标签（如 ["Kpop风格","时尚动感"]），不做替换；
    # 精选库的长描述单独写入 hidden_style_description，仅下游生成用，不展示给前端。
    hidden_style_description = None
    curated_style_prompt_id = None
    from .agent_video_constants import ENABLE_CURATED_STYLE_MATCH
    if ENABLE_CURATED_STYLE_MATCH and analysis_data.matched_style_category:
        try:
            from ....crud.video.curated_style_prompt import get_random_curated_style_prompt_by_category
            prompt = await get_random_curated_style_prompt_by_category(analysis_data.matched_style_category)
            if prompt:
                hidden_style_description = prompt.description_en
                curated_style_prompt_id = prompt.uuid
                logger.info(f"🎬 精选风格大类 {analysis_data.matched_style_category}：已随机选用 uuid={prompt.uuid}")
        except Exception as e:
            logger.warning(f"精选风格库取值失败，仅不写 hidden_style_description，style_preferences 仍为 LLM 原样: {e}")
    elif analysis_data.matched_style_category and not ENABLE_CURATED_STYLE_MATCH:
        logger.info(
            "⏭️ ENABLE_CURATED_STYLE_MATCH=False，跳过精选风格库匹配（matched_style_category=%s）",
            analysis_data.matched_style_category,
        )
    # research + proposal in analysis.extra → DB additional_data
    analysis_additional = dict(getattr(analysis_data, "extra", None) or {})
    # 保存视频分析结果（style_preferences=LLM 短标签；hidden_style_description=精选库描述，不返前端）
    analysis_db = await create_video_analysis(
            run_id=state.get("run_id", ""),
            video_type=analysis_data.video_type,
            duration=analysis_data.duration,
            main_character=analysis_data.main_character,
            purpose=analysis_data.purpose,
            next_action=analysis_data.next_action,
            key_elements=analysis_data.key_elements,
            style_preferences=analysis_data.style_preferences,
            target_audience=analysis_data.target_audience,
            user_id=state.get("user_id", ""),
            conversation_id=str(state.get("conversation_id", "")),
            thread_id=state.get("thread_id", ""),
            content_category=cc_enum.value,
            hidden_style_description=hidden_style_description,
            curated_style_prompt_id=curated_style_prompt_id,
            additional_data=analysis_additional or None,
    )
    analysis_uuid = analysis_db.uuid
    logger.info(f"💾 视频分析结果已保存到数据库: {analysis_uuid}")
    
    # ✅ 音频转录已在 music_generation_node 处理，无需再保存
    
    # ✅ 使用 generate_completion_message_stream 生成 user_message（与 outline 等节点一致）
    from ....services.agent.utils.prompt_utils import generate_completion_message_stream
    user_message, completion_message = await generate_completion_message_stream(
        event_type=MessageType.VIDEO_ANALYSIS,
        messages=all_messages,
        send_event_func=send_event_func,
        conversation_id=state.get("conversation_id"),
        lang=detected_language
    )
    if completion_message:
        all_messages = list(all_messages) + [completion_message]
    logger.info(f"📝 生成的用户消息: {user_message}")
    await send_event_func(
        conversation_id=state["conversation_id"],
        event_type=MessageType.VIDEO_ANALYSIS,
        message=user_message,
        extra_data={
            "analysis_uuid": analysis_uuid,
            "run_id": state.get("run_id"),
            "thread_id": state.get("thread_id")
        }
    )
    
    # 返回时带上更新后的 user_input_data（content_category 已写入 user_option），后续节点从这里取
    return {
        "analysis_uuid": analysis_uuid,
        "user_input_data": updated_user_input_data,
        "generation_config": generation_config,
        "messages": all_messages,
        "detected_language": detected_language
    }