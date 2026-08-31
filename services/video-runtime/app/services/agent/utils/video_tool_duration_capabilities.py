"""
规划用离散秒数：从 wrapper 链上每个 ToolInfo.supported_duration_seconds 聚合求交集。

单一事实来源在链配置（video_tool_wrapper / lipsync_tool_wrapper 的 _i2v）；
新增 I2V 模型时在对应 _i2v 上填 supported_duration_seconds 即可，勿再维护并行 ToolType 映射。

Sora（SORA_2 / SORA_2_PRO）为离散档 {4,8,12}，与其它模型连续区间求交会压扁规划（如只剩 4、8）；
故不参与此处交集，仍保留链上 ToolInfo；实际生成由 Sora 工具内 clamp/就近映射处理。

VIDEO_DRIVEN / AUDIO_DRIVEN（无 user_option）：模块载入时对**全产品**各 VideoGenerationTool 链求交（兼容旧逻辑）。

有 user_option 时：`get_*_duration_values_for_user` 仅对用户 **video_generation_tool** 对应链（+ 开口型时的 **lipsync** 链）求交。
scene split 阈值：`agent_config.video_tool_profiles` 可 per-tool 配置，不必等于 min(duration)。
"""
from __future__ import annotations

import logging
from typing import Dict, FrozenSet, Iterable, List, Set

from app.models.tool_enums import ToolType
from app.models.user_options import (
    LIPSYNC_CAPABLE_VIDEO_TOOLS,
    UserOption,
    VideoGenerationTool,
    resolve_effective_lipsync_tool,
    resolve_effective_video_tool,
    should_apply_lipsync_for_planning,
)
from app.services.tool_service import ToolInfo
from app.tools.video.lipsync_tool_wrapper import _get_lipsync_chain
from app.tools.video.video_tool_wrapper import _get_video_chain

logger = logging.getLogger(__name__)

_FALLBACK_SECONDS = [3, 4, 5, 6, 7, 8, 9, 10]

# 见模块 docstring：Sora 不参与「全体统一规划」交集。
_EXCLUDE_FROM_PLANNING_INTERSECTION: FrozenSet[ToolType] = frozenset(
    (ToolType.SORA_2, ToolType.SORA_2_PRO)
)


def _for_planning_intersection(by_type: Dict[ToolType, FrozenSet[int]]) -> Dict[ToolType, FrozenSet[int]]:
    return {t: s for t, s in by_type.items() if t not in _EXCLUDE_FROM_PLANNING_INTERSECTION}


def _merge_supported_seconds_by_type(infos: Iterable[ToolInfo]) -> Dict[ToolType, FrozenSet[int]]:
    out: Dict[ToolType, FrozenSet[int]] = {}
    for info in infos:
        s = info.supported_duration_seconds
        if s is None:
            logger.warning(
                "video_tool_duration_capabilities: ToolType %s 的 ToolInfo 无 supported_duration_seconds，已跳过（请在链上 _i2v 填写）",
                info.tool_type.value,
            )
            continue
        prev = out.get(info.tool_type)
        if prev is not None and prev != s:
            raise ValueError(
                f"同一 ToolType {info.tool_type!r} 出现不一致的 supported_duration_seconds: {prev!r} vs {s!r}"
            )
        out[info.tool_type] = s
    return out


def _intersect_sorted_seconds(by_type: Dict[ToolType, FrozenSet[int]]) -> List[int]:
    if not by_type:
        return list(_FALLBACK_SECONDS)
    sets = list(by_type.values())
    acc = set(sets[0])
    for fs in sets[1:]:
        acc &= set(fs)
    if not acc:
        logger.error(
            "video_tool_duration_capabilities: 工具整数秒交集为空 types=%s，回退 3–10",
            sorted(t.value for t in by_type),
        )
        return list(_FALLBACK_SECONDS)
    return sorted(acc)


def _supported_ranges_log_line(by_type: Dict[ToolType, FrozenSet[int]]) -> str:
    parts = []
    for t in sorted(by_type.keys(), key=lambda x: x.value):
        s = by_type[t]
        parts.append(f"{t.value}[{min(s)}..{max(s)}]")
    return " | ".join(parts)


def _log_planning_duration_compute() -> None:
    """模块载入时打一次点：便于核对 audio-driven 是否按 5 起及与 video-driven 差异。"""
    n_in = _for_planning_intersection(_NORMAL_BY_TYPE)
    a_in = _for_planning_intersection(_AUDIO_BY_TYPE)
    audio_only = set(a_in.keys()) - set(n_in.keys())
    logger.info(
        "video_tool_duration_capabilities: 规划求交排除 Sora: %s",
        [x.value for x in sorted(_EXCLUDE_FROM_PLANNING_INTERSECTION, key=lambda z: z.value)],
    )
    logger.info(
        "video_tool_duration_capabilities: VIDEO_DRIVEN 输入 %d 个 ToolType（排除 Sora 后）: %s",
        len(n_in),
        _supported_ranges_log_line(n_in),
    )
    logger.info(
        "video_tool_duration_capabilities: VIDEO_DRIVEN 交集 -> values=%s min=%s",
        VIDEO_DRIVEN_PLANNING_DURATION_VALUES,
        min(VIDEO_DRIVEN_PLANNING_DURATION_VALUES) if VIDEO_DRIVEN_PLANNING_DURATION_VALUES else None,
    )
    logger.info(
        "video_tool_duration_capabilities: AUDIO_DRIVEN 相对 VIDEO 多出的 ToolType: %s",
        sorted(t.value for t in audio_only) if audio_only else [],
    )
    logger.info(
        "video_tool_duration_capabilities: AUDIO_DRIVEN 输入 %d 个 ToolType（排除 Sora 后）: %s",
        len(a_in),
        _supported_ranges_log_line(a_in),
    )
    logger.info(
        "video_tool_duration_capabilities: AUDIO_DRIVEN 交集 -> values=%s min=%s（get_audio_driven_split_threshold 用此 min）",
        AUDIO_DRIVEN_PLANNING_DURATION_VALUES,
        min(AUDIO_DRIVEN_PLANNING_DURATION_VALUES) if AUDIO_DRIVEN_PLANNING_DURATION_VALUES else None,
    )


def _flat_tool_infos(get_chain, video_tools: Iterable[VideoGenerationTool]) -> List[ToolInfo]:
    out: List[ToolInfo] = []
    for vt in video_tools:
        out.extend(get_chain(vt))
    return out


def _all_normal_i2v_chain_tool_types() -> FrozenSet[ToolType]:
    tools = list(VideoGenerationTool)
    types: Set[ToolType] = set()
    for info in _flat_tool_infos(_get_video_chain, tools):
        types.add(info.tool_type)
    return frozenset(types)


def _all_lipsync_chain_tool_types() -> FrozenSet[ToolType]:
    types: Set[ToolType] = set()
    for vt in LIPSYNC_CAPABLE_VIDEO_TOOLS:
        for info in _get_lipsync_chain(vt):
            types.add(info.tool_type)
    for info in _get_lipsync_chain(None):
        types.add(info.tool_type)
    return frozenset(types)


_NORMAL_I2V_INFOS: List[ToolInfo] = _flat_tool_infos(_get_video_chain, list(VideoGenerationTool))
_LIPSYNC_EXTRA_INFOS: List[ToolInfo] = []
for vt in LIPSYNC_CAPABLE_VIDEO_TOOLS:
    _LIPSYNC_EXTRA_INFOS.extend(_get_lipsync_chain(vt))
_LIPSYNC_EXTRA_INFOS.extend(_get_lipsync_chain(None))

# video-driven：仅普通 I2V 链（无 lipsync wrapper）
_NORMAL_BY_TYPE = _merge_supported_seconds_by_type(_NORMAL_I2V_INFOS)
# audio-driven（口型规划等）：普通 I2V + lipsync 链并集再按 ToolType 合并后求交
_AUDIO_BY_TYPE = _merge_supported_seconds_by_type(_NORMAL_I2V_INFOS + _LIPSYNC_EXTRA_INFOS)

_NORMAL_I2V_TYPES: FrozenSet[ToolType] = _all_normal_i2v_chain_tool_types()
_LIPSYNC_CHAIN_TYPES: FrozenSet[ToolType] = _all_lipsync_chain_tool_types()
_AUDIO_PLANNING_TYPES: FrozenSet[ToolType] = _NORMAL_I2V_TYPES | _LIPSYNC_CHAIN_TYPES

VIDEO_DRIVEN_PLANNING_DURATION_VALUES: List[int] = _intersect_sorted_seconds(
    _for_planning_intersection(_NORMAL_BY_TYPE)
)
AUDIO_DRIVEN_PLANNING_DURATION_VALUES: List[int] = _intersect_sorted_seconds(
    _for_planning_intersection(_AUDIO_BY_TYPE)
)


def _intersect_duration_values_from_infos(infos: List[ToolInfo]) -> List[int]:
    """对一条（或合并后的）wrapper 链上的 ToolInfo 求规划用整数秒交集（排除 Sora）。"""
    by_type = _merge_supported_seconds_by_type(infos)
    return _intersect_sorted_seconds(_for_planning_intersection(by_type))


def get_video_driven_duration_values_for_user(user_option: UserOption) -> List[int]:
    """按用户选择的普通 I2V 链（含 fallback）求 VIDEO 规划离散秒数。"""
    vt = resolve_effective_video_tool(user_option)
    return _intersect_duration_values_from_infos(list(_get_video_chain(vt)))


def get_audio_driven_duration_values_for_user(user_option: UserOption) -> List[int]:
    """按用户 video 链 +（若开口型）口型链求 AUDIO 规划离散秒数。"""
    vt = resolve_effective_video_tool(user_option)
    infos: List[ToolInfo] = list(_get_video_chain(vt))
    if should_apply_lipsync_for_planning(user_option):
        lt = resolve_effective_lipsync_tool(user_option)
        infos.extend(_get_lipsync_chain(lt))
    return _intersect_duration_values_from_infos(infos)


_log_planning_duration_compute()
