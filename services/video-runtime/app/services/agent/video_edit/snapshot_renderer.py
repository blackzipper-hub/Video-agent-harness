"""
render_snapshot_for_llm — 把 ProjectSnapshot 渲染成 LLM 可读的文本。

对标 Claude Code 的 system-reminder 注入：每轮刷新，只出现在动态段。
"""
import logging
from typing import Optional

from ....models.project_snapshot import PhaseStatus, ProjectSnapshot, StageStats

logger = logging.getLogger(__name__)

# 状态 → Emoji 映射
_STATUS_ICONS = {
    PhaseStatus.NOT_STARTED: "⏳",
    PhaseStatus.IN_PROGRESS: "🔄",
    PhaseStatus.COMPLETED: "✅",
    PhaseStatus.PARTIAL: "⚠️",
    PhaseStatus.FAILED: "❌",
    "not_started": "⏳",
    "in_progress": "🔄",
    "completed": "✅",
    "partial": "⚠️",
    "failed": "❌",
}


def _icon(status: str) -> str:
    return _STATUS_ICONS.get(status, "❓")


def _render_stage(label: str, stats: Optional[StageStats]) -> str:
    """渲染单个阶段的一行摘要。"""
    if stats is None:
        return f"  {label}: ⏳ 未开始"

    status = stats.get("status", "not_started")
    icon = _icon(status)
    total = stats.get("total", 0)
    succeeded = stats.get("succeeded", 0)
    failed_items = stats.get("failed_items", [])
    versions_count = stats.get("versions_count", {})

    if status == "not_started":
        return f"  {label}: {icon} 未开始"

    line = f"  {label}: {icon} {succeeded}/{total}"

    if failed_items:
        shot_labels = ", ".join(f"shot_{s}" for s in failed_items)
        line += f"  失败: [{shot_labels}]"

    if versions_count:
        multi = [f"shot_{k}: {v}v" for k, v in sorted(versions_count.items())]
        line += f"  多版本: {', '.join(multi)}"

    return line


def render_snapshot_for_llm(
    snap: Optional[ProjectSnapshot],
    language: str = "zh",
) -> str:
    """把 ProjectSnapshot 渲染成 ≤500 token 的文本块，用于 system prompt 动态段。

    Args:
        snap: 项目快照，None 时返回占位文本。
        language: 渲染语言（当前仅 zh，预留 en 扩展）。

    Returns:
        可直接拼入 system prompt 的纯文本。
    """
    if snap is None:
        return "# 项目状态\n  尚无运行中的项目。"

    phase = snap.get("phase", "unknown")
    task_status = snap.get("task_status", "unknown")
    total_shots = snap.get("total_shots", 0)
    total_dur = snap.get("total_duration_sec", 0.0)
    pending_gate = snap.get("pending_gate")

    lines = [
        "# 项目状态",
        f"  阶段: {phase}",
        f"  任务状态: {task_status}",
        f"  总镜头数: {total_shots}  总时长: {total_dur:.1f}s",
        "",
    ]

    lines.append(_render_stage("大纲", snap.get("outline")))
    preview = snap.get("outline_preview") or {}
    if preview:
        title = (preview.get("title") or "").strip()
        theme = (preview.get("theme") or "").strip()
        desc = (preview.get("description") or "").strip()
        key_msg = (preview.get("key_message") or "").strip()
        style_tags = (preview.get("style_tags") or "").strip()
        if title:
            lines.append(f"    标题: {title}")
        if theme:
            lines.append(f"    主题: {theme}")
        if desc:
            lines.append(f"    描述: {desc}")
        if key_msg:
            lines.append(f"    核心信息: {key_msg}")
        if style_tags:
            lines.append(f"    风格标签: {style_tags}")
        lines.append(
            "    （改风格时对照以上文案：与新风冲突的字段一并放入 modify_outline fields）"
        )
    lines.append(_render_stage("角色", snap.get("characters")))
    lines.append(_render_stage("场景", snap.get("scenes")))
    lines.append(_render_stage("关键帧", snap.get("keyframes")))
    lines.append(_render_stage("视频", snap.get("videos")))
    # 旁白：无数据时不显示（当前 pipeline 不生成旁白）
    narration_stats = snap.get("narrations")
    if narration_stats and narration_stats.get("status") != "not_started":
        lines.append(_render_stage("旁白", narration_stats))
    lines.append(_render_stage("配乐", snap.get("music")))
    lines.append(_render_stage("片段", snap.get("segments")))
    lines.append(_render_stage("成片", snap.get("assembly")))

    if pending_gate:
        lines.append("")
        if str(pending_gate).startswith("failed_"):
            lines.append(
                f"  ⏸️ 阶段失败暂停: {pending_gate}"
                "（修复失败项后可 continue_pipeline）"
            )
        else:
            lines.append(f"  ⏸️ 等待确认: {pending_gate}")

    return "\n".join(lines)
