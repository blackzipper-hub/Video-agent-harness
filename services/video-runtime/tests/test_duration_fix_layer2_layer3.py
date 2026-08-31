"""
Layer2 和 Layer3 时长修复验证测试

Layer2 修复：_align_segment_video_to_target_duration
  - 延长场景：改为两步法 freeze_or_tail_slow(target+0.1) + trim_only(target)
  - 截短场景：直接 trim_only(target)

Layer3 修复：_process_single_segment 写 DB 时用实际 probe 时长，不用 target_duration
"""

import pytest
from unittest.mock import AsyncMock, patch, call


# ─── Layer 2 测试 ─────────────────────────────────────────────

class TestAlignSegmentVideoTwoStep:
    """验证延长场景用两步法：freeze(target+0.1) → trim_only(target)"""

    @pytest.mark.asyncio
    async def test_extension_calls_freeze_then_trim(self):
        """视频短于目标 → 先 freeze 到 target+0.1，再 trim_only 到 target"""
        from app.services.agent.video.video_segments_service import (
            _align_segment_video_to_target_duration,
        )

        freeze_url = "https://cdn.example.com/frozen_overshoot.mp4"
        trimmed_url = "https://cdn.example.com/trimmed_exact.mp4"

        with patch(
            "app.services.agent.video.video_segments_service.msc"
        ) as mock_msc:
            mock_msc.video_info = AsyncMock(return_value={"duration": 1.41})
            mock_msc.video_trim = AsyncMock(side_effect=[
                {"result_url": freeze_url, "duration": 1.52},   # step1: freeze overshoot
                {"result_url": trimmed_url, "duration": 1.50},  # step2: trim exact
            ])

            result = await _align_segment_video_to_target_duration(
                "https://cdn.example.com/lipsync_short.mp4",
                1.50,
                "test_run_001",
                segment_number=2,
                allow_speed_adjust=False,
            )

        assert result == trimmed_url, f"应返回 trim_only 结果，实际: {result}"
        assert mock_msc.video_trim.call_count == 2, "应调用两次 video_trim"
        # 第一次：freeze_or_tail_slow 到 1.60 (target+0.1)
        first_call = mock_msc.video_trim.call_args_list[0]
        assert first_call.args[1] == pytest.approx(1.60, abs=0.01), \
            f"第一步 target 应为 1.60 (1.50+0.1), 实际: {first_call.args[1]}"
        assert first_call.kwargs.get("mode") == "freeze_or_tail_slow"
        # 第二次：trim_only 到 1.50
        second_call = mock_msc.video_trim.call_args_list[1]
        assert second_call.args[1] == pytest.approx(1.50, abs=0.001), \
            f"第二步 target 应为 1.50, 实际: {second_call.args[1]}"
        assert second_call.kwargs.get("mode") == "trim_only"

    @pytest.mark.asyncio
    async def test_truncation_calls_trim_only_directly(self):
        """视频长于目标 → 直接 trim_only，不走 freeze"""
        from app.services.agent.video.video_segments_service import (
            _align_segment_video_to_target_duration,
        )

        trimmed_url = "https://cdn.example.com/trimmed.mp4"

        with patch(
            "app.services.agent.video.video_segments_service.msc"
        ) as mock_msc:
            mock_msc.video_info = AsyncMock(return_value={"duration": 4.54})
            mock_msc.video_trim = AsyncMock(return_value={
                "result_url": trimmed_url, "duration": 4.40,
            })

            result = await _align_segment_video_to_target_duration(
                "https://cdn.example.com/lipsync_long.mp4",
                4.40,
                "test_run_002",
                segment_number=3,
                allow_speed_adjust=False,
            )

        assert result == trimmed_url
        assert mock_msc.video_trim.call_count == 1, "截短只应调用一次"
        trim_call = mock_msc.video_trim.call_args_list[0]
        assert trim_call.kwargs.get("mode") == "trim_only", \
            f"截短场景应用 trim_only, 实际 mode={trim_call.kwargs.get('mode')}"

    @pytest.mark.asyncio
    async def test_within_tolerance_skips_trim(self):
        """误差 ≤ 0.02s 时跳过对齐，直接返回原 URL"""
        from app.services.agent.video.video_segments_service import (
            _align_segment_video_to_target_duration,
        )
        original_url = "https://cdn.example.com/already_good.mp4"

        with patch(
            "app.services.agent.video.video_segments_service.msc"
        ) as mock_msc:
            mock_msc.video_info = AsyncMock(return_value={"duration": 4.399})
            mock_msc.video_trim = AsyncMock()

            result = await _align_segment_video_to_target_duration(
                original_url, 4.40, "test_run_003",
                segment_number=4, allow_speed_adjust=False,
            )

        assert result == original_url
        mock_msc.video_trim.assert_not_called()

    @pytest.mark.asyncio
    async def test_extension_large_gap_also_two_step(self):
        """大于 SEGMENT_LIGHT_ALIGN_MAX_SEC 但 allow_speed_adjust=False 时仍用两步法"""
        from app.services.agent.video.video_segments_service import (
            _align_segment_video_to_target_duration,
            SEGMENT_LIGHT_ALIGN_MAX_SEC,
        )
        # gap = 3.0s > SEGMENT_LIGHT_ALIGN_MAX_SEC(2.0)
        input_dur = 3.0
        target_dur = 6.0

        freeze_url = "https://cdn.example.com/frozen.mp4"
        trim_url = "https://cdn.example.com/trimmed.mp4"

        with patch(
            "app.services.agent.video.video_segments_service.msc"
        ) as mock_msc:
            mock_msc.video_info = AsyncMock(return_value={"duration": input_dur})
            mock_msc.video_trim = AsyncMock(side_effect=[
                {"result_url": freeze_url, "duration": target_dur + 0.15},
                {"result_url": trim_url, "duration": target_dur},
            ])

            result = await _align_segment_video_to_target_duration(
                "https://cdn.example.com/video.mp4",
                target_dur, "test_run_004",
                segment_number=5, allow_speed_adjust=False,
            )

        # Large gap, lipsync => no speed adjust => should still use freeze+trim two-step
        assert result == trim_url


# ─── Layer 3 测试 ─────────────────────────────────────────────

class TestProcessSingleSegmentActualDuration:
    """验证 _process_single_segment 写 DB 用实际 probe 时长，而非 target_duration"""

    def _make_merge_result(self, url="https://cdn.example.com/merged.mp4"):
        from unittest.mock import MagicMock
        from app.models.video_state import VideoSegmentStatus
        mr = MagicMock()
        mr.success = True
        mr.merged_video_url = url
        mr.status = VideoSegmentStatus.SUCCESS
        mr.error_msg = None
        mr.failed_video_count = 0
        mr.total_video_count = 1
        return mr

    def _make_mapping_info(self, duration=5.0, audio_url="https://cdn.example.com/audio.mp3"):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.duration = duration
        m.audio_url = audio_url
        m.videos = ["https://cdn.example.com/shot.mp4"]
        m.shot_numbers = [1]
        return m

    @pytest.mark.asyncio
    async def test_writes_actual_probe_duration_to_result(self):
        """probe 结果与 target 不一致时，VideoSegmentResult.duration 应用 probe 值"""
        from app.services.agent.video.video_segments_service import (
            _process_single_segment,
            merge_segment_videos,
        )

        target_duration = 5.0
        actual_probe_duration = 4.95   # 50ms short (Layer1/Layer2 residual error)
        mapping_info = self._make_mapping_info(duration=target_duration)
        merge_result = self._make_merge_result()

        with patch(
            "app.services.agent.video.video_segments_service.merge_segment_videos",
            new_callable=AsyncMock,
            return_value=merge_result,
        ) as mock_merge, patch(
            "app.services.agent.video.video_segments_service.msc"
        ) as mock_msc:
            mock_msc.video_info = AsyncMock(return_value={"duration": actual_probe_duration})

            result = await _process_single_segment(
                segment_number=1,
                audio_segment_id="seg_001",
                mapping_info=mapping_info,
                video_assembly_data=_make_mock_assembly_data(),
                send_event_func=AsyncMock(),
            )

        assert result.duration == pytest.approx(actual_probe_duration, abs=0.001), \
            f"duration 应为实际 probe {actual_probe_duration}，实际: {result.duration}"

    @pytest.mark.asyncio
    async def test_falls_back_to_target_if_probe_fails(self):
        """probe 失败时，VideoSegmentResult.duration 回退到 target_duration"""
        from app.services.agent.video.video_segments_service import (
            _process_single_segment,
        )

        target_duration = 5.0
        mapping_info = self._make_mapping_info(duration=target_duration)
        merge_result = self._make_merge_result()

        with patch(
            "app.services.agent.video.video_segments_service.merge_segment_videos",
            new_callable=AsyncMock,
            return_value=merge_result,
        ), patch(
            "app.services.agent.video.video_segments_service.msc"
        ) as mock_msc:
            mock_msc.video_info = AsyncMock(side_effect=Exception("probe failed"))

            result = await _process_single_segment(
                segment_number=1,
                audio_segment_id="seg_001",
                mapping_info=mapping_info,
                video_assembly_data=_make_mock_assembly_data(),
                send_event_func=AsyncMock(),
            )

        assert result.duration == pytest.approx(target_duration, abs=0.001), \
            f"probe失败时应回退到 target {target_duration}，实际: {result.duration}"


def _make_mock_assembly_data():
    from unittest.mock import MagicMock
    d = MagicMock()
    d.video_generations = []
    d.music_data = {}
    d.narrations_data = {}
    d.story_outline = MagicMock()
    d.story_outline.uuid = "outline_001"
    return d
