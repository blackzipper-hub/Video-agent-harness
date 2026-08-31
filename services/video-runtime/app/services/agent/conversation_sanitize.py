"""P0-D：``/conversation/detail`` 历史消息对外返回字段白名单。

默认不启用，由 ``settings.SHIELD_CONVERSATION_DETAIL_SANITIZE_ENABLED`` 控制。关闭时调用方
应继续使用原 ``message_to_dict`` 输出（行为完全不变）。

设计原则：**保守 + 向后兼容**
- ``event_type``: 不做 drop（短字符串，自身不含敏感信息；legacy / 未来新增的事件都不丢）。
- ``event_data``: 字段级白名单（敏感信息集中在结构化字段，默认丢弃白名单外的字段）。
- ``meta_data`` / ``additional_data``: 默认不下发（历史上常被塞调试信息）。

详见 ``Cuti-Agent-Learning/PROMPT_SECURITY_CUTI_ANALYSIS.md`` §8 P0-D。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# 与 ``video_edit/tool_registry.TOOL_PUBLIC_REGISTRY`` 同源；这里只需要内部名本身。
_INTERNAL_TOOL_NAMES = (
    "get_project_status",
    "get_artifact_detail",
    "regenerate_keyframes",
    "regenerate_videos",
    "regenerate_characters",
    "reassemble_video",
    "continue_pipeline",
    "select_version",
    "update_music_prompt",
    "modify_outline",
    "analyze_image",
    "analyze_video",
)
_TOOL_NAME_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(n) for n in _INTERNAL_TOOL_NAMES) + r")\b"
)

# 历史 DB 里 video_edit 工具事件的 content 形如：
#   "🔧 调用工具: regenerate_keyframes"
#   "📋 regenerate_keyframes 结果:\n...2000 字 tool 原始输出..."
# 这两种写死文案的前缀在 VA ``agent_router_endpoints.py`` 里，变更一定要同步。
_COMPANION_TOOL_CALL_RE = re.compile(r"^🔧\s*调用工具:\s*\S+\s*$")
_COMPANION_TOOL_RESULT_RE = re.compile(r"^📋\s*\S+\s*结果:\n", re.MULTILINE)


# event_data 中允许对外的键（超出集合的字段默认丢弃）
# 这里的键以「前端确实消费」为度量；超出即视为内部字段。
# 规则：新增允许字段时必须先确认前端使用，再加到这里。
_PUBLIC_EVENT_DATA_FIELDS = frozenset({
    # 路由 / 上下文（前端用于渲染与继续会话）
    "run_id", "thread_id", "conversation_uuid", "conversation_id",
    "agent_type", "selected_agent", "language",
    # 确认流（VCA 端发出，VA 端理论上不出现；保留键以防上游透传）
    "has_confirmed", "ready_for_confirmation",
    "confirmed_summary", "summary_input",
    # 流式/目标事件指示
    "target_event", "content_type", "source", "hidden",
    # video_edit SSE / 工具事件（走 public shape 后）
    "message_key", "result", "log_id",
    # post_regenerate 交互卡片（整棵 interaction 树原样保留）
    "schema_version", "interaction",
    # 通用展示字段
    "title", "message", "progress", "stage", "stage_progress",
    "completed", "total",
    "shot_number", "shot_numbers", "version_index", "version_count",
    "artifact_type", "character_uuids", "job_id", "job_count",
    "next_stage", "music_uuid", "fields_changed",
    # 产物 URL（前端必需）
    "url", "image_url", "video_url", "audio_url", "cdn_url",
    # 可见 timestamp
    "timestamp",
    # shield 本身的标记（便于前端忽略或展示拒答态）
    "shield_blocked", "shield_reason",
})

# 显式标注为敏感、任何时候都不应对外的字段（即使出现在 _PUBLIC_EVENT_DATA_FIELDS 里也先过这一层）
# 这些字段常见于 ERROR / 内部事件。
_FORBIDDEN_EVENT_DATA_FIELDS = frozenset({
    "error_message", "error_detail", "error_stack", "traceback", "stack",
    "tool", "args", "input",
    "llm_prompt", "llm_response_raw", "prompt", "system_prompt",
    "credentials", "api_key", "secret",
    "user_id",
})


def _filter_event_data(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k in _FORBIDDEN_EVENT_DATA_FIELDS:
            continue
        if k in _PUBLIC_EVENT_DATA_FIELDS:
            out[k] = v
    return out


def _sanitize_video_edit_tool_sse_content(event_type: Optional[str], content: Any) -> Any:
    """对 ``video_edit_tool_call`` / ``video_edit_tool_result`` 消息的 content 剥 tool 名 / 原始输出。

    目标场景：
      "🔧 调用工具: regenerate_keyframes"
      "📋 regenerate_keyframes 结果:\n<2000 字 tool 原始输出>"

    策略：
    - video_edit_tool_call：整条替换为 ``"🔧 调用工具..."``（不含具体工具名）。
    - video_edit_tool_result：仅保留 ``"📋 工具已执行"`` 前缀，**丢弃原始输出**。
    - 其它事件：原样返回（含 None / 非字符串）。
    """
    if not isinstance(content, str) or not event_type:
        return content
    if event_type == "video_edit_tool_call":
        if _COMPANION_TOOL_CALL_RE.match(content.strip()) or _TOOL_NAME_RE.search(content):
            return "🔧 调用工具..."
        return content
    if event_type == "video_edit_tool_result":
        if _COMPANION_TOOL_RESULT_RE.search(content) or _TOOL_NAME_RE.search(content):
            return "📋 工具已执行"
        return content
    return content


def sanitize_message_for_public(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """对单条 message dict 做白名单过滤。

    策略（保守）：
    - 永远返回一个消息 dict（不整条丢弃 legacy / 未知 event_type）。
    - 仅对 ``event_data`` 做字段级白名单 + 黑名单过滤。
    - 丢弃 ``meta_data`` / ``additional_data`` 两个调试字段。

    入参格式与 ``message_to_dict`` 输出一致（已 parse 成 dict）。
    """
    event_type = msg.get("event_type")
    out: Dict[str, Any] = {
        "id": msg.get("id"),
        "uuid": msg.get("uuid"),
        "conversation_id": msg.get("conversation_id"),
        "run_id": msg.get("run_id"),
        "role": msg.get("role"),
        "content": _sanitize_video_edit_tool_sse_content(event_type, msg.get("content")),
        "created_at": msg.get("created_at"),
        "sequence": msg.get("sequence"),
    }
    if event_type:
        out["event_type"] = event_type
    ed = msg.get("event_data")
    if ed:
        out["event_data"] = _filter_event_data(ed)
    # meta_data / additional_data 默认不下发
    return out
