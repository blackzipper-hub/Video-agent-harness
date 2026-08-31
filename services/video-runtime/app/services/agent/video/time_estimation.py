"""
VideoAgent 阶段耗时推算
======================

与 `cost_estimation.py` 同构：计费聚合「单价 × 数量」得积分，本模块聚合「单耗时 × 数量」得秒数。
**复用** `cost_estimation` 的 scene/shot 数量估算，保证时间与积分口径一致。

输出：每个**用户可见 step**（music/analysis/story_style/visual/scenes/storyboards/shots/final）的预计秒数 + 总计。

三种用法（对应 cost_estimation）：
1. 提交前/首包全量：estimate_video_agent_time(user_option, ...)
2. gate 暂停只算剩余：get_time_estimate_for_gate(state, gate_step)
3. 从 state 推断：get_time_estimate_from_state(state)

初版单耗时全部来自 `agent_config/timing_profile.py`（工具维度经验值），后续用 probe / 生产 tool_duration_sec 校准。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ....models.user_options import UserOption
from app.agent_config import timing_profile as tp
from app.agent_config.duration import (
    get_audio_driven_split_threshold,
    get_video_driven_split_threshold,
)
from .cost_estimation import (
    estimate_scene_and_shot_count,
    _resolve_video_tool_type,
    _image_tool_type_from_user_option,
    _lipsync_shot_ratio,
    _get_resolution,
    _keyframe_reflection_multiplier,
)
from ..utils.prompt_utils import get_concurrency_limit

logger = logging.getLogger(__name__)


# ── 响应模型 ──────────────────────────────────────────────

class TimeEstimateResult(BaseModel):
    """阶段耗时预估结果"""
    total_est_seconds: int = Field(description="总预估秒数（串行链路求和，并行分支取 max）")
    step_seconds: Dict[str, int] = Field(description="每个用户 step 的预估秒数")
    confidence: str = Field(default="low", description="low(首包,初始参数) | medium(gate,已知数量) | high(历史回归)")
    shot_count: int = Field(default=0, description="估算镜头数")
    estimated_shot_duration_sec: int = Field(default=5, description="估算单镜头成片时长（秒）")


# ── 单镜头/单帧耗时 ───────────────────────────────────────

def _video_unit_seconds(user_option: UserOption, clip_duration_sec: int) -> float:
    resolution = _get_resolution(user_option)
    has_end_image = bool(user_option.enable_continuity_mode)
    tool_type = _resolve_video_tool_type(user_option, has_end_image, resolution)
    return tp.get_video_unit_seconds(
        tool_type=tool_type,
        resolution=resolution,
        clip_duration_sec=clip_duration_sec,
        is_lipsync=False,
    )


def _lipsync_unit_seconds(user_option: UserOption, clip_duration_sec: int) -> float:
    resolution = _get_resolution(user_option)
    return tp.get_video_unit_seconds(
        tool_type=None,
        resolution=resolution,
        clip_duration_sec=clip_duration_sec,
        is_lipsync=True,
    )


def _image_unit_seconds(user_option: UserOption) -> float:
    resolution = _get_resolution(user_option)
    tool_type = _image_tool_type_from_user_option(user_option)
    return tp.get_image_unit_seconds(tool_type=tool_type, resolution=resolution)


# ── 主函数：全量耗时预估 ──────────────────────────────────

def estimate_video_agent_time(
    user_option: UserOption,
    scene_count: Optional[int] = None,
    shot_count: Optional[int] = None,
    character_count: Optional[int] = None,
    duration_sec: Optional[int] = None,
    is_audio_driven: bool = False,
    audio_duration_sec: Optional[float] = None,
    audio_segment_durations: Optional[List[float]] = None,
    content_category: Optional[str] = None,
    include_music: bool = True,
    confidence: str = "low",
) -> TimeEstimateResult:
    """
    推算 VideoAgent 各 step 耗时（秒）。

    图片/视频是「工具调用 + 串行 LLM/编排 overhead」两部分组成，原模型只算了工具部分，故 story_style/
    visual/storyboards/shots 全被低估。这里统一按「ceil(数量 / 真实并发) × (单产物 + 单产物 LLM) × 倍数 + 串行 overhead」：

    story_style_sec = OUTLINE_LLM + CHARACTER_LIST_LLM + ceil(角色数/并发) × 单图 × retry
    visual_sec      = VISUAL_ELEMENTS_LLM + ceil(角色数/并发) × 单图(融合) × retry
    storyboards_sec = STORYBOARD_PRE_LLM + ceil(keyframe数/并发) × (单图 + KEYFRAME_PROMPT_LLM) × ROUND × retry
    shots_sec       = VIDEO_PROMPT_OVERHEAD + ceil(shot数/并发) × 加权单镜头 × retry
    并发数全部取自 prompt_utils.get_concurrency_limit（与真正控制 semaphore 的 CONCURRENCY_LIMITS 同源）。
    music_sec       = Suno 单曲耗时（bgm_parallel 时与主链并行，不计入串行总时）
    """
    if duration_sec is None and user_option is not None:
        duration_sec = getattr(user_option, "duration", None)
    if content_category is None and user_option is not None:
        cc = getattr(user_option, "content_category", None)
        content_category = getattr(cc, "value", None) if cc is not None else None
    from ....models.tool_enums import ContentCategory
    is_product_launch = content_category == ContentCategory.PRODUCT_LAUNCH.value

    # Step 1: scene/shot（复用计费同一函数，1:1）
    scenes, shots = estimate_scene_and_shot_count(
        user_option=user_option,
        scene_count=scene_count,
        shot_count=shot_count,
        duration_sec=duration_sec,
        is_audio_driven=is_audio_driven,
        audio_duration_sec=audio_duration_sec,
        audio_segment_durations=audio_segment_durations,
        content_category=content_category,
    )
    shots = max(1, shots)

    # Step 1.5: 单镜头真实时长（基于 split threshold）
    if is_audio_driven:
        threshold = get_audio_driven_split_threshold(user_option, content_category)
    else:
        threshold = get_video_driven_split_threshold(user_option)
    est_shot_duration = max(3, int(threshold))

    # Step 2: keyframe 数量（首尾帧 + 反思倍数，与计费一致；Seedance2 reference_t2v → 0）
    from ....models.user_options import should_skip_keyframe_pipeline

    if should_skip_keyframe_pipeline(user_option):
        keyframe_count = 0
    else:
        base_keyframes = shots * (2 if user_option.enable_continuity_mode else 1)
        refl_mult = _keyframe_reflection_multiplier(user_option)
        keyframe_count = int(round(base_keyframes * refl_mult))

    # 角色数量：已知用真实值；未知时用代理（实测 30s/9镜头≈8角色，角色≈镜头数，封顶兜底）
    if character_count is not None and character_count > 0:
        char_count = int(character_count)
    else:
        char_count = max(tp.DEFAULT_CHARACTER_IMAGE_COUNT, min(shots, 8))

    # 单图/单镜头单价 + 真实并发（全部取自 get_concurrency_limit，与生产 semaphore 同源）
    img_unit = _image_unit_seconds(user_option)
    kf_concurrency = get_concurrency_limit("keyframe_generation")
    video_concurrency = get_concurrency_limit("video_generation")

    # Step 3: story_style（大纲 LLM + 角色清单 LLM + 角色图生成）
    char_batches = math.ceil(char_count / max(1, kf_concurrency))
    char_img_sec = char_batches * img_unit * tp.KEYFRAME_RETRY_TIME_MULT
    story_style_sec = tp.OUTLINE_LLM_SEC + tp.CHARACTER_LIST_LLM_SEC + char_img_sec

    # Step 3.5: visual（视觉元素匹配 LLM + 角色融合图，融合图数量≈角色数）
    visual_sec = tp.VISUAL_ELEMENTS_LLM_SEC + char_batches * img_unit * tp.KEYFRAME_RETRY_TIME_MULT

    # Step 4: storyboards（关键帧：前置 LLM + 每帧[图+prompt LLM] × 两轮 × retry）
    if keyframe_count <= 0:
        storyboards_sec = 0.0
    else:
        kf_batches = math.ceil(keyframe_count / max(1, kf_concurrency))
        storyboards_sec = (
            tp.STORYBOARD_PRE_LLM_SEC
            + kf_batches * (img_unit + tp.KEYFRAME_PROMPT_LLM_SEC) * tp.KEYFRAME_ROUND_FACTOR * tp.KEYFRAME_RETRY_TIME_MULT
        )

    # Step 5: shots（视频生成，最大头；前置批量 prompt LLM + 按 lipsync 占比加权）
    lipsync_ratio = _lipsync_shot_ratio(user_option)
    normal_ratio = 1.0 - lipsync_ratio
    video_unit = _video_unit_seconds(user_option, est_shot_duration)
    lipsync_unit = _lipsync_unit_seconds(user_option, est_shot_duration)
    weighted_shot_unit = normal_ratio * video_unit + lipsync_ratio * lipsync_unit
    shot_batches = math.ceil(shots / max(1, video_concurrency))
    shots_sec = tp.VIDEO_PROMPT_OVERHEAD_SEC + shot_batches * weighted_shot_unit * tp.VIDEO_RETRY_TIME_MULT

    # Step 6: 固定经验常数 step（纯 LLM 主导，不随数量变）
    fixed = tp.STEP_FIXED_SECONDS
    music_sec = tp.get_music_seconds(duration_sec) if include_music else 0.0

    step_seconds: Dict[str, int] = {
        "music": int(round(music_sec)),
        "analysis": int(round(fixed["analysis"])),
        "story_style": int(round(story_style_sec)),
        "visual": int(round(visual_sec)),
        "scenes": int(round(fixed["scenes"])),
        "storyboards": int(round(storyboards_sec)),
        "shots": int(round(shots_sec)),
        "final": int(round(fixed["final"])),
    }
    if not include_music:
        step_seconds.pop("music", None)
    if is_product_launch:
        step_seconds["narration"] = int(round(tp.get_narration_step_seconds(shots)))

    total = (
        step_seconds["analysis"]
        + step_seconds["story_style"]
        + step_seconds["visual"]
        + step_seconds["scenes"]
        + step_seconds.get("narration", 0)
        + step_seconds["storyboards"]
        + step_seconds["shots"]
        + step_seconds["final"]
    )
    # 音乐若为前置串行模式（user_upload / suno_then_transcribe）计入总时；
    # bgm_parallel 与主链并行，不叠加。这里保守：include_music 时把 music 计入总时，
    # 由调用方按 music_mode 决定是否扣除（见 attach_step_seconds_to_path）。
    if include_music:
        total += step_seconds["music"]

    return TimeEstimateResult(
        total_est_seconds=int(total),
        step_seconds=step_seconds,
        confidence=confidence,
        shot_count=shots,
        estimated_shot_duration_sec=est_shot_duration,
    )


# ── 简单 agent（image/music/story/video_gen）单产物耗时预估 ──
# 这些 agent 没有多步 todo bar，给一个 backend-static 的整体预估（单产物 n=1）。
_STORY_FIXED_SEC = 30      # 故事/文本 LLM 流式
_IMAGE_ORCH_OVERHEAD = 5   # image agent LLM 编排开销
_VIDEO_ORCH_OVERHEAD = 10  # video_gen LLM 编排 + 关键帧开销


def estimate_simple_agent_seconds(agent_type: str, user_option: Optional[UserOption]) -> int:
    """image / music / story / video_gen 单产物整体预估秒数（backend-static）。"""
    at = (agent_type or "").lower()
    if user_option is None:
        user_option = UserOption.default()

    if at == "music":
        return int(round(tp.get_music_seconds(getattr(user_option, "duration", None))))
    if at == "story":
        return _STORY_FIXED_SEC
    if at == "image":
        return int(round(_image_unit_seconds(user_option) + _IMAGE_ORCH_OVERHEAD))
    if at in ("video_gen", "video"):
        try:
            threshold = get_video_driven_split_threshold(user_option)
        except Exception:
            threshold = None
        est_dur = getattr(user_option, "duration", None) or threshold or 5
        est_dur = max(3, int(est_dur))
        return int(round(_video_unit_seconds(user_option, est_dur) + _VIDEO_ORCH_OVERHEAD))
    return 0


# ── 从 state 推断（首包 / gate 通用）──────────────────────

async def get_time_estimate_from_state(
    state: Dict[str, Any],
    include_music: bool = True,
) -> TimeEstimateResult:
    """
    从 VideoAgent state 推断参数并返回全量耗时预估。
    暂停时（state 里有 scene_uuids + shot_uuids）用确定数量 → confidence=medium；
    首包时按初始参数估算 → confidence=low。
    （提取逻辑与 cost_estimation._get_full_estimate_from_state 对齐）
    """
    user_input_data = state.get("user_input_data")
    user_option = user_input_data.user_option if user_input_data else None
    if not user_option:
        user_option = UserOption.default()

    scene_uuids = state.get("scene_uuids") or []
    shot_uuids = state.get("shot_uuids") or []
    character_uuids = state.get("character_uuids") or []
    duration_sec = getattr(user_option, "duration", None)

    scene_count = len(scene_uuids) if scene_uuids else None
    shot_count = len(shot_uuids) if shot_uuids else None
    character_count = len(character_uuids) if character_uuids else None
    confidence = "medium" if shot_count else "low"

    audio_files = getattr(user_input_data, "audio_files", None) if user_input_data else None
    is_audio_driven = bool(audio_files)

    cc = getattr(user_option, "content_category", None)
    content_category = getattr(cc, "value", None) if cc is not None else None

    audio_duration_sec: Optional[float] = None
    audio_segment_durations: Optional[List[float]] = None
    at_uuids = state.get("audio_transcription_uuids") or []
    if at_uuids:
        try:
            from ..utils.database_utils import get_audio_transcription_from_db
            at = await get_audio_transcription_from_db(at_uuids)
            if at is not None and getattr(at, "duration", None) is not None:
                audio_duration_sec = float(getattr(at, "duration", 0))
                segs = getattr(at, "segments", None)
                if segs:
                    audio_segment_durations = [
                        float(getattr(s, "duration", 0))
                        for s in segs
                        if getattr(s, "duration", None) is not None
                    ]
        except Exception as e:
            logger.debug("load audio_transcription for time estimate failed: %s", e)

    if audio_duration_sec is None and is_audio_driven:
        audio_duration_sec = float(duration_sec) if duration_sec is not None else None

    return estimate_video_agent_time(
        user_option=user_option,
        scene_count=scene_count,
        shot_count=shot_count,
        character_count=character_count,
        duration_sec=duration_sec,
        is_audio_driven=is_audio_driven,
        audio_duration_sec=audio_duration_sec,
        audio_segment_durations=audio_segment_durations,
        content_category=content_category,
        include_music=include_music,
        confidence=confidence,
    )


# ── gate 暂停：剩余阶段耗时 ───────────────────────────────

# step 在流水线中的串行先后（用于算"剩余"）
_STEP_ORDER = ["analysis", "story_style", "scenes", "visual", "storyboards", "narration", "shots", "final"]

# gate → 该 gate 之后仍需执行的 step
_GATE_REMAINING_STEPS = {
    "after_music": ["analysis", "story_style", "scenes", "visual", "storyboards", "narration", "shots", "final"],
    "after_outline": ["story_style", "scenes", "visual", "storyboards", "narration", "shots", "final"],
    "after_character": ["scenes", "visual", "storyboards", "narration", "shots", "final"],
    "after_storyboard_detail": ["narration", "storyboards", "shots", "final"],
    "after_keyframe_reflection": ["narration", "shots", "final"],
    "after_shots": ["final"],
}


async def get_time_estimate_for_gate(state: Dict[str, Any], gate_step: str) -> Dict[str, Any]:
    """
    每个 interrupt 暂停时返回**剩余阶段**的预估耗时（秒）。
    与 cost_estimation.get_credit_estimate_for_gate 同位、同时机调用。
    """
    estimate = await get_time_estimate_from_state(state, include_music=False)
    remaining_steps = _GATE_REMAINING_STEPS.get(gate_step, _STEP_ORDER)
    remaining = sum(estimate.step_seconds.get(s, 0) for s in remaining_steps)
    return {
        "remaining_est_seconds": int(remaining),
        "step_seconds": {s: estimate.step_seconds.get(s, 0) for s in remaining_steps},
        "confidence": estimate.confidence,
    }


# ── 给 workflow_state 事件用：把 est_seconds 注入 path ────

def attach_step_seconds_to_path(
    path: List[Dict[str, Any]],
    estimate: TimeEstimateResult,
    music_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    给 workflow_state.path 每个 entry 注入 est_seconds，并返回 total / confidence。
    bgm_parallel 模式下 music 与主链并行 → 不计入串行 total。
    """
    enriched: List[Dict[str, Any]] = []
    total = 0
    for entry in path:
        sid = entry.get("id")
        secs = estimate.step_seconds.get(sid, 0)
        new_entry = {**entry, "est_seconds": int(secs)}
        enriched.append(new_entry)
        if sid == "music" and music_mode == "bgm_parallel":
            continue  # 并行，不计入串行总时
        total += int(secs)

    return {
        "path": enriched,
        "total_est_seconds": int(total),
        "estimate_confidence": estimate.confidence,
    }
