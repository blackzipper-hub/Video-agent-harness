"""Video Edit Agent 工具对外公开形状注册表。

目的：SSE 发给前端时，**不直接暴露**内部工具名与完整 args/result；
改为走此处的 message_key（前端通过 i18n key 展示）与公开字段白名单。

**默认不启用**：由 ``settings.SHIELD_COMPANION_SSE_PUBLIC_SHAPE_ENABLED`` 控制。
当关闭时，调用方仍可继续使用原始 tool/args/result；此模块不主动改变任何行为。

详见 ``Cuti-Agent-Learning/PROMPT_SECURITY_CUTI_ANALYSIS.md`` §8 P0-A。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# 内部工具名 -> {公开 message_key, 可对外返回的字段白名单}
#
# ⚠️ 现状说明（2026-04 审计）：
#   - 多数 video_edit tool 的 ``_arun`` 仍 ``return str``（人读文案）。
#   - 因此 ``public_fields`` 对 str 输出不会匹配到字段（raw_output 不是 dict）。
#   - 若未来某个 tool 改为 ``return {...}``，此处的 ``public_fields`` 会自动生效。
TOOL_PUBLIC_REGISTRY: Dict[str, Dict[str, Any]] = {
    "get_project_status":    {"key": "tool.project.inspect",     "public_fields": ["overall_status", "stage_progress"]},
    "get_artifact_detail":   {"key": "tool.artifact.inspect",    "public_fields": ["artifact_type", "shot_number", "version_count"]},
    "regenerate_keyframes":  {"key": "tool.keyframe.regenerate", "public_fields": ["shot_numbers", "job_count"]},
    "regenerate_videos":     {"key": "tool.video.regenerate",    "public_fields": ["shot_numbers", "job_count"]},
    "regenerate_characters": {"key": "tool.character.regenerate","public_fields": ["character_uuids", "job_count"]},
    "reassemble_video":      {"key": "tool.assembly.rebuild",    "public_fields": ["job_id"]},
    "continue_pipeline":     {"key": "tool.pipeline.continue",   "public_fields": ["next_stage"]},
    "select_version":        {"key": "tool.version.select",      "public_fields": ["artifact_type", "version_index"]},
    "update_music_prompt":   {"key": "tool.music.update",        "public_fields": ["music_uuid"]},
    "modify_outline":        {"key": "tool.outline.update",      "public_fields": ["fields_changed"]},
    "update_scene":          {"key": "tool.scene.update",        "public_fields": ["scene_number"]},
    "analyze_image":         {"key": "tool.inspect.still", "public_fields": []},
    "analyze_video":         {"key": "tool.inspect.motion", "public_fields": []},
}

# 未知工具的默认 message_key（兜底：永远不回传原始工具名）
_FALLBACK_RUNNING_KEY = "tool.running"
_FALLBACK_DONE_KEY = "tool.done"


def to_public_tool_call_payload(tool_name: str) -> Dict[str, Any]:
    """on_tool_start 事件对外 payload（不含 tool 原名、不含 args）。"""
    entry = TOOL_PUBLIC_REGISTRY.get(tool_name)
    return {"message_key": entry["key"] if entry else _FALLBACK_RUNNING_KEY}


def to_public_tool_result_payload(tool_name: str, raw_output: Any) -> Dict[str, Any]:
    """on_tool_end 事件对外 payload（按 public_fields 白名单挑字段）。"""
    entry = TOOL_PUBLIC_REGISTRY.get(tool_name)
    if not entry:
        return {"message_key": _FALLBACK_DONE_KEY}
    fields: List[str] = entry.get("public_fields") or []
    data = raw_output if isinstance(raw_output, dict) else {}
    return {
        "message_key": entry["key"],
        "result": {k: data[k] for k in fields if k in data},
    }


def public_error_payload(log_id: Optional[str] = None) -> Dict[str, Any]:
    """对外错误 payload：不暴露 str(e)/堆栈/路径。message_key 沿用历史 i18n key。"""
    out: Dict[str, Any] = {"message_key": "video_edit.internal_error"}
    if log_id:
        out["log_id"] = log_id
    return out
