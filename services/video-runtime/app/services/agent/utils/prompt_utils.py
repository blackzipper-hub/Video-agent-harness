"""
提示词构建工具函数
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Optional, List, Dict, Any, Union, TYPE_CHECKING, Type, TypeVar

if TYPE_CHECKING:
    from ....services.agent.base_agent import MessageType
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langchain_core.runnables import Runnable
from ....models.user_options import UserOption, VideoGenerationTool
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class RuleApplicability(str, Enum):
    """规则适用性枚举（deprecated stub — craft lives in kit/skills shared references）"""
    VIDEO = "video"
    IMAGE = "image"
    ALL = "all"


# Available style names for detect_video_style (craft lives in video-style-detection-director skill)
STYLE_NAMES = ["general", "kpop"]
# Thin key stub for callers that still import STYLE_EXAMPLES_DICT.keys()
STYLE_EXAMPLES_DICT = {name: {} for name in STYLE_NAMES}

# Deprecated: craft moved to kit/skills/stages/shared/references/content-moderation-*.md
CONTENT_MODERATION_RULES: Dict[str, Any] = {}


def _read_shared_reference(name: str) -> str:
    """Load a shared skill reference markdown file (content moderation, etc.)."""
    from app.services.agent.stage_runtime.paths import SKILLS_ROOT

    p = SKILLS_ROOT / "stages" / "shared" / "references" / name
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def attach_images_to_messages(
    messages: List[BaseMessage],
    image_urls: List[str],
    target_message_index: Optional[int] = None,
    prepend_text: bool = True,
    add_image_index_hint: bool = True,
    image_descriptions: Optional[List[str]] = None,
) -> List[BaseMessage]:
    """通用工具函数：将图片附加到消息列表中
    
    将图片 URL 列表附加到指定的消息中，转换为 LangChain 支持的多模态消息格式。
    这个函数会修改消息列表并返回，支持链式调用。
    
    Args:
        messages: LangChain 消息列表（通常来自 prompt_template.format_messages()）
        image_urls: 要附加的图片 URL 列表
        target_message_index: 要附加到的消息索引（None 表示自动查找最后一条 HumanMessage）
        prepend_text: 是否将文本放在图片前面（True）还是后面（False），默认 True
        add_image_index_hint: 是否在消息文本中添加图片索引提示（默认 True）
        image_descriptions: 可选，与 image_urls 等长的每张图说明（如 ["参考图1", "参考图2", "生成图"]），
            会在每张图前插入「【图N: 说明】」便于 LLM 区分；长度不一致时忽略
        
    Returns:
        List[BaseMessage]: 修改后的消息列表（原地修改并返回）
        
    Example:
        >>> messages = prompt_template.format_messages(...)
        >>> image_urls = ["https://example.com/img1.jpg", "https://example.com/img2.jpg"]
        >>> messages = attach_images_to_messages(messages, image_urls)
        >>> # 现在 messages 中的最后一条 HumanMessage 包含了文本和图片
        >>> # 并且文本末尾会自动添加图片索引提示
        
    Note:
        - 如果目标消息已经是多模态格式（content 是 list），会在现有内容基础上添加图片
        - 如果图片列表为空，直接返回原消息列表
        - 支持的图片格式取决于使用的 LLM（如 Gemini、GPT-4V 等）
        - 当 add_image_index_hint=True 时，会在文本末尾添加图片编号说明，便于 LLM 引用
        - 当 image_descriptions 长度与 image_urls 一致时，每张图前会加「【图N: 说明】」文本
    """
    # 如果没有图片，直接返回
    if not image_urls:
        logger.debug("没有图片需要附加，返回原消息列表")
        return messages
    
    # 过滤掉空的 URL，并去掉首尾引号（避免从 JSON/字符串解析带出 '..."...' 导致 invalid_image_url）
    def _normalize_image_url(u: str) -> str:
        return (u or "").strip().strip("'\"")
    valid_image_urls = [_normalize_image_url(url) for url in image_urls if url and (url or "").strip()]
    valid_image_urls = [u for u in valid_image_urls if u]
    if not valid_image_urls:
        logger.debug("没有有效的图片 URL，返回原消息列表")
        return messages
    
    # 确定目标消息索引
    if target_message_index is None:
        # 自动查找最后一条 HumanMessage
        target_message_index = None
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                target_message_index = i
                break
        
        if target_message_index is None:
            logger.warning("未找到 HumanMessage，无法附加图片")
            return messages
    
    # 验证索引有效性
    if target_message_index < 0 or target_message_index >= len(messages):
        logger.warning(f"消息索引 {target_message_index} 越界，无法附加图片")
        return messages
    
    target_message = messages[target_message_index]
    
    # 构建新的多模态内容
    new_content = []
    
    # 处理原有内容
    original_text = ""
    if isinstance(target_message.content, str):
        # 原内容是字符串
        original_text = target_message.content
    elif isinstance(target_message.content, list):
        # 原内容已经是多模态格式，提取文本部分
        for item in target_message.content:
            if isinstance(item, dict) and item.get("type") == "text":
                original_text = item.get("text", "")
                break
    
    # 如果需要添加图片索引提示，在文本末尾添加（有 image_descriptions 时带标签，与参考图总表一致）
    if add_image_index_hint and valid_image_urls:
        image_count = len(valid_image_urls)
        use_desc = image_descriptions and len(image_descriptions) == image_count
        index_hint = f"\n\n📋 参考图片编号（共 {image_count} 张）：\n"
        for i in range(image_count):
            if use_desc and image_descriptions[i]:
                index_hint += f"- 图片 {i + 1}: {image_descriptions[i]}\n"
            else:
                index_hint += f"- 图片 {i + 1}\n"
        index_hint += "\n注意：上述图片已按顺序附加在本消息中，编号从 1 开始。如需引用特定图片，请使用图片编号（整数）。"
        original_text = original_text + index_hint
    
    # 添加文本块
    if original_text:
        text_block = {"type": "text", "text": original_text}
        if prepend_text:
            new_content.append(text_block)
    
    # 是否使用每张图说明（长度一致时才使用）
    use_descriptions = (
        image_descriptions is not None
        and len(image_descriptions) == len(valid_image_urls)
    )
    # 添加图片（可选：每张图前插入说明文本）
    for i, url in enumerate(valid_image_urls):
        if use_descriptions and image_descriptions[i]:
            new_content.append({
                "type": "text",
                "text": f"【图{i + 1}: {image_descriptions[i]}】",
            })
        new_content.append({
            "type": "image_url",
            "image_url": {"url": url}
        })
    
    # 如果文本在后面，现在添加
    if not prepend_text and original_text:
        new_content.append({"type": "text", "text": original_text})
    
    # 创建新的 HumanMessage 替换原消息
    messages[target_message_index] = HumanMessage(content=new_content)
    
    logger.info(f"✅ 已将 {len(valid_image_urls)} 张图片附加到消息索引 {target_message_index}")
    
    return messages


def _get_language_instruction(detected_language: Optional[str] = None) -> str:
    """返回语言强制要求的 XML 块（用于追加到 System 或 Human 消息内容末尾）。"""
    from ....utils.i18n import get_current_language
    current_lang = (detected_language or get_current_language() or "").strip() or "en"
    return f"""
<language_requirements>
  <detected_language>{current_lang}</detected_language>
  <note>User/audio detected language is '{current_lang}' (ISO 639-1). You MUST use this language only.</note>

  <mandatory_rules>
    <rule priority="critical">Your entire response (all prompts, descriptions, text) MUST be 100% in '{current_lang}' language. No exceptions.</rule>
    <rule priority="critical">Do NOT use English unless detected_language is 'en'. If detected_language is zh, ja, ko, etc., output ONLY in that language.</rule>
    <rule priority="critical">If you produce any text in the wrong language, regenerate it in '{current_lang}' immediately.</rule>
  </mandatory_rules>

  <self_correction>If you notice you used the wrong language, stop and regenerate in '{current_lang}' only.</self_correction>
</language_requirements>"""


def add_language_suffix_to_system_message(system_content: str, detected_language: Optional[str] = None) -> str:
    """为 system message 添加语言指示后缀（XML 格式）。首先使用 detected_language，强烈要求输出仅使用该语言。
    优先使用传入的 detected_language（用户/音频检测语言），没有再用 ContextVar。
    """
    suffix = _get_language_instruction(detected_language)
    if not system_content.endswith("\n"):
        system_content += "\n"
    return system_content + suffix


def _apply_language_suffix_to_content(
    content: Union[str, List[Dict[str, Any]]],
    detected_language: Optional[str] = None,
) -> Union[str, List[Dict[str, Any]]]:
    """为 content 追加语言要求。若 content 为多模态 list（text + image_url 等），仅修改最后一个 text 块，保持 list 结构不变。"""
    suffix = _get_language_instruction(detected_language)
    if isinstance(content, str):
        return add_language_suffix_to_system_message(content, detected_language)
    if not isinstance(content, list):
        return content
    # 多模态 list：找到最后一个 type="text" 的块，在其 text 末尾追加 suffix
    new_content = []
    last_text_idx = -1
    for idx, part in enumerate(content):
        if isinstance(part, dict) and part.get("type") == "text":
            last_text_idx = len(new_content)
        new_content.append(part)
    if last_text_idx >= 0:
        block = new_content[last_text_idx]
        text = block.get("text", "")
        if text and not text.endswith("\n"):
            text += "\n"
        new_content[last_text_idx] = {**block, "text": text + suffix}
    else:
        prefix = "\n" if new_content else ""
        new_content.append({"type": "text", "text": prefix + suffix})
    return new_content


def apply_language_suffix_to_system_message_in_messages(
    messages: List[BaseMessage],
    detected_language: Optional[str] = None,
) -> None:
    """仅对 SystemMessage 追加语言要求（Human 为 facts JSON 时不可追加，避免破坏结构化 brief）。
    支持多模态 content（list of text/image_url）：仅修改最后一个 text 块。
    输出语言以 skill + facts.detected_language 为准。
    """
    for i, msg in enumerate(messages):
        if isinstance(msg, SystemMessage):
            content = getattr(msg, "content", None)
            new_content = _apply_language_suffix_to_content(content, detected_language)
            messages[i] = SystemMessage(content=new_content)


def get_content_from_structured_output_parse_error(exc: Exception) -> Optional[str]:
    """
    从 Agent ProviderStrategy 解析失败异常中尝试取出「最后一条 AI 回复」的文本 content。
    不同 LangChain 版本可能把 content 放在 output / response / ai_message 或 __cause__ 上。

    Returns:
        若能从 exc 或其 __cause__ 上找到 .content（str 或 list 中最后一个 type=text 的 text），返回该字符串；否则 None。
    """
    def _content_from_obj(obj: Any) -> Optional[str]:
        if obj is None:
            return None
        raw = getattr(obj, "content", None)
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            for block in reversed(raw):
                if isinstance(block, dict) and block.get("type") == "text":
                    return (block.get("text") or "") or ""
        return None

    for e in (exc, getattr(exc, "__cause__", None)):
        if e is None:
            continue
        for attr in ("output", "response", "ai_message", "message"):
            c = _content_from_obj(getattr(e, attr, None))
            if c is not None:
                return c
    return None


def extract_first_json_from_string(content: str) -> Optional[Dict[str, Any]]:
    """
    从可能含「多段 JSON」或「JSON + 尾部」的字符串中，只取第一个完整 JSON 对象并解析。
    用于 Agent ProviderStrategy 解析失败（Extra data）时的兜底：content 里多段时只取第一段。

    Returns:
        解析得到的 dict，若找不到合法 JSON 则返回 None。
    """
    if not content or not isinstance(content, str):
        return None
    content = content.strip()
    start = content.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(content[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


_T = TypeVar("_T")


def try_parse_structured_output_from_exception(exc: Exception, model_class: Type[_T]) -> Optional[_T]:
    """
    create_agent ProviderStrategy 解析失败（Extra data / Failed to parse structured output）时，
    从异常里取 content、取第一段 JSON、用 model_class 解析。通用，任意 create_agent 调用方可用。

    Returns:
        解析成功返回 model_class 实例，否则 None。
    """
    err = str(exc)
    if "Failed to parse structured output" not in err and "Extra data" not in err:
        return None
    content = get_content_from_structured_output_parse_error(exc)
    if not content:
        return None
    d = extract_first_json_from_string(content)
    if not d:
        return None
    try:
        return model_class.model_validate(d)
    except Exception:
        return None


async def invoke_agent_with_parse_fallback(
    agent: Any,
    inputs: Dict[str, Any],
    *,
    context: Optional[Dict[str, Any]] = None,
    result_model: Type[_T],
) -> Dict[str, Any]:
    """
    与 invoke_structured_llm_resilient 对应：对 create_agent 的 ainvoke 做包装。
    正常返回与 agent.ainvoke 同形：{"structured_response": ..., "messages": [...]}。
    若抛出的异常为 ProviderStrategy 解析失败（Extra data 等），则从异常中取第一段 JSON 解析为 result_model，
    返回 {"structured_response": parsed, "messages": []}，后续逻辑无需区分是正常还是兜底。
    """
    try:
        if context is not None:
            return await agent.ainvoke(inputs, context=context)
        return await agent.ainvoke(inputs)
    except Exception as e:
        fallback = try_parse_structured_output_from_exception(e, result_model)
        if fallback is not None:
            return {"structured_response": fallback, "messages": []}
        raise


async def invoke_structured_llm_resilient(
    runnable: Runnable,
    messages: List[BaseMessage],
    *,
    retry_config: Optional[Dict[str, Any]] = None,
    max_attempts: int = 2,
    retry_on_none: bool = True,
    logger_instance: Optional[logging.Logger] = None,
    run_config: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    ①② 统一：直接 LLM 的「异常重试 + 返回 None 重试」。
    - 若传 retry_config，先对 runnable 包 with_retry（异常重试），再循环 ainvoke。
    - 循环内：ainvoke 直到得到非 None 或用尽 max_attempts（None 重试）。
    - run_config：可选，传给 ainvoke(config=run_config)，用于 LangSmith 统一 trace（run_id/trace_id 等）。
    用于 video_consistency 等「直接 with_structured_output 的 LLM」调用。

    Returns:
        首次非 None 的返回值；若用尽次数仍为 None 则返回 None。
    """
    log = logger_instance or logger
    effective = runnable
    if retry_config:
        effective = runnable.with_retry(**retry_config)
    result = None
    for attempt in range(1, max_attempts + 1):
        result = await effective.ainvoke(messages, config=run_config or {})
        if result is not None:
            return result
        if not retry_on_none:
            return None
        log.warning("LLM 返回 None，第 %s 次尝试（共最多 %s 次）", attempt, max_attempts)
    return None


def get_image_moderation_rules(user_option: Optional[UserOption] = None) -> str:
    """Load IMAGE content-moderation craft from shared skill reference markdown.

    ``user_option`` retained for call-site compat; filtering is documented in the md
    (tool-specific sections). Skills should follow the reference directly.
    """
    _ = user_option
    return _read_shared_reference("content-moderation-image.md")


def get_video_moderation_rules(user_option: Optional[UserOption] = None) -> str:
    """Load VIDEO content-moderation craft from shared skill reference markdown.

    ``user_option`` retained for call-site compat; Sora-only sections are labeled
    in the md (apply when brief says video tool is Sora / Sora Pro).
    """
    _ = user_option
    return _read_shared_reference("content-moderation-video.md")


def get_video_tool_name(user_option: Optional[UserOption] = None) -> str:
    """获取视频生成工具的友好名称
    
    Args:
        user_option: 用户选项配置
        
    Returns:
        工具的友好名称
    """
    if not user_option:
        user_option = UserOption.default()
    
    video_tool = user_option.video_generation_tool
    
    tool_names = {
        VideoGenerationTool.AUTO: "Auto",
        VideoGenerationTool.POLLO_SEEDANCE: "WaveSpeed Seedance v1 Pro Fast",
        VideoGenerationTool.SEEDANCE_V1_5: "WaveSpeed Seedance v1.5 Pro (image-to-video-fast)",
        VideoGenerationTool.SEEDANCE_2_I2V: "WaveSpeed Seedance 2.0",
        VideoGenerationTool.SEEDANCE_2_I2V_TURBO: "WaveSpeed Seedance 2.0 Turbo",
        VideoGenerationTool.SEEDANCE_2_FAST_I2V: "WaveSpeed Seedance 2.0 Fast",
        VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO: "WaveSpeed Seedance 2.0 Fast Turbo",
        VideoGenerationTool.WAN_2_5: "WaveSpeed Wan 2.5",  # Alibaba Wan 2.5 I2V，可选音频，无尾帧
        VideoGenerationTool.WAN_2_6: "WaveSpeed Wan 2.6 Flash",  # Alibaba Wan 2.6 Flash，720p/1080p，3-15s
        VideoGenerationTool.LTX_2_3: "WaveSpeed LTX 2.3 Lipsync",  # 主流程 lipsync 默认，audio+image→video
        VideoGenerationTool.KLING_V2_AI_AVATAR_PRO: "Kwaivgi Kling V2 AI Avatar Pro",
        VideoGenerationTool.WAN_2_2_SPEECH_TO_VIDEO: "WaveSpeed WAN 2.2 Speech-to-Video",
        VideoGenerationTool.KLING_V3_STD: "WaveSpeed Kling v3.0 Std",
        VideoGenerationTool.HAPPYHORSE_1_0_I2V: "Alibaba HappyHorse 1.0",
        VideoGenerationTool.HAPPYHORSE_1_1_I2V: "Alibaba HappyHorse 1.1",
        VideoGenerationTool.OPENAI_SORA: "OpenAI Sora",
        VideoGenerationTool.OPENAI_SORA_PRO: "OpenAI Sora Pro"
    }
    
    return tool_names.get(video_tool, "Auto")


# 并发控制配置
# 说明：semaphore控制并发批次数，batch_size控制每批数量，内层API有独立的并发控制
# 实际并发 = semaphore × batch_size（理论值，实际受内层API的semaphore限制）
CONCURRENCY_LIMITS = {
    # 视频片段处理 - FFmpeg资源密集型操作
    "video_segments_processing": 5,  # 单任务处理，无批次

    # 视频组装下载 - S3下载 + FFmpeg操作
    "video_assembly_download": 10,   # 并发下载segment视频（I/O密集型，可以高并发）

    # 场景生成 - LLM调用
    "scene_generation": 10,          # 并发数
    "scene_batch_size": 5,           # 每批数量
    
    # 关键帧生成 - 图像生成API调用（三层控制）
    "keyframe_generation": 10,                    # 第1层：内部API并发（实际控制API调用数）
    "keyframe_batch_processing_semaphore": 10,    # 第2层：批次并发（控制同时处理的批次数）
    "keyframe_batch_size": 4,                     # 第3层：每批数量（10批×4个=理论40并发，实际受第1层限制为10）
    
    # 视频生成 - 视频生成API调用（三层控制）
    "video_generation": 10,                       # 第1层：内部API并发（全局共享，跨批次限制总并发）
    "video_batch_processing_semaphore": 10,       # 第2层：批次并发（控制同时处理的批次数，设为10允许批次并行）
    "video_batch_size": 4,                        # 第3层：每批数量（LLM prompt限制，不能太大）
    
    # 分镜详细生成 - LLM调用
    "storyboard_detail_generation": 10,  # 并发数
    "storyboard_detail_batch_size": 5,   # 每批数量

    # 分镜首帧合规修订 - LLM调用
    "storyboard_first_frame_revision": 10,  # 并发批次数
    "first_frame_revision_batch_size": 5,   # 每批镜头数

    # per-shot 生成路由（LLM）；每批镜头数与首帧修订对齐，便于上下文与 token 行为一致
    "per_shot_generation_routing": 6,
    "per_shot_routing_batch_size": 5,

    # 视频对嘴型 - 计算密集型操作
    "video_lipsync_processing": 5,   # 并发处理数
    "video_lipsync_selection": 10,   # 并发选择数
    
    # 视觉元素匹配 - LLM调用
    "visual_elements_matching": 10,  # 并发批次数
    
    # 关键帧反思 - VLM调用（视觉分析+LLM调用）
    "keyframe_reflection": 10,        # VLM评审并发数（全部关键帧并发评审）
    "keyframe_reflection_regen": 10,  # 重新生成并发数（评审完需要重生的并发重生）

    # FFmpeg 每进程线程数（多 agent/多并发时避免单进程占满 CPU；0=全部核心）
    "ffmpeg_threads": 2,
}

# 随机延迟范围配置（秒）
RANDOM_DELAY_RANGE = {
    "min_delay": 0.1,  # 最小延迟100ms
    "max_delay": 1.0,  # 最大延迟1s
}


async def add_random_delay():
    """添加随机延迟，避免并发请求过于集中"""
    delay = random.uniform(RANDOM_DELAY_RANGE["min_delay"], RANDOM_DELAY_RANGE["max_delay"])
    await asyncio.sleep(delay)


def get_concurrency_limit(service_name: str) -> int:
    """获取指定服务的并发限制
    
    Args:
        service_name: 服务名称，对应CONCURRENCY_LIMITS中的key
        
    Returns:
        并发限制数量，如果服务名不存在则返回默认值5
    """
    return CONCURRENCY_LIMITS.get(service_name, 5)


def format_history_for_summarization(messages: List[BaseMessage]) -> str:
    """将消息列表格式化为「会议纪要」式文本，便于总结模型生成完成消息。
    
    Best Practice: 不直接给模型原始消息对象，而是给一份结构化描述，减少噪音、突出关键信息。
    
    Args:
        messages: 对话消息列表（HumanMessage / AIMessage / ToolMessage）
        
    Returns:
        格式化后的多行字符串，形如 [用户输入]: ... / [助手执行]: ... / [系统动作]: ... / [动作结果]: ...
    """
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
    
    formatted_lines = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            if msg.content:
                formatted_lines.append(f"[用户输入]: {msg.content}")
        elif isinstance(msg, AIMessage):
            if msg.content:
                formatted_lines.append(f"[助手执行]: {msg.content}")
            if getattr(msg, "tool_calls", None):
                tool_names = [tc.get("name", "?") for tc in msg.tool_calls]
                formatted_lines.append(f"[系统动作]: 调用了工具 {', '.join(tool_names)}")
        elif isinstance(msg, ToolMessage):
            content_str = str(msg.content) if msg.content else ""
            summary = content_str[:100] + ("..." if len(content_str) > 100 else "")
            formatted_lines.append(f"[动作结果]: 任务已完成/报错（摘要: {summary}）")
        else:
            if hasattr(msg, "content") and msg.content:
                formatted_lines.append(f"[其他]: {str(msg.content)[:200]}")
    return "\n".join(formatted_lines) if formatted_lines else "(无对话内容)"


def llm_chunk_content_to_text(raw_chunk: Any) -> str:
    """将 LLM stream chunk.content 抽成纯文本。

    gpt-5.x / Responses API 常返回 list[dict]（含 type=reasoning / type=text）。
    若直接 ''.join 或塞给前端，会报 TypeError 或 UI 出现 [object Object]。
    """
    if raw_chunk is None:
        return ""
    if isinstance(raw_chunk, str):
        return raw_chunk
    if isinstance(raw_chunk, list):
        parts: List[str] = []
        for item in raw_chunk:
            text = llm_chunk_content_to_text(item)
            if text:
                parts.append(text)
        return "".join(parts)
    if isinstance(raw_chunk, dict):
        # reasoning / thinking 不进 UI；只抽 type=text / output_text 等可见块
        if raw_chunk.get("type") in ("reasoning", "thinking"):
            return ""
        text = raw_chunk.get("text") or raw_chunk.get("content")
        if isinstance(text, str):
            return text
        if isinstance(text, list):
            return llm_chunk_content_to_text(text)
        return ""
    text_attr = getattr(raw_chunk, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    if isinstance(text_attr, list):
        return llm_chunk_content_to_text(text_attr)
    return ""


async def generate_completion_message_stream(
    event_type: MessageType,
    messages: List[BaseMessage],
    send_event_func: Optional[Any] = None,
    conversation_id: Optional[int] = None,
    lang: Optional[str] = None
) -> tuple[str, Optional[BaseMessage]]:
    """生成节点完成消息（支持流式输出，内部用 VIDEO_COMPLETION_MESSAGE 取 LLM）
    
    Args:
        event_type: 事件类型（如 MessageType.STORY_OUTLINE_GENERATED）
        messages: 本次 node 的完整对话（input + output）
        send_event_func: 发送事件函数（用于 stream）
        conversation_id: 对话ID（用于 stream）
        lang: 语言代码（ISO 639-1）
        
    Returns:
        (user_message, completion_message): 用户消息和AI消息
        
    注意：
        - 使用 i18n 获取任务上下文描述
        - 使用 apply_language_suffix_to_system_message_in_messages 强制语言
        - 不要在消息中暴露供应商、代码、UUID 等内部细节
        - 支持流式输出到前端
    """
    from ....utils.i18n import get_i18n_message_async, get_current_language
    from prompts.prompt_loader import create_llm_from_model_config
    from prompts.prompt_config import PromptName, PROMPTS_CONFIG
    from app.orchestration.skills.prompt_context import (
        facts_human_message,
        skill_system_message,
    )
    from langchain_core.messages import AIMessage, SystemMessage, HumanMessage
    from ....services.agent.base_agent import MessageType
    
    # 获取当前语言
    current_lang = lang or get_current_language()
    
    # ✅ 使用 i18n 获取任务上下文
    task_context = await get_i18n_message_async(
        f"completion.{event_type.value}",
        default=event_type.value,
        lang=current_lang
    )
    
    try:
        formatted_history = format_history_for_summarization(messages)
        system = skill_system_message(
            "completion-message-director",
            lead="Follow completion-message-director.",
        )
        facts = {
            "event_type": event_type.value,
            "task_context": task_context,
            "conversation_history": formatted_history,
            "lang": current_lang,
        }
        prompt_messages = [
            SystemMessage(content=system),
            HumanMessage(content=facts_human_message(facts)),
        ]
        agent_llm = create_llm_from_model_config(
            PROMPTS_CONFIG[PromptName.VIDEO_COMPLETION_MESSAGE]["model_config"]
        )
        
        # 强制语言（System + Human 都强化）
        apply_language_suffix_to_system_message_in_messages(prompt_messages, current_lang)
        
        # ✅ 流式输出（chunk.content 可能是 Responses list[dict]，必须先抽成 str）
        full_content: List[str] = []
        
        async for chunk in agent_llm.astream(prompt_messages):
            raw = chunk.content if hasattr(chunk, "content") else chunk
            content = llm_chunk_content_to_text(raw)
            
            if content:
                full_content.append(content)
                
                # 发送流式 chunk 事件
                if send_event_func and conversation_id:
                    try:
                        await send_event_func(
                            event_type=MessageType.STREAMING_CHUNK,
                            conversation_id=conversation_id,
                            message=content,
                            extra_data={
                                "target_event": event_type.value,
                                "content_type": "text"
                            }
                        )
                    except Exception as stream_error:
                        logger.warning(f"流式事件发送失败: {stream_error}")
        
        # 构建最终消息
        final_content = "".join(full_content).strip()
        if not final_content:
            raise ValueError("completion stream produced empty text after chunk normalization")
        ai_message = AIMessage(content=final_content)
        
        return final_content, ai_message
        
    except Exception as e:
        logger.error(f"生成完成消息失败: {e}")
        # 降级：返回简单的完成消息
        fallback_msg = await get_i18n_message_async(
            f"{event_type.value}.default",
            default=f"{task_context}完成",
            lang=current_lang
        )
        return fallback_msg, None


def is_video_spin_prompt_trace_enabled() -> bool:
    """开启后打印与 I2V/首帧/转圈等规则相关的完整 prompt 文本，便于重跑日志分析。环境变量：VIDEO_SPIN_PROMPT_TRACE=1|true|yes|on"""
    v = os.environ.get("VIDEO_SPIN_PROMPT_TRACE", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def video_spin_prompt_trace_max_chars() -> int:
    """单段追踪正文最大字符数（默认 24000）；可用 VIDEO_SPIN_PROMPT_TRACE_MAX_CHARS 覆盖，夹在 4000～500000 之间。"""
    raw = os.environ.get("VIDEO_SPIN_PROMPT_TRACE_MAX_CHARS", "").strip()
    if raw.isdigit():
        return max(4000, min(int(raw), 500_000))
    return 24000


def _spin_trace_extract_text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("type")
                if t == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("text"), str):
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def format_messages_for_spin_trace(messages: Optional[List[Any]], max_total_chars: int) -> str:
    """将 LangChain messages 展平为可 grep 的纯文本（多模态块仅保留 text 部分）。"""
    if not messages:
        return ""
    chunks: List[str] = []
    total = 0
    max_total_chars = max(1000, max_total_chars)
    for i, m in enumerate(messages):
        role = getattr(m, "type", None) or ""
        name = type(m).__name__
        text = _spin_trace_extract_text_from_content(getattr(m, "content", None))
        header = f"\n--- [{i}] {name} type={role} text_len={len(text)} ---\n"
        piece = header + text
        if total + len(piece) > max_total_chars:
            chunks.append(f"\n... [VIDEO_SPIN_PROMPT_TRACE truncated at {max_total_chars} chars] ...\n")
            break
        chunks.append(piece)
        total += len(piece)
    return "".join(chunks)


def log_video_spin_prompt_trace(
    phase: str,
    *,
    messages: Optional[List[Any]] = None,
    text: Optional[str] = None,
    log_context: Optional[Dict[str, Any]] = None,
    extra_lines: Optional[List[str]] = None,
) -> None:
    """
    在 VIDEO_SPIN_PROMPT_TRACE 开启时写入 INFO 日志。前缀固定 [VIDEO_SPIN_PROMPT_TRACE]，便于从 performance / agent 日志中过滤。
    """
    if not is_video_spin_prompt_trace_enabled():
        return
    max_c = video_spin_prompt_trace_max_chars()
    header_parts: List[str] = [f"[VIDEO_SPIN_PROMPT_TRACE] phase={phase}"]
    if log_context:
        try:
            header_parts.append(f"context={json.dumps(log_context, ensure_ascii=False, default=str)}")
        except Exception:
            header_parts.append(f"context={log_context!r}")
    if extra_lines:
        for line in extra_lines:
            if line:
                header_parts.append(line)
    body = text if text is not None else ""
    if messages is not None:
        body = format_messages_for_spin_trace(messages, max_total_chars=max_c)
    full = "\n".join(header_parts) + "\n" + body
    chunk = 14000
    if len(full) <= chunk:
        logger.info("%s", full)
        return
    n = (len(full) + chunk - 1) // chunk
    for i in range(0, len(full), chunk):
        logger.info(
            "%s\n[VIDEO_SPIN_PROMPT_TRACE part %s/%s]",
            full[i : i + chunk],
            i // chunk + 1,
            n,
        )
