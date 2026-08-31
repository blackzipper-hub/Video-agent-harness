"""
消息总结工具：根据对话历史生成完成消息（Message 总结）。
迁移自 Cuti-VideoAgent chat_agent/services/agent/utils/prompt_utils.py。
供 Agent Router 或 workflow 调用方在任务完成后生成用户友好的总结消息。
"""
from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

logger = logging.getLogger(__name__)


def format_history_for_summarization(messages: List[BaseMessage]) -> str:
    """将消息列表格式化为「会议纪要」式文本，便于总结模型生成完成消息。

    Best Practice: 不直接给模型原始消息对象，而是给一份结构化描述，减少噪音、突出关键信息。

    Args:
        messages: 对话消息列表（HumanMessage / AIMessage / ToolMessage）

    Returns:
        格式化后的多行字符串，形如 [用户输入]: ... / [助手执行]: ... / [系统动作]: ... / [动作结果]: ...
    """
    from langchain_core.messages import ToolMessage

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


def _get_language_instruction(detected_language: Optional[str] = None) -> str:
    """返回语言强制要求的 XML 块（用于追加到 System 或 Human 消息内容末尾）。"""
    current_lang = (detected_language or "").strip() or "en"
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
    """为 system message 添加语言指示后缀（XML 格式）。"""
    suffix = _get_language_instruction(detected_language)
    if not system_content.endswith("\n"):
        system_content += "\n"
    return system_content + suffix


def apply_language_suffix_to_system_message_in_messages(
    messages: List[BaseMessage],
    detected_language: Optional[str] = None,
) -> None:
    """对每条 SystemMessage 和 HumanMessage 都追加语言要求（强化输出语言一致性）。原地修改 messages。"""
    for i, msg in enumerate(messages):
        if isinstance(msg, SystemMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            messages[i] = SystemMessage(content=add_language_suffix_to_system_message(content, detected_language))
        elif isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            messages[i] = HumanMessage(content=add_language_suffix_to_system_message(content, detected_language))


def _load_completion_message_template():
    """从本地 Mustache 文件加载完成消息模板。"""
    from langchain_core.prompts import ChatPromptTemplate

    base_dir = os.path.dirname(__file__)
    # chat_agent/services/agent/utils -> 项目根目录（4 级：utils->agent->services->chat_agent 的父目录）
    project_root = os.path.abspath(os.path.join(base_dir, "..", "..", "..", ".."))
    file_path = os.path.join(project_root, "prompts", "completion", "generate_completion_message.mustache")

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"完成消息模板不存在: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    if "## System Message" in content and "## Human Message" in content:
        _, rest = content.split("## System Message", 1)
        system_content, human_content = rest.split("## Human Message", 1)
        system_content = system_content.strip()
        human_content = human_content.strip()
        messages = [
            ("system", system_content),
            ("human", human_content),
        ]
    else:
        raise ValueError("模板须包含 ## System Message 与 ## Human Message")

    return ChatPromptTemplate.from_messages(messages, template_format="mustache")


def _create_completion_llm():
    """创建用于完成消息生成的 LLM（OpenAI）。"""
    from app.llm.openai_failover import FailoverChatOpenAI as ChatOpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("COMPLETION_MESSAGE_MODEL", "gpt-4.1-mini")
    return ChatOpenAI(
        model=model,
        temperature=0.7,
        timeout=180,
        api_key=api_key,
    )


async def generate_completion_message_stream(
    event_type: Any,
    messages: List[BaseMessage],
    send_event_func: Optional[Any] = None,
    conversation_id: Optional[int] = None,
    lang: Optional[str] = None,
) -> tuple[str, Optional[BaseMessage]]:
    """生成节点完成消息（支持流式输出）。

    Args:
        event_type: 事件类型（如 MessageType.STORY_OUTLINE_GENERATED），需有 .value 属性
        messages: 本次 node 的完整对话（input + output）
        send_event_func: 发送事件函数（用于 stream）
        conversation_id: 对话ID（用于 stream）
        lang: 语言代码（ISO 639-1）

    Returns:
        (user_message, completion_message): 用户消息文本和 AI 消息对象；失败时 completion_message 为 None。
    """
    from langchain_core.messages import AIMessage

    from ..base_agent import MessageType

    event_value = getattr(event_type, "value", str(event_type))
    current_lang = (lang or "en").strip() or "en"
    task_context = event_value

    try:
        prompt_template = _load_completion_message_template()
        agent_llm = _create_completion_llm()
    except Exception as e:
        logger.warning(f"加载完成消息模板或 LLM 失败: {e}，使用降级文案")
        fallback_msg = f"{task_context}完成"
        return fallback_msg, None

    try:
        formatted_history = format_history_for_summarization(messages)
        conversation_history = [formatted_history]

        prompt_messages = (await prompt_template.ainvoke({
            "event_type": event_value,
            "task_context": task_context,
            "conversation_history": conversation_history,
            "formatted_history": formatted_history,
            "language_suffix": "",
        })).messages

        apply_language_suffix_to_system_message_in_messages(prompt_messages, current_lang)

        from .prompt_utils import llm_chunk_content_to_text

        full_content: List[str] = []
        async for chunk in agent_llm.astream(prompt_messages):
            raw = chunk.content if hasattr(chunk, "content") else chunk
            content = llm_chunk_content_to_text(raw)
            if content:
                full_content.append(content)
                if send_event_func and conversation_id:
                    try:
                        await send_event_func(
                            event_type=MessageType.STREAMING_CHUNK,
                            conversation_id=conversation_id,
                            message=content,
                            extra_data={
                                "target_event": event_value,
                                "content_type": "text",
                            },
                        )
                    except Exception as stream_error:
                        logger.warning(f"流式事件发送失败: {stream_error}")

        final_content = "".join(full_content).strip()
        if not final_content:
            raise ValueError("completion stream produced empty text after chunk normalization")
        ai_message = AIMessage(content=final_content)
        return final_content, ai_message

    except Exception as e:
        logger.error(f"生成完成消息失败: {e}")
        fallback_msg = f"{task_context}完成"
        return fallback_msg, None
