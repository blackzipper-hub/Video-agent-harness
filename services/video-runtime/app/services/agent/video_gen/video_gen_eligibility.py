"""
video_gen 规格守门（确定性兜底，不靠 LLM 拍脑袋）。

当前阶段只支持 SD2 全家桶（4 个 i2v 变体；t2v 是内部实现）。
判定"是否符合单模型规格"：模型 ∈ SD2 + duration ≤ 该变体单段最大时长。
不符合 → 路由层回退旧 `video`（Workflow）。
"""
import logging
from typing import Optional

from ....models.user_options import UserOption, VideoGenerationTool
from .video_gen_tools import SD2_VIDEO_TOOLS

logger = logging.getLogger(__name__)


def _max_duration_for_tool(tool: VideoGenerationTool) -> int:
    """该 SD2 变体单段最大时长（真值来源：video_tool_wrapper 的 chain）。失败兜底 15s。"""
    try:
        from ....tools.video.video_tool_wrapper import _get_video_chain
        chain = _get_video_chain(tool)
        durations = chain[0].supported_duration_seconds if chain else None
        if durations:
            return max(durations)
    except Exception as e:
        logger.debug(f"video_gen: 读取 {tool} 最大时长失败，兜底 15s: {e}")
    return 15


def fits_single_model(user_option: Optional[UserOption]) -> bool:
    """符合单模型直生规格 ⇔ 模型 ∈ SD2 全家桶 且 duration ≤ 该变体单段最大时长。

    配乐 / 对口型不影响判定（交给 SD2 处理）。AUTO / 非 SD2 / 超时长 → False（回退旧 video）。
    """
    if user_option is None:
        return False
    tool = getattr(user_option, "video_generation_tool", None)
    if tool not in SD2_VIDEO_TOOLS:
        return False
    duration = getattr(user_option, "duration", None)
    if duration is None:
        return True  # 未指定时长，按默认（规格内）
    return int(duration) <= _max_duration_for_tool(tool)
