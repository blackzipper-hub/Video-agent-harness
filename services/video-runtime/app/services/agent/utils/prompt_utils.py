"""
提示词构建工具函数
"""
from __future__ import annotations

import json
import os
from typing import Optional, List, Dict, Any, Type, TypeVar

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langchain_core.runnables import Runnable
import logging

logger = logging.getLogger(__name__)


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
