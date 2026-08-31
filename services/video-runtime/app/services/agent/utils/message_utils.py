"""
消息处理工具：消息提取与序列化等通用功能。
"""
import logging
import json
from typing import Dict, Any, Optional, List, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _metrics_updates_from_artifact(artifact: Any) -> Dict[str, Any]:
    """从 ToolMessage.artifact（Pydantic 或 dict）提取 metrics 字段。"""
    def _get(obj: Any, key: str):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    updates = {}
    v = _get(artifact, "tool_duration_sec")
    if v is not None:
        updates["tool_duration_sec"] = v
    v = _get(artifact, "tool_cost")
    if v is not None:
        updates["tool_cost"] = v
    v = _get(artifact, "video_tool_metrics")
    if v is not None:
        updates["video_tool_metrics"] = v
    v = _get(artifact, "image_tool_metrics")
    if v is not None:
        updates["image_tool_metrics"] = v
    v = _get(artifact, "speech_tool_metrics")
    if v is not None:
        updates["speech_tool_metrics"] = v
    # lipsync 带声预览：LLM structured_response 常漏抄，必须以 artifact 为准
    v = _get(artifact, "preview_video_url")
    if v:
        updates["preview_video_url"] = v
    return updates


def patch_tool_metrics_from_last_tool_message(
    messages: List[Any],
    structured_response: T,
) -> T:
    """始终以最后一条 ToolMessage.artifact 的 metrics 为准（wrapper 使用 response_format='content_and_artifact'），
    避免 LLM 传错 tool_duration_sec/tool_cost/*_tool_metrics；artifact 有则覆盖 structured_response。

    同时回填 preview_video_url（lipsync 去音轨前带声预览），避免 LLM 漏抄导致落库丢失。
    """
    if structured_response is None:
        return structured_response
    last_tool = next((m for m in reversed(messages) if getattr(m, "type", None) == "tool"), None)
    if not last_tool:
        return structured_response

    artifact = getattr(last_tool, "artifact", None)
    if artifact is None:
        return structured_response

    updates = _metrics_updates_from_artifact(artifact)

    def _get(obj: Any, key: str):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    for url_field in ("image_url", "video_url", "audio_url"):
        artifact_url = _get(artifact, url_field)
        sr_url = _get(structured_response, url_field)
        if artifact_url and sr_url and artifact_url != sr_url:
            logger.warning(
                "[patch_tool_metrics] %s 不一致（LLM 转录错误），以 artifact 为准: llm=%s, artifact=%s",
                url_field, sr_url, artifact_url,
            )
            updates[url_field] = artifact_url

    if not updates:
        return structured_response
    patched_urls = [
        f for f in ("image_url", "video_url", "audio_url", "preview_video_url") if f in updates
    ]
    logger.info(
        "[patch_tool_metrics] 以 ToolMessage.artifact 覆盖: tool_duration_sec=%s, tool_cost=%s, patched_urls=%s",
        updates.get("tool_duration_sec"),
        updates.get("tool_cost"),
        patched_urls or None,
    )
    return structured_response.model_copy(update=updates)


def extract_ai_message_json(result: Dict[str, Any]) -> Optional[str]:
    """从 create_react_agent 的结果中提取并序列化 AIMessage。"""
    if not result.get("messages"):
        return None
    try:
        for msg in reversed(result["messages"]):
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                return json.dumps({
                    "type": "AIMessage",
                    "content": msg.content,
                    "tool_calls": msg.tool_calls,
                    "additional_kwargs": getattr(msg, "additional_kwargs", {}),
                    "response_metadata": getattr(msg, "response_metadata", {}),
                }, ensure_ascii=False, default=str)
    except Exception as e:
        logger.warning(f"保存AIMessage失败: {e}")
    return None
