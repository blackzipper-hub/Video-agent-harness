"""
Per-tool 视频/口型规划配置（与 user_options.VideoGenerationTool 对齐）。

仅放链上 supported_duration_seconds 推不出的产品策略（如 scene_split_threshold_sec）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from app.models.user_options import (
    UserOption,
    VideoGenerationTool,
    resolve_effective_lipsync_tool,
    resolve_effective_video_tool,
    should_apply_lipsync_for_planning,
)


@dataclass(frozen=True)
class VideoToolPlanningProfile:
    """单个用户可选视频/口型工具的规划旋钮。"""

    scene_split_threshold_sec: Optional[float] = None


VIDEO_TOOL_PLANNING_PROFILES: Dict[VideoGenerationTool, VideoToolPlanningProfile] = {
    # 默认/旧 Seedance 链也抬高拆分阈值，避免 audio-driven 按 min=3 切碎
    VideoGenerationTool.POLLO_SEEDANCE: VideoToolPlanningProfile(scene_split_threshold_sec=8.0),
    VideoGenerationTool.SEEDANCE_V1_5: VideoToolPlanningProfile(scene_split_threshold_sec=8.0),
    VideoGenerationTool.SEEDANCE_2_I2V: VideoToolPlanningProfile(scene_split_threshold_sec=8.0),
    VideoGenerationTool.SEEDANCE_2_I2V_TURBO: VideoToolPlanningProfile(scene_split_threshold_sec=8.0),
    VideoGenerationTool.SEEDANCE_2_FAST_I2V: VideoToolPlanningProfile(scene_split_threshold_sec=8.0),
    VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO: VideoToolPlanningProfile(scene_split_threshold_sec=8.0),
}


def get_video_tool_planning_profile(tool: VideoGenerationTool) -> VideoToolPlanningProfile:
    return VIDEO_TOOL_PLANNING_PROFILES.get(tool, VideoToolPlanningProfile())


def resolve_scene_split_threshold(
    user_option: UserOption,
    *,
    duration_values: List[int],
) -> float:
    """解析本 run 的 scene split / 规划 prefer 阈值（秒）。

    用于 audio-driven 切分 **和** video-driven 成本/时长估计。
    显式 profile 优先；否则 preferred_planning_unit（偏 8s），禁止落到工具链 min=3。
    """
    from app.agent_config.duration import preferred_planning_unit

    fallback = float(preferred_planning_unit(duration_values))
    explicit: List[float] = []

    vt = resolve_effective_video_tool(user_option)
    v_prof = get_video_tool_planning_profile(vt)
    if v_prof.scene_split_threshold_sec is not None:
        explicit.append(float(v_prof.scene_split_threshold_sec))

    if should_apply_lipsync_for_planning(user_option):
        lt = resolve_effective_lipsync_tool(user_option)
        l_prof = get_video_tool_planning_profile(lt)
        if l_prof.scene_split_threshold_sec is not None:
            explicit.append(float(l_prof.scene_split_threshold_sec))

    if explicit:
        return float(min(explicit))
    return fallback


# Back-compat alias (audio-driven call sites)
def resolve_audio_driven_scene_split_threshold(
    user_option: UserOption,
    *,
    audio_driven_duration_values: List[int],
) -> float:
    return resolve_scene_split_threshold(
        user_option, duration_values=audio_driven_duration_values
    )
