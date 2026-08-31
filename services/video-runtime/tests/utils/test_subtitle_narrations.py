"""Unit tests for narration-driven subtitle segment builder."""
import pytest
from types import SimpleNamespace

from app.utils.subtitle_utils import (
    SubtitleSegment,
    create_subtitle_segments_from_narrations,
)


def _version(text: str, duration: float, success: bool = True, enhanced_prompt: str = None):
    return SimpleNamespace(
        narration_text=text,
        enhanced_prompt=enhanced_prompt if enhanced_prompt is not None else text,
        duration=duration,
        success=success,
    )


def test_create_subtitle_segments_from_narrations_orders_by_shot_and_accumulates_time():
    narrations_data = {
        2: {"version": _version("Second line.", 2.0)},
        1: {"version": _version("First line.", 3.0)},
    }
    segments = create_subtitle_segments_from_narrations(narrations_data)
    assert len(segments) == 2
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 3.0
    assert segments[0].text == "First line."
    assert segments[1].start_time == 3.0
    assert segments[1].end_time == 5.0
    assert segments[1].text == "Second line."


def test_create_subtitle_segments_skips_failed_versions():
    narrations_data = {
        1: {"version": _version("Skip me", 1.0, success=False)},
        2: {"version": _version("Keep me", 2.5)},
    }
    segments = create_subtitle_segments_from_narrations(narrations_data)
    assert len(segments) == 1
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 2.5


def test_create_subtitle_segments_empty_input():
    assert create_subtitle_segments_from_narrations({}) == []


def test_create_subtitle_segments_prefers_enhanced_prompt_over_narration_text():
    narrations_data = {
        1: {
            "version": _version(
                "Original storyboard line.",
                3.0,
                enhanced_prompt="Spoken optimized line.",
            ),
        },
    }
    segments = create_subtitle_segments_from_narrations(narrations_data)
    assert len(segments) == 1
    assert segments[0].text == "Spoken optimized line."


def test_create_subtitle_segments_with_shot_timeline_includes_silent_prefix():
    narrations_data = {
        2: {"version": _version("Hello world.", 2.85)},
    }
    shot_timeline = [(1, 0.0, 3.04), (2, 3.04, 2.85)]
    segments = create_subtitle_segments_from_narrations(
        narrations_data, shot_timeline=shot_timeline,
    )
    assert len(segments) == 1
    assert segments[0].start_time == 3.04
    assert segments[0].end_time == pytest.approx(5.89, abs=0.01)
    assert segments[0].text == "Hello world."
