#!/usr/bin/env python3
"""测试 process_segment_by_request 统一入口（segment by request 改造）"""

import pytest
from unittest.mock import AsyncMock, patch

from app.services.agent.video.video_segments_service import (
    SegmentProcessRequest,
    process_segment_by_request,
)


@pytest.mark.asyncio
async def test_process_segment_by_request_placeholder_no_videos_raises_without_placeholder_duration():
    """无视频且未传 placeholder_duration 时应抛出 ValueError"""
    req = SegmentProcessRequest(
        segment_number=1,
        video_urls=[],
        target_duration=0.0,
        placeholder_duration=None,
    )
    with pytest.raises(ValueError, match="no video_urls and no placeholder_duration"):
        await process_segment_by_request(req)


@pytest.mark.asyncio
async def test_process_segment_by_request_placeholder_returns_url_when_placeholder_duration_given():
    """无视频但传 placeholder_duration 时生成黑屏并返回上传 URL（mock 上传）"""
    fake_url = "https://cdn.example.com/videos/black_segment_1_abc123.mp4"
    req = SegmentProcessRequest(
        segment_number=1,
        video_urls=[],
        target_duration=0.0,
        placeholder_duration=1.5,
    )
    with patch(
        "app.services.agent.video.video_segments_service.upload_file_from_temp",
        new_callable=AsyncMock,
        return_value=fake_url,
    ):
        with patch(
            "app.utils.video_utils.create_black_placeholder_video",
            new_callable=AsyncMock,
        ):
            result = await process_segment_by_request(req)
    assert result == fake_url


@pytest.mark.asyncio
async def test_process_segment_by_request_lipsync_dispatches_to_merge_and_trim():
    """is_lipsync=True 时应调用 merge_and_trim_lipsync_videos（不调速）"""
    req = SegmentProcessRequest(
        segment_number=2,
        video_urls=["https://example.com/v1.mp4"],
        target_duration=3.0,
        is_lipsync=True,
    )
    with patch(
        "app.services.agent.video.video_segments_service.merge_and_trim_lipsync_videos",
        new_callable=AsyncMock,
        return_value="https://cdn.example.com/lipsync_2.mp4",
    ) as mock_lipsync:
        with patch(
            "app.services.agent.video.video_segments_service.process_and_merge_videos",
            new_callable=AsyncMock,
        ) as mock_merge:
            result = await process_segment_by_request(req)
    mock_lipsync.assert_awaited_once()
    assert mock_lipsync.await_args.kwargs["video_urls"] == ["https://example.com/v1.mp4"]
    assert mock_lipsync.await_args.kwargs["target_duration"] == 3.0
    assert mock_lipsync.await_args.kwargs["segment_number"] == 2
    mock_merge.assert_not_awaited()
    assert result == "https://cdn.example.com/lipsync_2.mp4"


@pytest.mark.asyncio
async def test_process_segment_by_request_normal_dispatches_to_process_and_merge():
    """is_lipsync=False 时应调用 process_and_merge_videos（可带 shot_durations）"""
    req = SegmentProcessRequest(
        segment_number=3,
        video_urls=["https://example.com/a.mp4", "https://example.com/b.mp4"],
        target_duration=5.0,
        shot_durations=[2.0, 3.0],
        is_lipsync=False,
    )
    with patch(
        "app.services.agent.video.video_segments_service.merge_and_trim_lipsync_videos",
        new_callable=AsyncMock,
    ) as mock_lipsync:
        with patch(
            "app.services.agent.video.video_segments_service.process_and_merge_videos",
            new_callable=AsyncMock,
            return_value="https://cdn.example.com/merged_3.mp4",
        ) as mock_merge:
            result = await process_segment_by_request(req)
    mock_merge.assert_awaited_once()
    assert mock_merge.await_args.kwargs["video_urls"] == req.video_urls
    assert mock_merge.await_args.kwargs["target_duration"] == 5.0
    assert mock_merge.await_args.kwargs["segment_number"] == 3
    assert mock_merge.await_args.kwargs["shot_durations"] == [2.0, 3.0]
    mock_lipsync.assert_not_awaited()
    assert result == "https://cdn.example.com/merged_3.mp4"


def test_segment_process_request_dataclass():
    """SegmentProcessRequest 字段与默认值"""
    r = SegmentProcessRequest(
        segment_number=1,
        video_urls=["u1"],
        target_duration=2.0,
    )
    assert r.segment_number == 1
    assert r.video_urls == ["u1"]
    assert r.target_duration == 2.0
    assert r.shot_durations is None
    assert r.is_lipsync is False
    assert r.placeholder_duration is None
