#!/usr/bin/env python3
"""
Test that pipeline (first-time segment) and request/sync (update video) paths behave consistently.

Both paths call process_segment_by_request -> process_and_merge_videos.
- When shot_durations are provided (from detailed_shot): both use per-shot trim/pad then concat (无调速).
- When shot_durations is None: merge uses concat then 阈值对齐 (freeze_or_tail_slow / speed_adjust, 无黑场垫片).
Pipeline (_process_single_segment) now passes shot_durations when it can resolve them from
detailed_shot (same as sync), so behavior is consistent.
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.services.agent.video.video_segments_service import (
    SegmentProcessRequest,
    process_segment_by_request,
)


# Same segment input used by both paths in real flow (e.g. segment 1: 2 videos, audio 5.5s)
SEGMENT_NUMBER = 1
VIDEO_URLS = ["https://cdn.example.com/v1.mp4", "https://cdn.example.com/v2.mp4"]
TARGET_DURATION = 5.5


@pytest.mark.asyncio
async def test_pipeline_style_passes_shot_durations_none():
    """Pipeline (first-time) path: _process_single_segment passes shot_durations=None."""
    req = SegmentProcessRequest(
        segment_number=SEGMENT_NUMBER,
        video_urls=VIDEO_URLS,
        target_duration=TARGET_DURATION,
        shot_durations=None,
        is_lipsync=False,
    )
    with patch(
        "app.services.agent.video.video_segments_service.process_and_merge_videos",
        new_callable=AsyncMock,
        return_value="https://cdn.example.com/merged_pipeline.mp4",
    ) as mock_merge:
        await process_segment_by_request(req)
    mock_merge.assert_awaited_once()
    assert mock_merge.await_args.kwargs["shot_durations"] is None
    assert mock_merge.await_args.kwargs["video_urls"] == VIDEO_URLS
    assert mock_merge.await_args.kwargs["target_duration"] == TARGET_DURATION
    assert mock_merge.await_args.kwargs["segment_number"] == SEGMENT_NUMBER


@pytest.mark.asyncio
async def test_sync_style_passes_shot_durations():
    """Sync (update) path: _sync_segments_audio_driven passes shot_durations from detailed_shots."""
    shot_durations = [2.75, 2.75]
    req = SegmentProcessRequest(
        segment_number=SEGMENT_NUMBER,
        video_urls=VIDEO_URLS,
        target_duration=TARGET_DURATION,
        shot_durations=shot_durations,
        is_lipsync=False,
    )
    with patch(
        "app.services.agent.video.video_segments_service.process_and_merge_videos",
        new_callable=AsyncMock,
        return_value="https://cdn.example.com/merged_sync.mp4",
    ) as mock_merge:
        await process_segment_by_request(req)
    mock_merge.assert_awaited_once()
    assert mock_merge.await_args.kwargs["shot_durations"] == shot_durations
    assert mock_merge.await_args.kwargs["video_urls"] == VIDEO_URLS
    assert mock_merge.await_args.kwargs["target_duration"] == TARGET_DURATION
    assert mock_merge.await_args.kwargs["segment_number"] == SEGMENT_NUMBER


@pytest.mark.asyncio
async def test_same_segment_input_different_merge_args_pipeline_vs_sync():
    """
    Same segment input (video_urls, target_duration) leads to different calls to
    process_and_merge_videos: pipeline uses shot_durations=None, sync uses a list.
    This is the root cause of 'first time segment+assemble wrong, update video correct'.
    """
    calls = []

    async def capture_merge(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs.copy()})
        return "https://cdn.example.com/merged.mp4"

    with patch(
        "app.services.agent.video.video_segments_service.process_and_merge_videos",
        new_callable=AsyncMock,
        side_effect=capture_merge,
    ):
        # Pipeline style (first-time segment service)
        req_pipeline = SegmentProcessRequest(
            segment_number=SEGMENT_NUMBER,
            video_urls=VIDEO_URLS,
            target_duration=TARGET_DURATION,
            shot_durations=None,
            is_lipsync=False,
        )
        await process_segment_by_request(req_pipeline)

        # Sync style (update video)
        req_sync = SegmentProcessRequest(
            segment_number=SEGMENT_NUMBER,
            video_urls=VIDEO_URLS,
            target_duration=TARGET_DURATION,
            shot_durations=[2.75, 2.75],
            is_lipsync=False,
        )
        await process_segment_by_request(req_sync)

    assert len(calls) == 2
    pipeline_kw = calls[0]["kwargs"]
    sync_kw = calls[1]["kwargs"]

    assert pipeline_kw["video_urls"] == sync_kw["video_urls"] == VIDEO_URLS
    assert pipeline_kw["target_duration"] == sync_kw["target_duration"] == TARGET_DURATION
    assert pipeline_kw["segment_number"] == sync_kw["segment_number"] == SEGMENT_NUMBER

    assert pipeline_kw["shot_durations"] is None
    assert sync_kw["shot_durations"] == [2.75, 2.75]

    # Only shot_durations differ; merge strategy therefore differs (concat+speed vs per-shot trim)
    for key in pipeline_kw:
        if key != "shot_durations":
            assert pipeline_kw[key] == sync_kw[key], f"Mismatch for key={key}"


@pytest.mark.asyncio
async def test_pipeline_and_sync_same_merge_when_same_shot_durations():
    """
    When both pipeline and request (sync) pass the same shot_durations, process_and_merge_videos
    is called with identical kwargs -> behavior is consistent (same per-shot trim/pad, no speed).
    """
    shot_durations = [2.75, 2.75]
    calls = []

    async def capture_merge(*args, **kwargs):
        calls.append(kwargs.copy())
        return "https://cdn.example.com/merged.mp4"

    with patch(
        "app.services.agent.video.video_segments_service.process_and_merge_videos",
        new_callable=AsyncMock,
        side_effect=capture_merge,
    ):
        # Pipeline style (first-time) with shot_durations from detailed_shot
        req_pipeline = SegmentProcessRequest(
            segment_number=SEGMENT_NUMBER,
            video_urls=VIDEO_URLS,
            target_duration=TARGET_DURATION,
            shot_durations=shot_durations,
            is_lipsync=False,
        )
        await process_segment_by_request(req_pipeline)

        # Sync style (update video) with same shot_durations
        req_sync = SegmentProcessRequest(
            segment_number=SEGMENT_NUMBER,
            video_urls=VIDEO_URLS,
            target_duration=TARGET_DURATION,
            shot_durations=shot_durations,
            is_lipsync=False,
        )
        await process_segment_by_request(req_sync)

    assert len(calls) == 2
    assert calls[0] == calls[1], "Pipeline and sync must call merge with identical kwargs when shot_durations are the same"
    assert calls[0]["shot_durations"] == shot_durations
    assert calls[0]["video_urls"] == VIDEO_URLS
    assert calls[0]["target_duration"] == TARGET_DURATION
