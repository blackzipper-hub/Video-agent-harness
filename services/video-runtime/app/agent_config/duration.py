"""
视频规划时长：离散秒数列表 + 场景拆分/规划 prefer。

设计（对齐 OpenMontage scene_plan）：
- **LLM** 写墙钟时长（scene.duration / script section windows）
- **程序** 只提供：API 可生成秒数表（clamp）+ prefer 提示（偏 8s）
- 禁止把 API min（常为 3/4）当成「推荐单场时长」

- 秒数列表：wrapper 链 supported_duration_seconds 求交
- prefer / split：video_tool_profiles.scene_split_threshold_sec，否则 preferred_planning_unit
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from app.agent_config.video_tool_profiles import (
    resolve_audio_driven_scene_split_threshold,
    resolve_scene_split_threshold,
)
from app.models.user_options import UserOption
from app.services.agent.utils.video_tool_duration_capabilities import (
    AUDIO_DRIVEN_PLANNING_DURATION_VALUES,
    VIDEO_DRIVEN_PLANNING_DURATION_VALUES,
    get_audio_driven_duration_values_for_user,
    get_video_driven_duration_values_for_user,
)


def get_audio_driven_duration_values(user_option: Optional[UserOption] = None) -> List[int]:
    """Audio-driven 规划用离散秒数：拆 scene、转录 max_segment、与 scene_structure 同源。"""
    if user_option is None:
        return list(AUDIO_DRIVEN_PLANNING_DURATION_VALUES)
    return get_audio_driven_duration_values_for_user(user_option)


def get_video_driven_duration_values(user_option: Optional[UserOption] = None) -> List[int]:
    """Video-driven 规划用离散秒数：大纲/场景约束、普通 I2V 成片选档。"""
    if user_option is None:
        return list(VIDEO_DRIVEN_PLANNING_DURATION_VALUES)
    return get_video_driven_duration_values_for_user(user_option)


def get_audio_driven_split_threshold(
    user_option: Optional[UserOption] = None,
    content_category: Optional[str] = None,
) -> float:
    """Audio-driven 场景拆分阈值（秒）。content_category 预留。"""
    del content_category  # reserved
    if user_option is None:
        return float(min(AUDIO_DRIVEN_PLANNING_DURATION_VALUES)) if AUDIO_DRIVEN_PLANNING_DURATION_VALUES else 5.0
    values = get_audio_driven_duration_values(user_option)
    return resolve_audio_driven_scene_split_threshold(
        user_option, audio_driven_duration_values=values
    )


def get_video_driven_split_threshold(user_option: Optional[UserOption] = None) -> float:
    """Video-driven 规划 prefer / 成本估算阈值（秒）。

    与 audio-driven 同源：profile（pollo/SD2=8）优先，禁止 min(API)=3。
    真正的单场时长仍由 LLM 在 scene 阶段设计，本值只作估计与 brief 提示。
    """
    if user_option is None:
        return float(preferred_planning_unit(VIDEO_DRIVEN_PLANNING_DURATION_VALUES))
    values = get_video_driven_duration_values(user_option)
    return resolve_scene_split_threshold(user_option, duration_values=values)


def preferred_planning_unit(allowed_durations: Optional[List[int]] = None) -> int:
    """章节/纠正重分配用的首选单元（偏 8s，贴近短剧对白场 / OM hero）。"""
    allowed = sorted({int(d) for d in (allowed_durations or []) if int(d) > 0})
    if not allowed:
        return 8
    for pref in (8, 10, 5, 6, 7, 9, 4):
        if pref in allowed:
            return pref
    return min(allowed, key=lambda d: (abs(d - 8), d))


def api_min_duration(allowed_durations: Optional[List[int]] = None) -> int:
    """工具链最小可生成秒数（clamp 下限，不是推荐场长）。"""
    allowed = sorted({int(d) for d in (allowed_durations or []) if int(d) > 0})
    return int(allowed[0]) if allowed else 3


def planning_duration_hints(
    user_option: Optional[UserOption] = None,
) -> Tuple[List[int], int, int]:
    """Return (allowed_durations, preferred_sec, api_min_sec) for video-driven briefs."""
    allowed = get_video_driven_duration_values(user_option)
    pref = int(get_video_driven_split_threshold(user_option)) if user_option else preferred_planning_unit(allowed)
    # keep preferred inside allowed when possible
    if allowed and pref not in allowed:
        pref = preferred_planning_unit(allowed)
    return allowed, pref, api_min_duration(allowed)


def scene_count_unit(allowed_durations: Optional[List[int]] = None) -> int:
    """软引导场数单元：偏 8s（OM 对白/蒙太奇甜区），不再用 5 把 30s 切成 6 场。"""
    return preferred_planning_unit(allowed_durations)


def short_drama_min_scenes(chapter_duration: float, count_unit: int = 8) -> int:
    """建议场景数（仅作 soft 引导，禁止程序强制拆镜）。

    OM 风格：更少、更密的场（约 5–11s），不是 3–4s 碎切。
    例：8s→1；12s→1–2；30s→3–4。
    """
    chapter = float(chapter_duration or 0)
    unit = max(6, int(count_unit or 8))
    if chapter < 10:
        return 1
    return max(1, int(round(chapter / unit)))


# 兼容旧名：语义已泛化为通用建议场数
suggested_min_scenes = short_drama_min_scenes


def allocate_durations_summing_to_target(
    target_duration: float,
    part_count: int,
    unit: int,
) -> List[float]:
    """将 target_duration 分给 part_count 段，优先按 unit 整数倍，总和精确等于 target，且不出现负数。

    例：target=30, parts=3, unit=5 → [10, 10, 10]
    例：target=30, parts=1, unit=5 → [30]
    """
    if part_count <= 0:
        return []
    target = float(target_duration)
    if part_count == 1:
        return [target]

    unit = max(1, int(unit))
    target_floor = int(target)
    frac = target - target_floor
    total_units = target_floor // unit
    rem_secs = target_floor - total_units * unit

    if total_units >= part_count:
        base_units = total_units // part_count
        extra_units = total_units % part_count
        durs = [
            float(base_units * unit + (unit if i < extra_units else 0))
            for i in range(part_count)
        ]
        durs[-1] += rem_secs + frac
    else:
        base = target / part_count
        durs = [base] * (part_count - 1)
        durs.append(target - sum(durs))

    for i in range(part_count - 1):
        if durs[i] < 0:
            durs[i] = 0.0
    durs[-1] = target - sum(durs[:-1])
    if durs[-1] < 0:
        pos = [max(0.0, d) for d in durs[:-1]]
        s = sum(pos)
        if s <= 0:
            even = target / part_count
            return [even] * (part_count - 1) + [target - even * (part_count - 1)]
        scale = (target * 0.999) / s if target > 0 else 0.0
        durs = [p * scale for p in pos]
        durs.append(target - sum(durs))
    return durs
