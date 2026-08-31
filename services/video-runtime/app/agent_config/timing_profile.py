"""
工具 / 阶段耗时 profile（单位：秒）
=================================

时间预估的「单价表」，与计费里的 `ToolService._PRICING_CONFIG` 同位：
- 计费是「单价(美元) × 数量」，时间是「单耗时(秒) × 数量」。
- 这里只放**数据 + 简单查表函数**，聚合逻辑在 `services/agent/video/time_estimation.py`。

初版数值来源（INITIAL，先用「工具维度」经验值，后续用真实数据替换）：
- 前端既有经验值 `MessageArea.calculateTotalEstimatedTime`：
  keyframe 90s/镜头、video 80s/镜头、music 180s、拼接 60s、固定 240s（覆盖 analysis+story+visual）。
- WaveSpeed/Suno 单次调用经验。

后续更准的来源（不改本文件结构，只改数值）：
- `scripts/probe_*` 工具耗时探针产出。
- 生产 version DB 的 `tool_duration_sec`（keyframe/video/character 已入库；music 写 additional_data）聚合 p50/p80。

⚠️ 所有数值都是**单次调用的有效墙钟**（已含 provider 排队/轮询），并发由 time_estimation 里的 concurrency 因子处理。
"""
from __future__ import annotations

from typing import Any, Optional

from ..models.tool_enums import ToolType


# ── 分辨率系数（相对 720p）────────────────────────────────
_RESOLUTION_MULT = {
    "480p": 0.75,
    "720p": 1.0,
    "1080p": 1.35,
}


def _resolution_key(resolution: Any) -> str:
    """把 Resolution 枚举 / 字符串归一到 '480p'|'720p'|'1080p'，默认 1080p。"""
    val = getattr(resolution, "value", None) or resolution
    if isinstance(val, str) and val in _RESOLUTION_MULT:
        return val
    return "1080p"


def _tool_value(tool_type: Any) -> str:
    return str(getattr(tool_type, "value", None) or tool_type or "").lower()


# ── 图片工具：单张关键帧/角色图耗时（按 ToolType 查表，与视频 _VIDEO_REF_SEC_720P_5S 同构）──
# REF 为「单张 @720p」有效墙钟（含 provider 排队/轮询），unit = REF × 分辨率系数（720p=1.0）。
# tool_type 用 cost_estimation._image_tool_type_from_user_option 解析出的同一个 ToolType（与计费同源）。
#
# 校准状态：2026-06-17 scripts/probe_image_timing.py 实测 T2I @720p（每工具 3-4 张取 p50）：
#   nano_banana≈8.7-12.6 / nano_banana_2≈13.7 / nano_banana_pro≈20.2 / seedream≈13.6 /
#   gpt_image_2≈p50 57-63（方差极大，单次曾飙到 229s，按 p50 取 60）。
# 下方数值即按该 probe p50 校准；后续可用生产 tool_duration_sec 聚合替换（不改结构）。
_IMAGE_REF_SEC_720P_BY_TOOL = {
    ToolType.GEMINI_2_5_FLASH_IMAGE: 10.0,        # nano_banana（fast，probe p50≈8.7-12.6）
    ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW: 14.0,  # nano_banana_2 / AUTO（flash，probe p50≈13.7）
    ToolType.GEMINI_3_PRO_IMAGE_PREVIEW: 20.0,    # nano_banana_pro（probe p50≈20.2，原 40 严重高估）
    ToolType.SEEDREAM_V4_5: 14.0,                 # seedream（probe p50≈13.6）
    ToolType.GPT_IMAGE_2: 60.0,                   # gpt_image_2（slow，probe p50≈57-63，方差大）
}
# 未入表工具的兜底：480p/720p/1080p 基准（秒）+ 慢模型 hint（×mult）。
_IMAGE_BASE_DEFAULT = 14.0
_IMAGE_BASE_BY_RES = {
    "480p": 10.5,
    "720p": 12.0,
    "1080p": 18.0,
}
# 高质量模型更慢（命名含 Pro / GPT Image / 4k 的未入表工具）
_IMAGE_SLOW_MODEL_MULT = 3.4
_IMAGE_SLOW_MODEL_HINTS = ("pro", "gpt_image", "gpt-image", "4k")


def get_image_unit_seconds(tool_type: Any = None, resolution: Any = None) -> float:
    """单张图片（关键帧/角色图/融合图）生成的有效墙钟秒数（按工具实测基准 + 分辨率缩放）。"""
    res_key = _resolution_key(resolution)
    ref = _IMAGE_REF_SEC_720P_BY_TOOL.get(tool_type)
    if ref is not None:
        # 基准在 720p，按分辨率系数缩放（720p=1.0）
        return round(ref * _RESOLUTION_MULT.get(res_key, 1.35), 2)
    # 未入表工具：回退到分辨率基准 × 慢模型 hint
    base = _IMAGE_BASE_BY_RES.get(res_key, _IMAGE_BASE_DEFAULT)
    tv = _tool_value(tool_type)
    if any(h in tv for h in _IMAGE_SLOW_MODEL_HINTS):
        base *= _IMAGE_SLOW_MODEL_MULT
    return round(base, 2)


# ── 视频工具：单镜头耗时（按 ToolType 查表，与 ToolService._PRICING_CONFIG 同构）──
# 模型：unit = REF(tool@720p,5s) + DURATION_SLOPE × (clip-5)，再 × 分辨率系数；lipsync 再 × 系数。
# REF 为 2026-06-15 探针实测的「(720p, 5s) 中位 api_elapsed」（scripts/probe_raw_tool_specs.py，
# 详见 docs/tool-timing-specs.md §3）。tool_type 用 cost_estimation._resolve_video_tool_type
# 解析出的同一个 ToolType（与计费同源）。
#
# ⚠️ 本轮 WaveSpeed 负载偏高，非 fast 的 Seedance 2.0 / *Turbo 系列出现大量长轮询（1080p<720p，
#    物理上不可能），属 provider 排队污染而非工具固有耗时——这些**不入表**，走 _VIDEO_FALLBACK_REF_SEC，
#    待非高峰重测或生产 tool_duration_sec 聚合后再补。
_VIDEO_REF_SEC_720P_5S = {
    ToolType.SEEDANCE_V1_PRO_FAST: 35.0,
    ToolType.SEEDANCE_V1_5_PRO_FAST: 88.0,
    ToolType.SEEDANCE_2_FAST_I2V: 104.0,
    ToolType.SEEDANCE_2_FAST_T2V: 115.0,
    ToolType.SEEDANCE_2_FAST_I2V_TURBO: 145.0,
    ToolType.SEEDANCE_2_FAST_T2V_TURBO: 175.0,
    ToolType.WAN_2_5_I2V: 80.0,
    ToolType.WAN_2_6_FLASH_I2V: 35.0,
    ToolType.KLING_V3_STD: 55.0,
    ToolType.HAPPYHORSE_1_0_I2V: 50.0,
    ToolType.HAPPYHORSE_1_1_I2V: 50.0,
    ToolType.SORA_2: 84.0,
    ToolType.SORA_2_PRO: 165.0,
}
# 未入表工具（排队污染的 Seedance 2.0 非 fast 系列、Lite 等）的兜底基准 @720p,5s。
# = 全工具 (720p,5s) 总体中位量级；turbo/fast 命名再打折（仅兜底路径用）。
_VIDEO_FALLBACK_REF_SEC = 80.0
_VIDEO_FALLBACK_TURBO_MULT = 0.85
_VIDEO_FALLBACK_TURBO_HINTS = ("turbo", "fast")
# 每多 1 秒成片增加的耗时（720p 网格 84s→99s/5s≈3，取保守 4）。
_VIDEO_DURATION_SLOPE_SEC = 4.0
# lipsync / speech-to-video 通常更慢
_VIDEO_LIPSYNC_MULT = 1.25
_VIDEO_LIPSYNC_HINTS = ("lipsync", "avatar", "speech_to_video", "s2v")


def get_video_unit_seconds(
    tool_type: Any = None,
    resolution: Any = None,
    clip_duration_sec: float = 5.0,
    is_lipsync: bool = False,
) -> float:
    """单镜头视频生成的有效墙钟秒数（按工具实测基准 + 时长/分辨率/lipsync 调整）。"""
    clip = max(1.0, float(clip_duration_sec or 5.0))
    ref = _VIDEO_REF_SEC_720P_5S.get(tool_type)
    if ref is None:
        ref = _VIDEO_FALLBACK_REF_SEC
        tv = _tool_value(tool_type)
        if any(h in tv for h in _VIDEO_FALLBACK_TURBO_HINTS):
            ref *= _VIDEO_FALLBACK_TURBO_MULT
    # 基准在 (720p, 5s)：先按时长补差，再按分辨率缩放（720p 系数为 1.0）
    unit = ref + _VIDEO_DURATION_SLOPE_SEC * (clip - 5.0)
    unit *= _RESOLUTION_MULT.get(_resolution_key(resolution), 1.35)
    tv = _tool_value(tool_type)
    if is_lipsync or any(h in tv for h in _VIDEO_LIPSYNC_HINTS):
        unit *= _VIDEO_LIPSYNC_MULT
    return round(max(1.0, unit), 2)


# ── 旁白 TTS（Minimax Speech + LLM 编排，按镜头 batch 并发）──────
NARRATION_BATCH_SIZE = 5
NARRATION_BATCH_WALL_SEC = 30.0


def get_narration_step_seconds(shot_count: int) -> float:
    """Product Launch 旁白步：ceil(镜头数 / batch) × 单批墙钟。"""
    n = max(1, int(shot_count or 1))
    batches = (n + NARRATION_BATCH_SIZE - 1) // NARRATION_BATCH_SIZE
    return round(batches * NARRATION_BATCH_WALL_SEC, 2)


# ── 音乐（Suno）：单曲耗时（轮询主导，对时长不敏感）──────
# 实测（scripts/probe_music_timing.py，2026-06-15，instrumental）：
#   30s 目标 p50≈81s / mean≈87s；60s 目标 p50≈60s / mean≈65s。
# 取 ~90s（含 auto_lyrics + 转写额外开销的保守值）；原 180s 偏高一倍。
_MUSIC_BASE_SEC = 90.0


def get_music_seconds(target_duration: Optional[float] = None) -> float:
    """Suno 单次音乐生成的有效墙钟秒数（创建 + 轮询）。"""
    return _MUSIC_BASE_SEC


# ── LLM / 固定阶段常数（非工具主导的 step）───────────────
# 这些 step 以 LLM 调用为主，给一个经验常数即可（拆自前端 fixedTime=240）。
STEP_FIXED_SECONDS = {
    "analysis": 40.0,       # user_input_analysis + video_analysis（含可选 Gemini 视频分析）
    "story_style": 100.0,   # outline LLM + 主角色图生成
    "visual": 40.0,         # visual_elements_matching + character_fusion
    "scenes": 60.0,         # scene_generation LLM
    "final": 60.0,          # video_segments + video_assembly（FFmpeg/MSC）
}

# 角色图数量兜底（story_style 里主角色图生成数，首包未知时用）
DEFAULT_CHARACTER_IMAGE_COUNT = 2


# ── 并发因子（墙钟 ≠ 串行求和的关键）──────────────────────
# 关键帧/视频是批量并发生成，N 个的墙钟 ≈ ceil(N / concurrency) × 单耗时。
# 并发数**不在本文件硬编码**：time_estimation 直接复用生产实际值
# prompt_utils.get_concurrency_limit("keyframe_generation"|"video_generation")
# （即 CONCURRENCY_LIMITS，当前均为 10），与真正控制 semaphore 的来源单一同源。


# ── 失败/一致性重试导致的时间放大（与计费的 retry 倍数同源）─
KEYFRAME_RETRY_TIME_MULT = 1.2
VIDEO_RETRY_TIME_MULT = 1.25


# ── 各 step 的 LLM / 编排串行 overhead（图片/视频生成之外的「非工具」耗时）──
# 这些是 image/video 工具调用之外、但每次必然发生的串行 LLM/编排时间，原模型完全没算，
# 导致 story_style/visual/storyboards/shots 被严重低估。数值来自 2026-06-17 thread_admin_7b7d3c9f：
#   - 大纲(outline) LLM ≈ 40s（story_style 的文本部分）
#   - 角色清单(character list) LLM ≈ 30s（出角色 schema，图前）
#   - 视觉元素匹配(visual_elements_matching) LLM ≈ 15s
#   - 分镜前置：first_frame_revision(≈29s) + per_shot_routing + storyboard_detail ≈ 50s
#   - 单关键帧除图片外还有一次 prompt LLM ≈ 10s（generate_single_keyframe_with_semaphore）
#   - 关键帧「两轮流式生成」≈ 1.8× 单轮
#   - 视频每批先批量 prompt 生成 + 评估修正 ≈ 30s（generate_batch_video_prompts + evaluate）
OUTLINE_LLM_SEC = 40.0
CHARACTER_LIST_LLM_SEC = 30.0
VISUAL_ELEMENTS_LLM_SEC = 15.0
STORYBOARD_PRE_LLM_SEC = 50.0
KEYFRAME_PROMPT_LLM_SEC = 10.0
KEYFRAME_ROUND_FACTOR = 1.8
VIDEO_PROMPT_OVERHEAD_SEC = 30.0


# ── 26 个 LangGraph node → 8 个用户可见 step 映射 ──────────
# 用于把节点级耗时（stage_timings）折叠回用户 step 维度做统计/对账。
NODE_TO_STEP = {
    "user_input_analysis": "analysis",
    "video_analysis": "analysis",
    "music_generation": "music",
    "music_bgm_generation": "music",
    "outline_generation": "story_style",
    "main_character_design": "story_style",
    "visual_elements_matching": "visual",
    "character_fusion": "visual",
    "scene_generation": "scenes",
    "storyboard_detail_generation": "narration",
    "storyboard_first_frame_revision": "storyboards",
    "per_shot_generation_routing": "storyboards",
    "keyframe_generation": "storyboards",
    "keyframe_reflection": "storyboards",
    "narration_generation": "narration",
    "video_generation": "shots",
    "video_segments": "final",
    "video_assembly": "final",
}
