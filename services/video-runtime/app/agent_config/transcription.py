"""
转录引擎 per-method 配置（与 user_options.VideoGenerationTool 正交）。

键：hybrid / gemini 等 DefaultValues.TRANSCRIPTION_METHOD 取值。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from app.models.tool_enums import AudioSegmentGranularity, DefaultValues


@dataclass(frozen=True)
class TranscriptionEngineProfile:
    """单个转录引擎的后端旋钮。"""

    granularity: AudioSegmentGranularity
    # 预留：enable_fill_gaps, merge_short_empty_threshold_sec 等


TRANSCRIPTION_ENGINE_PROFILES: Dict[str, TranscriptionEngineProfile] = {
    "gemini": TranscriptionEngineProfile(
        granularity=AudioSegmentGranularity.SENTENCE,
    ),
    "hybrid": TranscriptionEngineProfile(
        granularity=AudioSegmentGranularity.SENTENCE,
    ),
}

# 与历史 dict 形状兼容（tool_enums / 文档引用）
TRANSCRIPTION_METHOD_CONFIG: Dict[str, Dict[str, Any]] = {
    name: {"granularity": prof.granularity.value}
    for name, prof in TRANSCRIPTION_ENGINE_PROFILES.items()
}


def get_transcription_profile(method: str) -> TranscriptionEngineProfile:
    """按 transcription method 名取配置；未知 method 退回 DefaultValues 兜底。"""
    key = (method or "").strip().lower()
    prof = TRANSCRIPTION_ENGINE_PROFILES.get(key)
    if prof is not None:
        return prof
    return TranscriptionEngineProfile(
        granularity=AudioSegmentGranularity.from_value(DefaultValues.AUDIO_SEGMENT_GRANULARITY),
    )


def get_transcription_method_config(method: str) -> Dict[str, Any]:
    """按 method 名取配置 dict；未知 method 返回 {}（与旧 tool_enums 行为一致）。"""
    key = (method or "").strip().lower()
    if key in TRANSCRIPTION_METHOD_CONFIG:
        return dict(TRANSCRIPTION_METHOD_CONFIG[key])
    return {}


def get_audio_segment_granularity_for_method(method: str) -> AudioSegmentGranularity:
    """单一入口：按 method 解析 granularity。"""
    cfg = get_transcription_method_config(method)
    raw = cfg.get("granularity")
    if raw is None:
        raw = DefaultValues.AUDIO_SEGMENT_GRANULARITY
    return AudioSegmentGranularity.from_value(raw)
