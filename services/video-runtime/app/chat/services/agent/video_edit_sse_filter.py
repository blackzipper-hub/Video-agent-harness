"""P0-B：VCA 代理 VA 视频编辑 SSE 时的 NDJSON 事件过滤层。

**默认关闭**，由 ``settings.SHIELD_COMPANION_PROXY_SANITIZE_ENABLED`` 控制。关时保持原
字节级透传行为；开时逐行解析 SSE 事件并走同一套工具名/args/raw-result 白名单。

详见 ``Cuti-Agent-Learning/PROMPT_SECURITY_CUTI_ANALYSIS.md`` §8 P0-B。

事件 ``type`` 使用 ``video_edit_*`` 前缀（与 ``app.chat.services.agent.video_edit`` 对齐）。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# 与 Cuti-VideoAgent ``video_edit/tool_registry.TOOL_PUBLIC_REGISTRY`` 保持同步。
# 复制在 VCA 侧以免跨仓库 import；若 VA 新增工具，这里需同步维护。
_TOOL_PUBLIC_REGISTRY: Dict[str, Dict[str, Any]] = {
    "get_project_status":    {"key": "tool.project.inspect",      "public_fields": ["overall_status", "stage_progress"]},
    "get_artifact_detail":   {"key": "tool.artifact.inspect",     "public_fields": ["artifact_type", "shot_number", "version_count"]},
    "regenerate_keyframes":  {"key": "tool.keyframe.regenerate",  "public_fields": ["shot_numbers", "job_count"]},
    "regenerate_videos":     {"key": "tool.video.regenerate",     "public_fields": ["shot_numbers", "job_count"]},
    "regenerate_characters": {"key": "tool.character.regenerate", "public_fields": ["character_uuids", "job_count"]},
    "reassemble_video":      {"key": "tool.assembly.rebuild",     "public_fields": ["job_id"]},
    "continue_pipeline":     {"key": "tool.pipeline.continue",    "public_fields": ["next_stage"]},
    "select_version":        {"key": "tool.version.select",       "public_fields": ["artifact_type", "version_index"]},
    "update_music_prompt":   {"key": "tool.music.update",         "public_fields": ["music_uuid"]},
    "modify_outline":        {"key": "tool.outline.update",       "public_fields": ["fields_changed"]},
    "analyze_image":         {"key": "tool.inspect.still",        "public_fields": []},
    "analyze_video":         {"key": "tool.inspect.motion",     "public_fields": []},
}


def _public_tool_call(tool_name: Optional[str]) -> Dict[str, Any]:
    entry = _TOOL_PUBLIC_REGISTRY.get(tool_name or "")
    return {"message_key": entry["key"] if entry else "tool.running"}


def _public_tool_result(tool_name: Optional[str], raw: Any) -> Dict[str, Any]:
    entry = _TOOL_PUBLIC_REGISTRY.get(tool_name or "")
    if not entry:
        return {"message_key": "tool.done"}
    fields = entry.get("public_fields") or []
    data = raw if isinstance(raw, dict) else {}
    return {
        "message_key": entry["key"],
        "result": {k: data[k] for k in fields if k in data},
    }


def _public_error(log_id: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"message_key": "video_edit.internal_error"}
    if log_id:
        out["log_id"] = log_id
    return out


def sanitize_video_edit_sse_event(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """对一个 SSE 事件 dict 做白名单过滤；返回 None 表示该事件不对外下发。

    - video_edit_token / video_edit_done：保留（纯文本，上层 Output Rail 会再校验）。
    - video_edit_tool_call：去掉 tool/args，只保留 message_key。
    - video_edit_tool_result：按 public_fields 挑字段。
    - video_edit_error：不回传 str(e)，换为 message_key + log_id（生成在此处）。
    - 其它未知 type：保守起见丢弃。
    """
    kind = obj.get("type")
    if kind == "video_edit_token":
        c = obj.get("content")
        if not isinstance(c, str):
            return None
        return {"type": "video_edit_token", "content": c}

    if kind == "video_edit_done":
        c = obj.get("content")
        return {"type": "video_edit_done", "content": c if isinstance(c, str) else ""}

    if kind == "video_edit_tool_call":
        tool = obj.get("tool")
        return {"type": "video_edit_tool_call", **_public_tool_call(tool)}

    if kind == "video_edit_tool_result":
        tool = obj.get("tool")
        raw = obj.get("content")
        if isinstance(raw, str):
            try:
                raw_parsed = json.loads(raw)
                raw = raw_parsed
            except (TypeError, ValueError):
                raw = None
        return {"type": "video_edit_tool_result", **_public_tool_result(tool, raw)}

    if kind == "video_edit_error":
        log_id = str(uuid.uuid4())
        logger.warning(
            f"[shield] sanitize video_edit_error (log_id={log_id}): "
            f"{(obj.get('message') or '')[:500]}"
        )
        return {"type": "video_edit_error", **_public_error(log_id=log_id)}

    return None
