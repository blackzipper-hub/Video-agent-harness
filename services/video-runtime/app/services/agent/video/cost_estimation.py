"""
VideoAgent 积分/成本推算
========================

**核心目标**：在用户提交任务前和每次暂停时，给前端一个「预计消耗 XX 积分」的数值。

**只算 tool 成本**（keyframe 图片生成 + 视频生成），LLM 调用成本可忽略不计。

**汇率**：1 美元 = 100 积分  (CREDITS_PER_DOLLAR)

**计算公式**：
    keyframe积分 = keyframe数量 × 单张keyframe美元 × 100
    video积分   = shot数量 × 加权平均单镜头美元 × 100
    总积分      = keyframe积分 + video积分

    keyframe数量 = shot数 × (首尾帧? 2 : 1) × 反思倍数
    单镜头美元   = (1-lipsync比例) × 普通video单价 + lipsync比例 × lipsync单价

**关键事实**：
    - scene : shot = 1:1（prompt 强制 shots数量 == scenes数量）
    - lipsync 镜头走 lipsync_tool_wrapper（Wan 2.6 Flash / Wan 2.5 + audio_url），
      不走 latentsync ($0.08/s)

────────────────────────────────────────────
举例：前端默认配置（Seedance v1 Pro Fast + Nano Banana 2, 1080p, 30s, lipsync=50%, 无首尾帧, 无反思）

  拆分/规划 prefer = profile 8s（禁止 API min=3）
  scene数估计 = ceil(30/8) = 4，shot数 = 4（1:1；真实场数由 LLM 设计）

  keyframe单价 = $0.067 (Nano Banana 2 = Gemini 3.1 Flash)
  keyframe数   = 4 × 1 = 4
  keyframe积分 ≈ 4 × 0.067 × 100

  lipsync比例 = 50/100 = 0.5
  普通video / lipsync 单价按 prefer 秒数估算后加权

  总积分 ≈ 67 + 308 = 375

如果开启首尾帧（enable_continuity_mode=True）+ 反思 + 无lipsync：
  keyframe数 = 10 × 2 × 1.25 = 25 → keyframe积分 = 25 × 0.067 × 100 = 168
  video 使用 Seedance Lite（有尾帧），1080p $0.09/s × 5s = $0.45
  video积分 = 10 × 0.45 × 100 = 450
  总积分 ≈ 168 + 450 = 618
────────────────────────────────────────────

三种使用场景：
1. 新接口（用户提交前）：estimate_video_agent_credits(user_option, is_audio_driven, ...) → 全量预估
2. interrupt 暂停：get_credit_estimate_for_gate(state, gate_step) → 只返回剩余阶段的预估
3. 按 thread_id 查询：get_credit_estimate_for_thread(thread_id, ...) → 从 DB 取确定数量算积分
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ....models.user_options import (
    UserOption,
    VideoGenerationTool,
    ImageGenerationTool,
    DEFAULT_IMAGE_TOOL,
    DEFAULT_VIDEO_TOOL,
)
from ....models.tool_enums import (
    ContentCategory,
    Resolution,
    ToolType,
    DefaultValues,
)
from ....services.tool_service import (
    ToolService,
    get_seedance_tool_type,
    CREDITS_PER_DOLLAR,
)
from app.agent_config.duration import (
    get_audio_driven_split_threshold,
    get_video_driven_split_threshold,
)

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────

# scene : shot = 1:1（prompt 强制 "shots数量 = scenes_count"，shot_number = scene_number）
DEFAULT_SHOTS_PER_SCENE = 1

# 估算单镜头视频时长（秒）的兜底值
# 真实估算时会使用 threshold（通常 3s），仅在无法推断 threshold 时使用此值
DEFAULT_VIDEO_SECONDS_PER_SHOT = 5

# keyframe VLM 反思约 1/4 镜头会触发重生 → 整体约 ×1.25
KEYFRAME_REFLECTION_MULTIPLIER = 1.25

# 前端「显示反思」开关 → 用户大概率重选 → 约 ×2
SHOW_REFLECTION_KEYFRAME_MULTIPLIER = 2.0

# Built-in consistency check（always-on，非 VLM 反思节点）
# check_character_consistency_llm (image_tool_wrapper.py:406) ~15-20% retry
BUILTIN_KEYFRAME_CONSISTENCY_MULT = 1.2
# check_video_consistency_llm (video_tool_wrapper.py:348) ~20-25% retry
BUILTIN_VIDEO_CONSISTENCY_MULT = 1.25


# ── 响应模型 ──────────────────────────────────────────────

class CreditEstimateBreakdown(BaseModel):
    """积分预估的详细拆分"""
    scene_count: int = Field(description="场景数（= 镜头数，1:1）")
    shot_count: int = Field(description="镜头数")
    estimated_shot_duration_sec: int = Field(description="预估每镜头视频时长（秒），基于 split threshold")
    keyframe_count_estimate: int = Field(description="预估 keyframe 数量（含首尾帧和反思倍数）")
    keyframe_reflection_multiplier: float = Field(description="VLM 反思导致的 keyframe 倍数")
    builtin_keyframe_retry_multiplier: float = Field(description="Built-in character consistency retry 倍数 (always-on)")
    builtin_video_retry_multiplier: float = Field(description="Built-in video consistency retry 倍数 (always-on)")
    cost_per_keyframe_usd: float = Field(description="单张 keyframe 美元成本")
    cost_per_video_shot_usd: float = Field(description="单镜头普通视频美元成本")
    cost_per_lipsync_shot_usd: float = Field(description="单镜头 lipsync 视频美元成本（Wan 2.6 + audio）")
    lipsync_shot_ratio: float = Field(description="lipsync 镜头占比 0~1")


class CreditEstimateResult(BaseModel):
    """积分预估结果"""
    total_credits_estimate: int = Field(description="总预估积分 = keyframe + video")
    keyframe_credits_estimate: int = Field(description="keyframe 阶段预估积分")
    video_credits_estimate: int = Field(description="video 阶段预估积分")
    breakdown: CreditEstimateBreakdown = Field(description="详细拆分")


# ── 内部工具函数 ───────────────────────────────────────────

def _resolve_video_tool_type(
    user_option: UserOption,
    has_end_image: bool,
    resolution: Resolution,
) -> ToolType:
    """
    根据用户选的视频工具 + 是否有尾帧 + 分辨率 → 实际用于计费的 ToolType。

    例子：
      - pollo_seedance + 无尾帧         → SEEDANCE_V1_PRO_FAST
      - pollo_seedance + 有尾帧 + 720p → SEEDANCE_V1_LITE_I2V_720P
      - wan_2_5                          → WAN_2_5_I2V
    """
    from ....models.user_options import resolve_effective_video_tool, should_use_reference_to_video

    vt = resolve_effective_video_tool(user_option)

    if vt == VideoGenerationTool.POLLO_SEEDANCE:
        return get_seedance_tool_type(resolution, has_end_image)

    # Seedance2 reference-to-video 实际打 T2V 端点，计费跟 T2V 对齐
    if should_use_reference_to_video(user_option):
        t2v_mapping = {
            VideoGenerationTool.SEEDANCE_2_I2V: ToolType.SEEDANCE_2_T2V,
            VideoGenerationTool.SEEDANCE_2_I2V_TURBO: ToolType.SEEDANCE_2_T2V_TURBO,
            VideoGenerationTool.SEEDANCE_2_FAST_I2V: ToolType.SEEDANCE_2_FAST_T2V,
            VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO: ToolType.SEEDANCE_2_FAST_T2V_TURBO,
        }
        if vt in t2v_mapping:
            return t2v_mapping[vt]

    mapping = {
        VideoGenerationTool.SEEDANCE_V1_5: ToolType.SEEDANCE_V1_5_PRO_FAST,
        VideoGenerationTool.SEEDANCE_2_I2V: ToolType.SEEDANCE_2_I2V,
        VideoGenerationTool.SEEDANCE_2_I2V_TURBO: ToolType.SEEDANCE_2_I2V_TURBO,
        VideoGenerationTool.SEEDANCE_2_FAST_I2V: ToolType.SEEDANCE_2_FAST_I2V,
        VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO: ToolType.SEEDANCE_2_FAST_I2V_TURBO,
        VideoGenerationTool.WAN_2_5: ToolType.WAN_2_5_I2V,
        VideoGenerationTool.WAN_2_6: ToolType.WAN_2_6_FLASH_I2V,
        VideoGenerationTool.KLING_V3_STD: ToolType.KLING_V3_STD,
        VideoGenerationTool.HAPPYHORSE_1_0_I2V: ToolType.HAPPYHORSE_1_0_I2V,
        VideoGenerationTool.HAPPYHORSE_1_1_I2V: ToolType.HAPPYHORSE_1_1_I2V,
        VideoGenerationTool.OPENAI_SORA: ToolType.SORA_2,
        VideoGenerationTool.OPENAI_SORA_PRO: ToolType.SORA_2_PRO,
    }
    return mapping.get(vt, ToolType.SEEDANCE_V1_PRO_FAST)


def _resolve_lipsync_tool_type(user_option: UserOption) -> VideoGenerationTool:
    """复用 resolve_effective_lipsync_tool（单一来源）。"""
    from ....models.user_options import resolve_effective_lipsync_tool
    return resolve_effective_lipsync_tool(user_option)


def _image_tool_type_from_user_option(user_option: UserOption) -> ToolType:
    """
    用户选的图像工具 → 对应的 ToolType（用于 calculate_cost）。

    例子：
      NANO_BANANA     → GEMINI_2_5_FLASH_IMAGE   ($0.039/张)
      NANO_BANANA_PRO → GEMINI_3_PRO_IMAGE_PREVIEW ($0.134/张)
      NANO_BANANA_2   → GEMINI_3_1_FLASH_IMAGE_PREVIEW ($0.067/张)
      SEEDREAM        → SEEDREAM_V4_5             ($0.04/张)
      GPT_IMAGE_2     → GPT_IMAGE_2（按实际选用的 API 1k/2k/4k × quality 计价）
    """
    from ....services.tool_service import _USER_OPTION_TO_TOOL_TYPE
    it = user_option.image_generation_tool
    if it == ImageGenerationTool.AUTO:
        it = DEFAULT_IMAGE_TOOL
    return _USER_OPTION_TO_TOOL_TYPE.get(it, ToolType.GEMINI_2_5_FLASH_IMAGE)


def _cost_per_keyframe_dollar(user_option: UserOption) -> float:
    """
    单张关键帧的美元成本，直接调 ToolService.calculate_cost。

    例子：Nano Banana → $0.039, Nano Banana Pro → $0.134, Seedream → $0.04；GPT Image 2 随 resolution+aspect_ratio 选最小满足 TARGET 的 API 档（多为 1k/2k）再按档计价。
    """
    tool_type = _image_tool_type_from_user_option(user_option)
    if tool_type == ToolType.SEEDREAM_V4_5:
        return ToolService.calculate_cost(tool_type, output_image_count=1)
    if tool_type == ToolType.GPT_IMAGE_2:
        from ....tools.image.gpt_image_2_mapping import gpt_image_2_api_resolution_and_quality
        rv = getattr(user_option.resolution, "value", None) or Resolution.P1080.value
        arv = getattr(user_option.aspect_ratio, "value", None) or "16:9"
        gpt_res, qual = gpt_image_2_api_resolution_and_quality(rv, arv)
        return ToolService.calculate_cost(
            tool_type,
            gpt_image_resolution=gpt_res,
            gpt_image_quality=qual,
        )
    return ToolService.calculate_cost(tool_type, usage_metadata=None)


def _get_resolution(user_option: UserOption) -> Resolution:
    """从 user_option 解析分辨率，默认 1080p（与前端 defaults.ts 一致）。"""
    res = getattr(user_option.resolution, "value", None) or getattr(Resolution.P1080, "value", "1080p")
    if isinstance(res, str) and res not in ("480p", "720p", "1080p"):
        res = Resolution.P1080
    return Resolution(res) if isinstance(res, str) else res


def _cost_per_normal_video_shot_dollar(
    user_option: UserOption,
    duration_sec: int = DEFAULT_VIDEO_SECONDS_PER_SHOT,
) -> float:
    """
    单镜头**普通**视频的美元成本（非 lipsync 镜头）。

    例子（duration=5s, 1080p）：
      Seedance v1 Pro          → $0.048/s × 5 = $0.24
      Seedance Lite (有尾帧)    → $0.09/s × 5 = $0.45
      Wan 2.5                   → $0.15/s × 5 = $0.75
      Wan 2.6 (无audio)         → $0.125/5s × 1.5(1080p) = $0.1875
      Kling v3                  → $0.90
      Sora 2 (4s)               → $0.40
    """
    resolution = _get_resolution(user_option)
    has_end_image = bool(user_option.enable_continuity_mode)
    tool_type = _resolve_video_tool_type(user_option, has_end_image, resolution)

    if tool_type == ToolType.KLING_V3_STD:
        return ToolService.calculate_cost(tool_type, duration=duration_sec, sound=False)
    if tool_type == ToolType.HAPPYHORSE_1_0_I2V:
        return ToolService.calculate_cost(tool_type, duration=duration_sec, resolution=resolution)
    if tool_type == ToolType.HAPPYHORSE_1_1_I2V:
        return ToolService.calculate_cost(tool_type, duration=duration_sec, resolution=resolution)
    if tool_type == ToolType.WAN_2_6_FLASH_I2V:
        return ToolService.calculate_cost(
            tool_type, duration=duration_sec, resolution=resolution, enable_audio=False,
        )
    if tool_type in (ToolType.SORA_2, ToolType.SORA_2_PRO):
        ar = getattr(user_option.aspect_ratio, "value", None) or "16:9"
        return ToolService.calculate_cost(
            tool_type,
            duration=min(4, duration_sec) if duration_sec not in (4, 8, 12) else duration_sec,
            aspect_ratio=ar,
            resolution=resolution.value if hasattr(resolution, "value") else str(resolution),
        )
    return ToolService.calculate_cost(tool_type, duration=duration_sec, resolution=resolution)


def _cost_per_lipsync_shot_dollar(
    user_option: UserOption,
    duration_sec: int = DEFAULT_VIDEO_SECONDS_PER_SHOT,
) -> float:
    """
    单镜头 **lipsync** 视频的美元成本。

    lipsync 走 lipsync_tool_wrapper → LIPSYNC_CAPABLE_VIDEO_TOOLS（LTX 2.3、Kling V2 AI Avatar Pro、WAN 2.2 S2V、Wan 2.5、Wan 2.6，带 audio_url），
    不走 latentsync ($0.08/s)。

    计费逻辑：
      - LTX 2.3：billable_sec = max(duration_sec, 5)，cost = billable_sec × rate[resolution]
      - Kling V2 AI Avatar Pro：billable_sec = max(duration_sec, 5)，cost = billable_sec × $0.112/s（与分辨率无关）
      - WAN 2.2 Speech-to-Video：billable_sec = max(duration_sec, 5)（不足 5s 按 5s）；480p $0.03/s、720p $0.06/s（与 WaveSpeed 实测：6s+ 按秒线性）
      - Wan 2.6 + enable_audio=True → base $0.125/5s × res_mult × 2
      - Wan 2.5 → 标准 Wan 2.5 定价
    """
    resolution = _get_resolution(user_option)
    lipsync_tool = _resolve_lipsync_tool_type(user_option)

    if lipsync_tool == VideoGenerationTool.LTX_2_3:
        return ToolService.calculate_cost(
            ToolType.LTX_2_3_LIPSYNC, duration=duration_sec, resolution=resolution,
        )
    if lipsync_tool == VideoGenerationTool.KLING_V2_AI_AVATAR_PRO:
        return ToolService.calculate_cost(
            ToolType.KLING_V2_AI_AVATAR_PRO, duration=duration_sec,
        )
    if lipsync_tool == VideoGenerationTool.WAN_2_2_SPEECH_TO_VIDEO:
        return ToolService.calculate_cost(
            ToolType.WAN_2_2_SPEECH_TO_VIDEO, duration=duration_sec, resolution=resolution,
        )
    if lipsync_tool == VideoGenerationTool.WAN_2_5:
        return ToolService.calculate_cost(
            ToolType.WAN_2_5_I2V, duration=duration_sec, resolution=resolution,
        )
    return ToolService.calculate_cost(
        ToolType.WAN_2_6_FLASH_I2V, duration=duration_sec, resolution=resolution, enable_audio=True,
    )


def _lipsync_shot_ratio(user_option: UserOption) -> float:
    """
    Lipsync 镜头在所有镜头中的占比（0~1）。
    计费预估用：按用户 lipsync_coverage / Lip-Sync MV 估算占比。
    实际镜头 generation_mode 由 scene/revision 等上游写入，assign_generation_mode_to_shots 不再按覆盖率升格口型。

    例子：
      content_category=LIP_SYNC_MV → 1.0（预估）
      lipsync_coverage=50          → 0.5（预估）
      lipsync_coverage=0（默认）    → 0.0
    """
    cc = getattr(user_option, "content_category", None)
    if cc is not None and getattr(cc, "value", None) == ContentCategory.LIP_SYNC_MV.value:
        return 1.0
    coverage = getattr(user_option, "lipsync_coverage", 0) or 0
    return min(1.0, max(0.0, coverage / 100.0))


def _keyframe_reflection_multiplier(user_option: UserOption) -> float:
    """
    反思导致的 keyframe 数量倍数。

    - enable_keyframe_reflection → ×1.25（约 1/4 镜头触发重生）
    - enable_show_reflection     → ×2（用户大概率重选）
    - 两者同时开启取较大值

    例子：10 shot, 无反思 → 10 keyframe
          开启反思        → 10 × 1.25 = 13
          开启显示反思    → 10 × 2 = 20
    """
    mult = 1.0
    if user_option.enable_keyframe_reflection:
        mult *= KEYFRAME_REFLECTION_MULTIPLIER
    if getattr(user_option, "enable_show_reflection", False):
        mult = max(mult, SHOW_REFLECTION_KEYFRAME_MULTIPLIER)
    return mult


# ── 场景/镜头数估算 ──────────────────────────────────────

def estimate_scene_and_shot_count_from_initial_params(
    user_option: UserOption,
    is_audio_driven: bool,
    audio_duration_sec: Optional[float] = None,
    audio_segment_durations: Optional[List[float]] = None,
    duration_sec: Optional[int] = None,
    content_category: Optional[str] = None,
) -> tuple[int, int]:
    """
    用初始参数估算场景数与镜头数（scene = shot，1:1）。
    逻辑与 scene_generation_service._compute_scene_structure_for_chapter 一致。

    Audio-driven（有音频文件）：
      threshold = get_audio_driven_split_threshold(user_option, content_category)
        与 scene 生成一致: min(audio_driven_duration_values)（当前为全链工具整数秒交集，如 4s）

      每个 audio segment：
        duration <= threshold → 1 scene
        duration > threshold  → ceil(duration / threshold) scenes

      例子：音频 30s, 3 段 [8s, 12s, 10s], threshold=4s
        8s → ceil(8/4)=2, 12s → ceil(12/4)=3, 10s → ceil(10/4)=3
        共 10 scenes = 10 shots

    Video-driven（无音频）：
      threshold = get_video_driven_split_threshold(user_option)（profile prefer，如 8s；禁止 API min=3）
      scenes = ceil(duration / threshold)

      例子：duration=30s, threshold=8s → ceil(30/8) = 4 scenes（估计值；真实场数由 LLM 设计）
    """
    if is_audio_driven:
        threshold = get_audio_driven_split_threshold(user_option, content_category)
        if audio_segment_durations:
            scenes = 0
            for d in audio_segment_durations:
                if d <= threshold:
                    scenes += 1
                else:
                    scenes += math.ceil(d / threshold)
            scenes = max(1, scenes)
        elif audio_duration_sec is not None and audio_duration_sec > 0:
            scenes = max(1, math.ceil(audio_duration_sec / threshold))
        else:
            duration = duration_sec or getattr(user_option, "duration", None)
            if isinstance(duration, (int, float)) and duration > 0:
                scenes = max(1, math.ceil(float(duration) / threshold))
            else:
                scenes = 1
    else:
        threshold = get_video_driven_split_threshold(user_option)
        total = duration_sec or getattr(user_option, "duration", None)
        if total is not None and total > 0:
            scenes = max(1, math.ceil(float(total) / threshold))
        else:
            scenes = 1

    shots = scenes * DEFAULT_SHOTS_PER_SCENE
    return (scenes, shots)


def estimate_scene_and_shot_count(
    user_option: Optional[UserOption] = None,
    scene_count: Optional[int] = None,
    shot_count: Optional[int] = None,
    duration_sec: Optional[int] = None,
    is_audio_driven: bool = False,
    audio_duration_sec: Optional[float] = None,
    audio_segment_durations: Optional[List[float]] = None,
    content_category: Optional[str] = None,
) -> tuple[int, int]:
    """
    优先用已知的 scene_count/shot_count（暂停时 DB 里已有确定值），
    否则走初始参数估算。scene:shot = 1:1。
    """
    if shot_count is not None and shot_count >= 0:
        if scene_count is not None and scene_count >= 0:
            return (scene_count, shot_count)
        return (shot_count, shot_count)
    if scene_count is not None and scene_count >= 0:
        return (scene_count, scene_count * DEFAULT_SHOTS_PER_SCENE)
    if user_option and (is_audio_driven or duration_sec is not None or audio_duration_sec is not None):
        return estimate_scene_and_shot_count_from_initial_params(
            user_option=user_option,
            is_audio_driven=is_audio_driven,
            audio_duration_sec=audio_duration_sec,
            audio_segment_durations=audio_segment_durations,
            duration_sec=duration_sec,
            content_category=content_category,
        )
    if duration_sec is not None and duration_sec > 0 and user_option:
        return estimate_scene_and_shot_count_from_initial_params(
            user_option=user_option,
            is_audio_driven=False,
            duration_sec=duration_sec,
            content_category=content_category,
        )
    return (1, DEFAULT_SHOTS_PER_SCENE)


# ── 主函数：积分预估 ──────────────────────────────────────

def estimate_video_agent_credits(
    user_option: UserOption,
    scene_count: Optional[int] = None,
    shot_count: Optional[int] = None,
    duration_sec: Optional[int] = None,
    is_audio_driven: bool = False,
    audio_duration_sec: Optional[float] = None,
    audio_segment_durations: Optional[List[float]] = None,
    content_category: Optional[str] = None,
) -> CreditEstimateResult:
    """
    推算 VideoAgent 积分消耗，返回 CreditEstimateResult。

    核心改进：
    1. 用 split threshold 计算真实镜头时长（通常 3s），不再固定 5s
    2. 内置 built-in consistency check 的 retry 倍数（keyframe ×1.2, video ×1.25）

    单价复用：所有单价直接调 ToolService.calculate_cost()，
    和实际生成时扣费走的是同一个函数，改价只改 tool_service._PRICING_CONFIG 一处。

    ──────────── 完整例子 ────────────

    场景：60s Video-Driven, Seedance v1 + Nano Banana 2, 1080p, 无首尾帧, 无VLM反思, lipsync=50%

    Step 1 - scene/shot：
      threshold=3s → ceil(60/3) = 20 scenes = 20 shots, 每镜头 ~3s

    Step 2 - keyframe：
      无首尾帧 → 20 × 1 = 20, 无VLM反思 → 20 张
      单价 $0.067 → 理想 keyframe = 20 × $0.067 = $1.34
      built-in retry ×1.2 → 实际 $1.34 × 1.2 = $1.61 → 161 积分

    Step 3 - video (每镜头 3s)：
      lipsync 50%:
        普通: Seedance v1 Pro 1080p 3s → $0.048 × 3 = $0.144
        lipsync: Wan 2.6 + audio 1080p 3s → $0.125 × (3/5) × 1.5 × 2 = $0.225
      理想 video = 20 × (0.5×$0.144 + 0.5×$0.225) = 20 × $0.1845 = $3.69
      built-in retry ×1.25 → 实际 $3.69 × 1.25 = $4.61 → 461 积分

    Step 4: 总计 = 161 + 461 = 622 积分 ($6.22)
    """

    if duration_sec is None and user_option is not None:
        duration_sec = getattr(user_option, "duration", None)
    if content_category is None and user_option is not None:
        cc = getattr(user_option, "content_category", None)
        content_category = getattr(cc, "value", None) if cc is not None else None

    # Step 1: scene/shot（1:1）
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

    # Step 1.5: 推算每镜头真实时长（秒），基于 split threshold
    if is_audio_driven:
        threshold = get_audio_driven_split_threshold(user_option, content_category)
    else:
        threshold = get_video_driven_split_threshold(user_option)
    est_shot_duration = max(3, int(threshold))

    # Step 2: keyframe 数量
    # Seedance2 reference_t2v：跳过 keyframe 管线 → 0
    from ....models.user_options import should_skip_keyframe_pipeline

    if should_skip_keyframe_pipeline(user_option):
        refl_mult = 1.0
        keyframe_count_estimate = 0
    else:
        # 首尾帧 → 每 shot 2 张(首帧+尾帧)，否则 1 张
        base_keyframes = shots * (2 if user_option.enable_continuity_mode else 1)
        refl_mult = _keyframe_reflection_multiplier(user_option)
        keyframe_count_estimate = int(round(base_keyframes * refl_mult))

    # Step 3: 单价（美元）— 使用真实镜头时长而非固定 5s
    cost_per_kf = _cost_per_keyframe_dollar(user_option)
    cost_per_video = _cost_per_normal_video_shot_dollar(user_option, duration_sec=est_shot_duration)
    cost_per_lipsync = _cost_per_lipsync_shot_dollar(user_option, duration_sec=est_shot_duration)
    lipsync_ratio = _lipsync_shot_ratio(user_option)
    normal_ratio = 1.0 - lipsync_ratio

    # Step 4: 总美元（含 built-in consistency retry 倍数）
    keyframe_total_usd = keyframe_count_estimate * cost_per_kf * BUILTIN_KEYFRAME_CONSISTENCY_MULT
    video_total_usd = (
        shots * (normal_ratio * cost_per_video + lipsync_ratio * cost_per_lipsync)
        * BUILTIN_VIDEO_CONSISTENCY_MULT
    )

    # Step 5: 美元 → 积分
    def usd_to_credits(u: float) -> int:
        return int(round(u * CREDITS_PER_DOLLAR))

    keyframe_credits_est = usd_to_credits(keyframe_total_usd)
    video_credits_est = usd_to_credits(video_total_usd)
    total_credits_est = keyframe_credits_est + video_credits_est

    breakdown = CreditEstimateBreakdown(
        scene_count=scenes,
        shot_count=shots,
        estimated_shot_duration_sec=est_shot_duration,
        keyframe_count_estimate=keyframe_count_estimate,
        keyframe_reflection_multiplier=refl_mult,
        builtin_keyframe_retry_multiplier=BUILTIN_KEYFRAME_CONSISTENCY_MULT,
        builtin_video_retry_multiplier=BUILTIN_VIDEO_CONSISTENCY_MULT,
        cost_per_keyframe_usd=round(cost_per_kf, 6),
        cost_per_video_shot_usd=round(cost_per_video, 6),
        cost_per_lipsync_shot_usd=round(cost_per_lipsync, 6),
        lipsync_shot_ratio=round(lipsync_ratio, 4),
    )

    return CreditEstimateResult(
        total_credits_estimate=total_credits_est,
        keyframe_credits_estimate=keyframe_credits_est,
        video_credits_estimate=video_credits_est,
        breakdown=breakdown,
    )


# ── 给 interrupt 暂停用：返回剩余阶段的预估 ──────────────

async def get_credit_estimate_for_gate(state: Dict[str, Any], gate_step: str) -> Dict[str, Any]:
    """
    每个 interrupt 暂停时返回的积分预估。只返回**剩余阶段**的预估积分。

    gate_step 对应：
      - "after_character"            → 暂停1（keyframe + video 都没做）→ 返回 keyframe + video 预估
      - "after_keyframe_reflection"  → 暂停2（keyframe 做完，video 没做）→ 返回 video 预估
      - "after_shots"                → 暂停3（都做完了）→ 返回 0

    例子（20 shots @3s, Seedance v1 1080p, Nano Banana 2, lipsync=50%, 含 built-in retry）：
      暂停1: remaining = 161(keyframe) + 461(video) = 622
      暂停2: remaining = 461(video)
      暂停3: remaining = 0
    """
    if gate_step == "after_shots":
        return {"remaining_credits_estimate": 0, "keyframe_credits_estimate": 0, "video_credits_estimate": 0}

    estimate = await _get_full_estimate_from_state(state)

    if gate_step == "after_keyframe_reflection":
        return {
            "remaining_credits_estimate": estimate.video_credits_estimate,
            "keyframe_credits_estimate": 0,
            "video_credits_estimate": estimate.video_credits_estimate,
        }

    # after_character 或任何其他暂停 → 全量
    return {
        "remaining_credits_estimate": estimate.total_credits_estimate,
        "keyframe_credits_estimate": estimate.keyframe_credits_estimate,
        "video_credits_estimate": estimate.video_credits_estimate,
    }


async def _get_full_estimate_from_state(state: Dict[str, Any]) -> CreditEstimateResult:
    """
    从 VideoAgent state 自动推断参数并返回全量积分预估。

    暂停时（state 里有 scene_uuids + shot_uuids）：用确定数量。
    首包时（还没有 scene_uuids）：按初始参数估算。

    is_audio_driven / audio_duration_sec 始终从 user_input_data + audio_transcription_uuids 推断，
    不受 scene/shot 是否已知影响（修复：之前只在 scene_count 未知时才检测，导致暂停2
    的 is_audio_driven 错误为 False、est_shot_duration 使用 video-driven threshold）。
    """
    user_input_data = state.get("user_input_data")
    user_option = user_input_data.user_option if user_input_data else None
    if not user_option:
        from ....models.user_options import UserOption
        user_option = UserOption.default()

    scene_uuids = state.get("scene_uuids") or []
    shot_uuids = state.get("shot_uuids") or []
    duration_sec = getattr(user_option, "duration", None)

    scene_count = len(scene_uuids) if scene_uuids else None
    shot_count = len(shot_uuids) if shot_uuids else None

    # is_audio_driven / content_category 始终检测（不依赖 scene/shot 是否已知）
    audio_files = getattr(user_input_data, "audio_files", None) if user_input_data else None
    is_audio_driven = bool(audio_files)

    cc = getattr(user_option, "content_category", None)
    content_category = getattr(cc, "value", None) if cc is not None else None

    audio_duration_sec: Optional[float] = None
    audio_segment_durations: Optional[List[float]] = None

    # 从 audio_transcription_uuids 加载 AudioTranscription（与 video_assembly_node 一致）
    at_uuids = state.get("audio_transcription_uuids") or []
    if at_uuids:
        try:
            from ....services.agent.utils.database_utils import get_audio_transcription_from_db
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
            logger.debug("load audio_transcription for estimate failed: %s", e)

    if audio_duration_sec is None and is_audio_driven:
        audio_duration_sec = float(duration_sec) if duration_sec is not None else None

    return estimate_video_agent_credits(
        user_option=user_option,
        scene_count=scene_count,
        shot_count=shot_count,
        duration_sec=duration_sec,
        is_audio_driven=is_audio_driven,
        audio_duration_sec=audio_duration_sec if audio_duration_sec is not None else None,
        audio_segment_durations=audio_segment_durations,
        content_category=content_category,
    )


# ── 按 thread_id 从 DB 查（备用） ──────────────────────────

async def get_credit_estimate_for_thread(
    thread_id: str,
    user_id: str,
    user_option_override: Optional[UserOption] = None,
) -> CreditEstimateResult:
    """
    按 thread_id 从 DB 取 scene_count、shot_count 和 user_option，再推算积分。
    """
    from ....crud.conversation import async_get_conversation_by_thread_id
    from ....crud.video.video_story import get_scenes_by_conversation, get_detailed_shots_by_conversation
    from ....exceptions import BusinessException, BusinessExceptionCode

    conv = await async_get_conversation_by_thread_id(thread_id)
    if not conv:
        raise BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "未找到该 thread 对应对话")
    if getattr(conv, "user_id", None) != user_id:
        raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "无权访问该 thread")

    conversation_id = str(getattr(conv, "id", ""))
    scenes = await get_scenes_by_conversation(conversation_id, thread_id)
    shots = await get_detailed_shots_by_conversation(conversation_id, thread_id)
    scene_count = len(scenes)
    shot_count = len(shots)

    user_option = user_option_override
    if user_option is None:
        raw = getattr(conv, "user_option", None)
        if raw:
            try:
                import json
                from ....models.user_options import UserOption
                data = json.loads(raw) if isinstance(raw, str) else raw
                user_option = UserOption(**data) if isinstance(data, dict) else UserOption.default()
            except Exception as e:
                logger.debug("parse conversation user_option failed: %s", e)
                from ....models.user_options import UserOption
                user_option = UserOption.default()
        else:
            from ....models.user_options import UserOption
            user_option = UserOption.default()

    return estimate_video_agent_credits(
        user_option=user_option,
        scene_count=scene_count if scene_count > 0 else None,
        shot_count=shot_count if shot_count > 0 else None,
        duration_sec=getattr(user_option, "duration", None),
    )
