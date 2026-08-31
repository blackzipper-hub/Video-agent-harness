"""Music-video audio planning helpers (deterministic, no network).

Used by media.audio_analyze: pick a master window, then split into Seedance-safe
segments (4–15s). Trim/mix are separate capabilities.
"""
from __future__ import annotations

import math
from typing import Any


SEEDANCE_MAX_AUDIO_SEC = 15.0
SEEDANCE_MIN_CLIP_SEC = 4.0


def resolve_master_window(
    audio_duration_sec: float,
    *,
    start_sec: float | None = None,
    end_sec: float | None = None,
    target_duration_sec: float | None = None,
) -> tuple[float, float]:
    """Return [start, end) on the source track for the master BGM window."""
    duration = float(audio_duration_sec)
    if duration <= 0:
        raise ValueError("audio_duration_sec must be > 0")

    if start_sec is not None or end_sec is not None:
        start = float(start_sec or 0.0)
        end = float(end_sec) if end_sec is not None else duration
        if start < 0:
            raise ValueError("start_sec must be >= 0")
        if end <= start:
            raise ValueError("end_sec must be > start_sec")
        if start >= duration:
            raise ValueError("start_sec must be < audio duration")
        return start, min(end, duration)

    if target_duration_sec is not None:
        target = float(target_duration_sec)
        if target <= 0:
            raise ValueError("target_duration_sec must be > 0")
        if target >= duration - 1e-6:
            return 0.0, duration
        # Center crop — matches smart_clip heuristic_center when no transcription.
        start = max(0.0, (duration - target) / 2.0)
        return start, start + target

    return 0.0, duration


def plan_seedance_segments(
    window_duration_sec: float,
    *,
    window_start_sec: float = 0.0,
    max_segment_sec: float = SEEDANCE_MAX_AUDIO_SEC,
    min_segment_sec: float = SEEDANCE_MIN_CLIP_SEC,
) -> list[dict[str, Any]]:
    """Split a master window into equal-ish Seedance reference slices.

    Each segment duration is in [min_segment_sec, max_segment_sec] when the
    window is long enough; shorter windows yield a single segment.
    """
    window = float(window_duration_sec)
    if window <= 0:
        raise ValueError("window_duration_sec must be > 0")
    max_seg = float(max_segment_sec)
    min_seg = float(min_segment_sec)
    if max_seg <= 0 or min_seg <= 0:
        raise ValueError("segment bounds must be > 0")
    if min_seg > max_seg:
        raise ValueError("min_segment_sec must be <= max_segment_sec")

    origin = float(window_start_sec)
    if window <= max_seg + 1e-9:
        return [_segment(0, origin, window)]

    n = max(1, int(math.ceil(window / max_seg)))
    # Prefer fewer pieces as long as each stays <= max_seg.
    while n > 1 and window / (n - 1) <= max_seg + 1e-9:
        n -= 1
    # Avoid tiny tails under min_seg when the whole window can support it.
    while n > 1 and window / n < min_seg - 1e-9:
        n -= 1
    while window / n > max_seg + 1e-9:
        n += 1

    seg_dur = window / n
    segments: list[dict[str, Any]] = []
    cursor = origin
    for index in range(n):
        # Last segment absorbs float remainder.
        if index == n - 1:
            dur = (origin + window) - cursor
        else:
            dur = seg_dur
        segments.append(_segment(index, cursor, dur))
        cursor += dur
    return segments


def _segment(index: int, start: float, duration: float) -> dict[str, Any]:
    start_r = round(float(start), 3)
    dur_r = round(float(duration), 3)
    return {
        "index": index,
        "start_sec": start_r,
        "duration_sec": dur_r,
        "end_sec": round(start_r + dur_r, 3),
    }


def build_audiomap(
    *,
    audio_url: str,
    audio_duration_sec: float,
    window_start_sec: float,
    window_end_sec: float,
    segments: list[dict[str, Any]],
    method: str,
    smart_clip: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stable analyze payload for skill / plan tasks."""
    master_duration = round(window_end_sec - window_start_sec, 3)
    return {
        "audio_url": audio_url,
        "audio_duration_sec": round(float(audio_duration_sec), 3),
        "master": {
            "start_sec": round(float(window_start_sec), 3),
            "end_sec": round(float(window_end_sec), 3),
            "duration_sec": master_duration,
        },
        "segments": segments,
        "segment_count": len(segments),
        "max_segment_sec": SEEDANCE_MAX_AUDIO_SEC,
        "method": method,
        "smart_clip": smart_clip,
        "alignment": {
            "spine": "music",
            "rule": (
                "Generate each Seedance clip with duration ≈ segment.duration_sec, "
                "concat in order, then media.mix_audio replace with master window."
            ),
        },
    }
