"""
图像 Agent 状态与历史图片解析

Best Practice (LangGraph/LangChain):
- 以 messages 为单一数据源，从消息中解析出「所有可用图片」列表，用于：
  1. 构建 prompt（告知模型有哪些参考图）
  2. 设置 context.reference_image_urls，供 i2i 工具使用
- 多轮对话：用户上传 + 历史生成图都纳入，便于判断 t2i vs i2i。

多轮/多种来源的整理方式：
- 当前轮上传 (user_input_data.images) 优先排在前面。
- 历史按消息顺序：HumanMessage 中的 image_url → 用户当轮上传；ToolMessage 中的 image_url → 当轮生成；
  AIMessage 中的 Markdown ![](url) → 模型展示的生成图。通过 source (user_upload | generated) 与
  可选 tool_used (t2i | i2i) 区分，便于后续扩展（如只取「最近一轮生成图」）。
"""
import json
import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage

logger = logging.getLogger(__name__)

# Markdown 图片语法: ![alt](url)
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\s*\(\s*(https?://[^\s)]+)\s*\)", re.IGNORECASE)


@dataclass
class ImageArtifact:
    """单条图片记录（用于 state / metadata 表达）
    
    - source: 来源，便于区分「用户上传」与「历史生成」
    - tool_used: 若为生成图，可记录 t2i / i2i（从 ToolMessage 解析）
    """
    url: str
    source: str  # "user_upload" | "generated"
    tool_used: Optional[str] = None  # "t2i" | "i2i" | None
    turn_index: Optional[int] = None  # 可选：第几轮出现的（按消息顺序）


def _url_from_human_message(msg: HumanMessage) -> List[str]:
    """从 HumanMessage 中提取图片 URL（多模态 content）。"""
    urls: List[str] = []
    if not hasattr(msg, "content") or msg.content is None:
        return urls
    content = msg.content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                url = part.get("image_url") or {}
                if isinstance(url, dict):
                    u = url.get("url")
                else:
                    u = url
                if u and isinstance(u, str) and u.strip():
                    urls.append(u.strip().strip("'\""))
    return urls


def _url_from_tool_message(msg: ToolMessage) -> List[Tuple[str, Optional[str]]]:
    """从 ToolMessage 中提取图像生成结果的 image_url。
    
    工具返回多为 ImageGenerationResult.model_dump() 的 JSON，含 success, image_url 等。
    返回 (url, tool_used)，tool_used 从 name 推断（如 xxx_t2i / xxx_i2i）或 None。
    """
    result: List[Tuple[str, Optional[str]]] = []
    if not getattr(msg, "content", None):
        return result
    raw = msg.content
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return result
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # 尝试从字符串中提取 URL（如 "image_url: https://..."）
            m = re.search(r"https?://[^\s\]\)'\"]+", raw)
            if m:
                result.append((m.group(0).strip("'\""), None))
            return result
    else:
        return result

    if not isinstance(data, dict):
        return result
    url = data.get("image_url")
    if not url or not isinstance(url, str) or not url.strip():
        return result
    url = url.strip().strip("'\"")
    # 从 tool 的 name 推断 t2i / i2i（常见命名 *t2i* / *i2i*）
    tool_name = (getattr(msg, "name", None) or "") if hasattr(msg, "name") else ""
    tool_used: Optional[str] = None
    if "i2i" in tool_name.lower():
        tool_used = "i2i"
    elif "t2i" in tool_name.lower():
        tool_used = "t2i"
    result.append((url, tool_used))
    return result


def _urls_from_ai_message(msg: AIMessage) -> List[str]:
    """从 AIMessage 的 content 中提取 Markdown 图片 URL。"""
    urls: List[str] = []
    if not hasattr(msg, "content") or msg.content is None:
        return urls
    content = msg.content
    if isinstance(content, str):
        for m in MARKDOWN_IMAGE_PATTERN.finditer(content):
            urls.append(m.group(1).strip())
    return urls


def parse_image_artifacts_from_messages(
    messages: Optional[List[BaseMessage]],
    current_upload_urls: Optional[List[str]] = None,
) -> Tuple[List[ImageArtifact], List[str]]:
    """从对话消息中解析出所有图片 artifact 及扁平 URL 列表。
    
    Best Practice:
    - 以 messages 为单一数据源，不单独维护一份 image state，避免与消息不同步。
    - 当前轮用户上传的 URL 优先放在前面（current_upload_urls），再按历史顺序追加
      用户消息中的图、工具返回的生成图、AI 回复中的图。
    
    Args:
        messages: 历史消息列表（HumanMessage / AIMessage / ToolMessage）
        current_upload_urls: 本轮用户上传的图片 URL 列表（来自 user_input_data.images）
    
    Returns:
        (artifacts, all_urls):
        - artifacts: 带来源与可选 tool_used 的列表，便于后续扩展（如展示「第2轮生成的图」）
        - all_urls: 去重且保序的 URL 列表，用于 prompt 描述与 context.reference_image_urls
    """
    seen = set()
    artifacts: List[ImageArtifact] = []
    all_urls: List[str] = []

    def add_url(url: str, source: str, tool_used: Optional[str] = None, turn_index: Optional[int] = None):
        if not url or url in seen:
            return
        seen.add(url)
        artifacts.append(ImageArtifact(url=url, source=source, tool_used=tool_used, turn_index=turn_index))
        all_urls.append(url)

    # 1) 本轮上传的图（优先）
    if current_upload_urls:
        for u in current_upload_urls:
            add_url(u, "user_upload", turn_index=0)

    if not messages:
        return artifacts, all_urls

    turn = 0
    for msg in messages:
        if isinstance(msg, HumanMessage):
            turn += 1
            for u in _url_from_human_message(msg):
                add_url(u, "user_upload", turn_index=turn)
        elif isinstance(msg, ToolMessage):
            for u, tool_used in _url_from_tool_message(msg):
                add_url(u, "generated", tool_used=tool_used, turn_index=turn)
        elif isinstance(msg, AIMessage):
            for u in _urls_from_ai_message(msg):
                add_url(u, "generated", turn_index=turn)

    logger.info(
        f"📷 从消息中解析图片: 共 {len(all_urls)} 张 (user_upload={sum(1 for a in artifacts if a.source == 'user_upload')}, generated={sum(1 for a in artifacts if a.source == 'generated')})"
    )
    return artifacts, all_urls
