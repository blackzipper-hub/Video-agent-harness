"""
测试三个修改点：
1. split_segments_at_section_boundaries (transcribe 后处理)
2. _segments_in_section (防御性修复)
3. merge_segment_videos / resolve_shot_durations (公用合并方法 + per-shot 黑屏占位)
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from dataclasses import dataclass
from typing import List, Optional

from app.models.video_state import AudioSegment
from app.tools.transcribe.gemini import (
    merge_micro_duration_segments,
    postprocess_transcription,
    prune_sections_without_segment_overlap,
    split_segments_at_section_boundaries,
)
from app.services.agent.video.outline_generation_service import _segments_in_section


# ========== 1. split_segments_at_section_boundaries ==========

class TestSplitSegmentsAtSectionBoundaries:
    """split_segments_at_section_boundaries 测试：拆分跨 section 边界的 segment。"""

    def _seg(self, id: int, start: float, end: float, text: str = "") -> AudioSegment:
        return AudioSegment(
            id=id, start=start, end=end, text=text,
            duration=round(end - start, 3), emotion="neutral", tempo="medium",
        )

    def test_no_sections_returns_unchanged(self):
        segs = [self._seg(0, 0.0, 5.0, "hello")]
        result = split_segments_at_section_boundaries(segs, [])
        assert len(result) == 1
        assert result[0].text == "hello"

    def test_no_segments_returns_empty(self):
        result = split_segments_at_section_boundaries([], [{"start_time": 0, "end_time": 10}])
        assert result == []

    def test_segment_within_section_not_split(self):
        segs = [self._seg(0, 2.0, 8.0, "verse")]
        sections = [{"start_time": 0.0, "end_time": 10.0}]
        result = split_segments_at_section_boundaries(segs, sections)
        assert len(result) == 1
        assert result[0].text == "verse"

    def test_segment_crossing_one_boundary(self):
        segs = [self._seg(0, 5.0, 15.0, "crossing")]
        sections = [
            {"start_time": 0.0, "end_time": 10.0},
            {"start_time": 10.0, "end_time": 20.0},
        ]
        result = split_segments_at_section_boundaries(segs, sections)
        assert len(result) == 2
        assert result[0].start == 5.0 and result[0].end == 10.0
        assert result[0].text == "crossing"
        assert result[1].start == 10.0 and result[1].end == 15.0
        assert result[1].text == ""

    def test_segment_crossing_two_boundaries(self):
        segs = [self._seg(0, 5.0, 25.0, "long")]
        sections = [
            {"start_time": 0.0, "end_time": 10.0},
            {"start_time": 10.0, "end_time": 20.0},
            {"start_time": 20.0, "end_time": 30.0},
        ]
        result = split_segments_at_section_boundaries(segs, sections)
        assert len(result) == 3
        assert result[0].text == "long"
        assert result[1].text == "" and result[1].start == 10.0
        assert result[2].text == "" and result[2].start == 20.0

    def test_boundary_very_close_to_segment_edge_not_split(self):
        segs = [self._seg(0, 9.98, 15.0, "edge")]
        sections = [
            {"start_time": 0.0, "end_time": 10.0},
            {"start_time": 10.0, "end_time": 20.0},
        ]
        result = split_segments_at_section_boundaries(segs, sections)
        assert len(result) == 1

    def test_multiple_segments_mixed(self):
        segs = [
            self._seg(0, 0.0, 8.0, "no cross"),
            self._seg(1, 8.0, 15.0, "cross at 10"),
            self._seg(2, 15.0, 19.0, "no cross"),
        ]
        sections = [
            {"start_time": 0.0, "end_time": 10.0},
            {"start_time": 10.0, "end_time": 20.0},
        ]
        result = split_segments_at_section_boundaries(segs, sections)
        assert len(result) == 4

    def test_ids_renumbered(self):
        segs = [self._seg(10, 0.0, 5.0, "a"), self._seg(20, 5.0, 15.0, "b")]
        sections = [
            {"start_time": 0.0, "end_time": 10.0},
            {"start_time": 10.0, "end_time": 20.0},
        ]
        result = split_segments_at_section_boundaries(segs, sections)
        assert [s.id for s in result] == [0, 1, 2]

    def test_vocal_presence_inherited(self):
        seg = self._seg(0, 5.0, 15.0, "vocal")
        seg.vocal_presence = True
        sections = [
            {"start_time": 0.0, "end_time": 10.0},
            {"start_time": 10.0, "end_time": 20.0},
        ]
        result = split_segments_at_section_boundaries([seg], sections)
        assert all(s.vocal_presence is True for s in result)

    def test_real_scenario_9062aecc(self):
        segs = [
            self._seg(0, 0.0, 30.0, "verse1"),
            self._seg(1, 30.0, 60.0, "chorus1"),
            self._seg(2, 60.0, 64.41, "interlude-cross"),
            self._seg(3, 64.41, 90.0, "verse2"),
        ]
        sections = [
            {"start_time": 0.0, "end_time": 30.0},
            {"start_time": 30.0, "end_time": 60.0},
            {"start_time": 60.0, "end_time": 63.0},
            {"start_time": 63.0, "end_time": 90.0},
        ]
        result = split_segments_at_section_boundaries(segs, sections)
        cross_splits = [s for s in result if s.start >= 60.0 and s.end <= 64.41]
        assert len(cross_splits) == 2
        assert cross_splits[0].end == 63.0
        assert cross_splits[1].start == 63.0

    def test_duration_computed_correctly(self):
        segs = [self._seg(0, 5.0, 15.0, "test")]
        sections = [
            {"start_time": 0.0, "end_time": 10.0},
            {"start_time": 10.0, "end_time": 20.0},
        ]
        result = split_segments_at_section_boundaries(segs, sections)
        for s in result:
            assert abs(s.duration - (s.end - s.start)) < 0.001

    def test_split_creates_1ms_then_merge_heals(self):
        """复现 prod：长段跨 11.199 与 11.2 两个 section 边界 → 1ms 中段；merge_micro 并入邻段。"""
        segs = [self._seg(0, 7.5, 11.899, "lyrics")]
        sections = [
            {"start_time": 0.0, "end_time": 11.199},
            {"start_time": 11.199, "end_time": 11.2},
            {"start_time": 11.2, "end_time": 20.0},
        ]
        split = split_segments_at_section_boundaries(segs, sections)
        assert len(split) == 3
        assert abs((split[1].end - split[1].start) - 0.001) < 0.0005
        healed = merge_micro_duration_segments(split, min_duration=0.05)
        assert len(healed) == 2
        assert healed[0].start == 7.5 and healed[0].end == 11.2
        assert healed[0].text == "lyrics"
        assert healed[1].start == 11.2 and healed[1].end == 11.899
        assert all((s.end - s.start) + 1e-9 >= 0.05 for s in healed)

    def test_section_boundary_short_empty_merged_into_prev(self):
        """曲式边界上的短空段并入上一段，过短尾段由 merge_micro 收拢。"""
        segs = [
            self._seg(0, 5.186, 7.900, "Cutie"),
            self._seg(1, 7.900, 8.059, ""),
            self._seg(2, 8.059, 9.654, "Music video"),
        ]
        sections = [
            {"section_type": "A", "start_time": 0.0, "end_time": 7.900},
            {"section_type": "B", "start_time": 7.900, "end_time": 20.0},
        ]
        out_segs, out_secs = postprocess_transcription(
            segs,
            sections,
            total_duration=20.0,
            fill_gaps_enabled=False,
        )
        assert len(out_segs) == 1
        assert out_segs[0].start == 5.186 and out_segs[0].end == 9.654
        assert "Cutie" in out_segs[0].text and "Music video" in out_segs[0].text
        assert len(out_secs) == 2

    def test_leading_fill_gap_merged_into_first_lyric(self):
        """fill_gaps 片头 0→0.479 空段应并入首句，不单独落库。"""
        segs = [
            self._seg(0, 0.0, 0.479, ""),
            self._seg(1, 0.479, 5.585, "Make your music video with Cutie"),
            self._seg(2, 5.585, 11.729, ""),
        ]
        sections = [
            {"section_type": "Chorus", "start_time": 0.0, "end_time": 19.47},
        ]
        out_segs, _ = postprocess_transcription(
            segs,
            sections,
            total_duration=19.47,
            fill_gaps_enabled=False,
        )
        assert len(out_segs) == 2
        assert out_segs[0].start == 0.0 and out_segs[0].end == 5.585
        assert "Cutie" in out_segs[0].text

    def test_bc435c56_style_outro_tail_merged_after_postprocess(self):
        """跨 Outro 边界切分后，空尾巴并入上一段；Outro section 仍与合并段重叠则保留。"""
        segs = [
            self._seg(0, 0.0, 5.585, "Make your music video with Cutie"),
            self._seg(1, 5.585, 11.729, ""),
            self._seg(2, 11.729, 15.479, "Music video"),
            self._seg(3, 15.479, 19.470, "Music video"),
        ]
        sections = [
            {"section_type": "Chorus", "start_time": 0.479, "end_time": 5.585},
            {"section_type": "Break", "start_time": 5.585, "end_time": 11.729},
            {"section_type": "Reprise", "start_time": 11.729, "end_time": 17.074},
            {"section_type": "Outro", "start_time": 17.074, "end_time": 19.470},
        ]
        out_segs, out_secs = postprocess_transcription(
            segs,
            sections,
            total_duration=19.47,
            fill_gaps_enabled=False,
        )
        assert len(out_segs) == 4
        assert not any(
            s.start >= 17.074 - 0.01 and not (s.text or "").strip()
            for s in out_segs
        )
        last = out_segs[-1]
        assert last.start == 15.479 and last.end == 19.470
        assert (last.text or "").strip()
        assert any(s.get("section_type") == "Outro" for s in out_secs)
        assert out_segs[0].start == 0.0 and out_segs[0].end == 5.585


class TestPruneSectionsWithoutSegmentOverlap:
    def test_drops_section_with_no_overlap(self):
        sections = [
            {"section_type": "Chorus", "start_time": 0.479, "end_time": 5.585},
            {"section_type": "Outro", "start_time": 17.074, "end_time": 19.470},
        ]
        segs = [
            AudioSegment(
                id=0, start=0.0, end=5.585, text="hook", duration=5.585,
                emotion=None, tempo=None,
            ),
        ]
        kept = prune_sections_without_segment_overlap(sections, segs)
        assert len(kept) == 1
        assert kept[0]["section_type"] == "Chorus"


class TestMergeMicroDurationSegments:
    """merge_micro_duration_segments：去掉 section 切分或模型产生的亚阈值废段。"""

    def _seg(self, id: int, start: float, end: float, text: str = "") -> AudioSegment:
        return AudioSegment(
            id=id,
            start=start,
            end=end,
            text=text,
            duration=round(end - start, 6),
            emotion="neutral",
            tempo="medium",
        )

    def test_empty(self):
        assert merge_micro_duration_segments([], min_duration=0.05) == []

    def test_no_micro_unchanged(self):
        segs = [self._seg(0, 0.0, 2.0, "a")]
        out = merge_micro_duration_segments(segs, min_duration=0.05)
        assert len(out) == 1
        assert out[0].start == 0.0 and out[0].end == 2.0

    def test_micro_merges_into_previous(self):
        segs = [self._seg(0, 0.0, 3.0, "a"), self._seg(1, 3.0, 3.001, "")]
        out = merge_micro_duration_segments(segs, min_duration=0.05)
        assert len(out) == 1
        assert out[0].end == 3.001
        assert out[0].text == "a"
        assert out[0].id == 0

    def test_micro_at_start_merges_into_next(self):
        segs = [self._seg(0, 0.0, 0.001, ""), self._seg(1, 0.001, 2.0, "b")]
        out = merge_micro_duration_segments(segs, min_duration=0.05)
        assert len(out) == 1
        assert out[0].start == 0.0 and out[0].end == 2.0
        assert out[0].text == "b"

    def test_chained_micros_single_pass_coalesce(self):
        segs = [
            self._seg(0, 0.0, 10.0, "a"),
            self._seg(1, 10.0, 10.001, ""),
            self._seg(2, 10.001, 10.03, ""),
            self._seg(3, 10.03, 15.0, "b"),
        ]
        out = merge_micro_duration_segments(segs, min_duration=0.05)
        assert len(out) == 2
        assert out[0].end == 10.03
        assert out[1].start == 10.03 and out[1].end == 15.0


# ========== 2. _segments_in_section ==========

class TestSegmentsInSection:

    @dataclass
    class FakeSeg:
        uuid: str
        start: float
        end: float

    def test_segment_fully_inside(self):
        segs = [self.FakeSeg("a", 2.0, 8.0)]
        assert _segments_in_section(segs, 0.0, 10.0) == ["a"]

    def test_segment_fully_outside(self):
        segs = [self.FakeSeg("a", 12.0, 18.0)]
        assert _segments_in_section(segs, 0.0, 10.0) == []

    def test_segment_crossing_boundary_no_double_assign(self):
        """修复前会双归：start=8 在 section1，不应归属 section2"""
        seg = self.FakeSeg("cross", 8.0, 12.0)
        assert _segments_in_section([seg], 0.0, 10.0) == ["cross"]
        assert _segments_in_section([seg], 10.0, 20.0) == []

    def test_segment_start_equals_section_start(self):
        segs = [self.FakeSeg("a", 10.0, 15.0)]
        assert _segments_in_section(segs, 10.0, 20.0) == ["a"]

    def test_segment_start_equals_section_end(self):
        segs = [self.FakeSeg("a", 10.0, 15.0)]
        assert _segments_in_section(segs, 0.0, 10.0) == []

    def test_no_double_assignment(self):
        segs = [
            self.FakeSeg("a", 0.0, 5.0),
            self.FakeSeg("b", 5.0, 12.0),
            self.FakeSeg("c", 12.0, 20.0),
        ]
        sections = [(0.0, 10.0), (10.0, 20.0)]
        all_assigned = []
        for start, end in sections:
            all_assigned.extend(_segments_in_section(segs, start, end))
        assert len(all_assigned) == len(set(all_assigned))
        assert set(all_assigned) == {"a", "b", "c"}

    def test_real_scenario_957e41e9(self):
        seg = self.FakeSeg("6fa0a6f5", 53.5, 64.8)
        assert _segments_in_section([seg], 36.7, 59.7) == ["6fa0a6f5"]
        assert _segments_in_section([seg], 59.7, 61.0) == []

    def test_empty_segments(self):
        assert _segments_in_section([], 0.0, 10.0) == []

    def test_multiple_segments_same_section(self):
        segs = [self.FakeSeg("a", 1.0, 3.0), self.FakeSeg("b", 3.0, 7.0), self.FakeSeg("c", 7.0, 9.0)]
        assert _segments_in_section(segs, 0.0, 10.0) == ["a", "b", "c"]

    def test_last_section_end_shorter_than_transcription_orphan_repro(self):
        """复现 prod：末段 video_audio_section [210,259)，转录末条 start=259,end=260。
        半开区间下 start<259 为假 → 默认最后一节仍漏收。"""
        tail = self.FakeSeg("b68aff50", 259.0, 260.0)
        prev = self.FakeSeg("prev", 250.0, 259.0)
        segs = [prev, tail]
        assert _segments_in_section(segs, 210.0, 259.0) == ["prev"]
        assert _segments_in_section(segs, 210.0, 259.0, is_last_section=False) == ["prev"]

    def test_last_section_flag_extends_end_to_timeline(self):
        """修复：is_last_section=True 时上界抬到 max(et, max(seg.end))，末秒归入。"""
        tail = self.FakeSeg("b68aff50", 259.0, 260.0)
        prev = self.FakeSeg("prev", 250.0, 259.0)
        segs = [prev, tail]
        assert _segments_in_section(segs, 210.0, 259.0, is_last_section=True) == ["prev", "b68aff50"]

    def test_extended_last_section_no_double_assignment(self):
        """最后一节扩窗后，前节 [200,210) 与末节 [210,259)+扩 仍按 start 唯一归属。"""
        s209 = self.FakeSeg("e", 209.0, 210.0)
        s210 = self.FakeSeg("f", 210.0, 220.0)
        s259 = self.FakeSeg("tail", 259.0, 260.0)
        segs = [s209, s210, s259]
        a1 = _segments_in_section(segs, 200.0, 210.0, is_last_section=False)
        a2 = _segments_in_section(segs, 210.0, 259.0, is_last_section=True)
        assert a1 == ["e"]
        assert a2 == ["f", "tail"]
        assert len(a1 + a2) == len(set(a1 + a2))


# ========== 3. resolve_shot_durations ==========

class TestResolveShotDurations:

    @pytest.mark.asyncio
    async def test_valid_shot_ids_return_durations(self):
        from app.services.agent.video.video_segments_service import resolve_shot_durations
        mock_shots = [MagicMock(uuid="s1", duration=5.0), MagicMock(uuid="s2", duration=3.0)]
        with patch("app.crud.video.video_story.get_detailed_shots_by_uuids",
                    new_callable=AsyncMock, return_value=mock_shots):
            result = await resolve_shot_durations(["s1", "s2"], 1)
            assert result == [5.0, 3.0]

    @pytest.mark.asyncio
    async def test_empty_shot_ids_return_none(self):
        from app.services.agent.video.video_segments_service import resolve_shot_durations
        assert await resolve_shot_durations([], 1) is None

    @pytest.mark.asyncio
    async def test_all_none_shot_ids_return_none(self):
        from app.services.agent.video.video_segments_service import resolve_shot_durations
        assert await resolve_shot_durations([None, None], 1) is None

    @pytest.mark.asyncio
    async def test_zero_duration_return_none(self):
        from app.services.agent.video.video_segments_service import resolve_shot_durations
        mock_shots = [MagicMock(uuid="s1", duration=5.0), MagicMock(uuid="s2", duration=0)]
        with patch("app.crud.video.video_story.get_detailed_shots_by_uuids",
                    new_callable=AsyncMock, return_value=mock_shots):
            assert await resolve_shot_durations(["s1", "s2"], 1) is None


# ========== 4. merge_segment_videos ==========

class TestMergeSegmentVideos:
    """merge_segment_videos 测试：Pipeline / Sync 共用入口，包括 per-shot 黑屏占位。"""

    @pytest.mark.asyncio
    async def test_all_success(self):
        """所有 shot 成功 → SUCCESS"""
        from app.services.agent.video.video_segments_service import merge_segment_videos
        from app.models.video_state import VideoSegmentStatus

        with patch("app.services.agent.video.video_segments_service.resolve_shot_durations",
                    new_callable=AsyncMock, return_value=[5.0, 3.0]), \
             patch("app.services.agent.video.video_segments_service.process_segment_by_request",
                    new_callable=AsyncMock, return_value="https://cdn/merged.mp4"):
            result = await merge_segment_videos(
                segment_number=1,
                all_video_urls=["url1", "url2"],
                target_duration=8.0,
                all_detailed_shot_ids=["s1", "s2"],
                is_lipsync=False,
            )
            assert result.status == VideoSegmentStatus.SUCCESS
            assert result.success is True
            assert result.merged_video_url == "https://cdn/merged.mp4"

    @pytest.mark.asyncio
    async def test_all_failed_with_placeholder(self):
        """全部失败 + duration > 0 → 整段黑屏 → SUCCESS"""
        from app.services.agent.video.video_segments_service import merge_segment_videos
        from app.models.video_state import VideoSegmentStatus

        with patch("app.services.agent.video.video_segments_service.resolve_shot_durations",
                    new_callable=AsyncMock, return_value=None), \
             patch("app.services.agent.video.video_segments_service._generate_black_placeholder_url",
                    new_callable=AsyncMock, return_value="https://cdn/black.mp4"):
            result = await merge_segment_videos(
                segment_number=1,
                all_video_urls=[None, None],
                target_duration=5.0,
                all_detailed_shot_ids=["s1", "s2"],
                is_lipsync=False,
            )
            assert result.status == VideoSegmentStatus.SUCCESS
            assert result.merged_video_url == "https://cdn/black.mp4"

    @pytest.mark.asyncio
    async def test_all_failed_placeholder_fails(self):
        """全部失败 + 黑屏也失败 → FAILED"""
        from app.services.agent.video.video_segments_service import merge_segment_videos
        from app.models.video_state import VideoSegmentStatus

        with patch("app.services.agent.video.video_segments_service.resolve_shot_durations",
                    new_callable=AsyncMock, return_value=None), \
             patch("app.services.agent.video.video_segments_service._generate_black_placeholder_url",
                    new_callable=AsyncMock, side_effect=RuntimeError("FFmpeg not found")):
            result = await merge_segment_videos(
                segment_number=1,
                all_video_urls=[None, None],
                target_duration=5.0,
                all_detailed_shot_ids=["s1", "s2"],
                is_lipsync=False,
            )
            assert result.status == VideoSegmentStatus.FAILED
            assert result.success is False

    @pytest.mark.asyncio
    async def test_all_failed_zero_duration(self):
        """全部失败 + duration=0 → FAILED（不生成占位）"""
        from app.services.agent.video.video_segments_service import merge_segment_videos
        from app.models.video_state import VideoSegmentStatus

        with patch("app.services.agent.video.video_segments_service.resolve_shot_durations",
                    new_callable=AsyncMock, return_value=None):
            result = await merge_segment_videos(
                segment_number=1,
                all_video_urls=[None],
                target_duration=0.0,
                all_detailed_shot_ids=[None],
                is_lipsync=False,
            )
            assert result.status == VideoSegmentStatus.FAILED

    @pytest.mark.asyncio
    async def test_partial_failure_with_per_shot_placeholder(self):
        """3 个 shot，中间失败 + 有 shot durations → per-shot 黑屏占位"""
        from app.services.agent.video.video_segments_service import merge_segment_videos
        from app.models.video_state import VideoSegmentStatus

        mock_process = AsyncMock(return_value="https://cdn/merged.mp4")
        mock_black = AsyncMock(return_value="https://cdn/black.mp4")

        with patch("app.services.agent.video.video_segments_service.resolve_shot_durations",
                    new_callable=AsyncMock, return_value=[5.0, 3.0, 4.0]), \
             patch("app.services.agent.video.video_segments_service._generate_black_placeholder_url",
                    mock_black), \
             patch("app.services.agent.video.video_segments_service.process_segment_by_request",
                    mock_process):
            result = await merge_segment_videos(
                segment_number=1,
                all_video_urls=["url1", None, "url3"],
                target_duration=12.0,
                all_detailed_shot_ids=["s1", "s2", "s3"],
                is_lipsync=True,
            )
            assert result.status == VideoSegmentStatus.PARTIAL_SUCCESS
            assert result.success is True
            assert result.merged_video_url == "https://cdn/merged.mp4"

            # 验证黑屏 URL 用于失败 shot 的位置
            mock_black.assert_called_once_with(1, 3.0)
            req = mock_process.call_args[0][0]
            assert req.video_urls == ["url1", "https://cdn/black.mp4", "url3"]
            assert req.shot_durations == [5.0, 3.0, 4.0]
            assert req.is_lipsync is True

    @pytest.mark.asyncio
    async def test_partial_failure_no_shot_durations_fallback(self):
        """部分失败 + 无 shot durations → 只用成功视频合并（兜底）"""
        from app.services.agent.video.video_segments_service import merge_segment_videos
        from app.models.video_state import VideoSegmentStatus

        mock_process = AsyncMock(return_value="https://cdn/merged.mp4")
        with patch("app.services.agent.video.video_segments_service.resolve_shot_durations",
                    new_callable=AsyncMock, return_value=None), \
             patch("app.services.agent.video.video_segments_service.process_segment_by_request",
                    mock_process):
            result = await merge_segment_videos(
                segment_number=1,
                all_video_urls=["url1", None, "url3"],
                target_duration=12.0,
                all_detailed_shot_ids=["s1", "s2", "s3"],
                is_lipsync=False,
            )
            assert result.status == VideoSegmentStatus.PARTIAL_SUCCESS
            assert result.success is True
            req = mock_process.call_args[0][0]
            assert req.video_urls == ["url1", "url3"]
            assert req.shot_durations is None

    @pytest.mark.asyncio
    async def test_merge_exception_returns_failed(self):
        """合并过程抛异常 → FAILED"""
        from app.services.agent.video.video_segments_service import merge_segment_videos
        from app.models.video_state import VideoSegmentStatus

        with patch("app.services.agent.video.video_segments_service.resolve_shot_durations",
                    new_callable=AsyncMock, return_value=[5.0]), \
             patch("app.services.agent.video.video_segments_service.process_segment_by_request",
                    new_callable=AsyncMock, side_effect=RuntimeError("FFmpeg crashed")):
            result = await merge_segment_videos(
                segment_number=1,
                all_video_urls=["url1"],
                target_duration=5.0,
                all_detailed_shot_ids=["s1"],
                is_lipsync=True,
            )
            assert result.status == VideoSegmentStatus.FAILED
            assert "FFmpeg crashed" in result.error_msg

    @pytest.mark.asyncio
    async def test_lipsync_mode_forwarded(self):
        """is_lipsync=True 正确传递"""
        from app.services.agent.video.video_segments_service import merge_segment_videos

        mock_process = AsyncMock(return_value="https://cdn/merged.mp4")
        with patch("app.services.agent.video.video_segments_service.resolve_shot_durations",
                    new_callable=AsyncMock, return_value=[5.0]), \
             patch("app.services.agent.video.video_segments_service.process_segment_by_request",
                    mock_process):
            await merge_segment_videos(
                segment_number=1,
                all_video_urls=["url1"],
                target_duration=5.0,
                all_detailed_shot_ids=["s1"],
                is_lipsync=True,
            )
            assert mock_process.call_args[0][0].is_lipsync is True

    @pytest.mark.asyncio
    async def test_empty_segment_no_shots(self):
        """0 个 shot → FAILED"""
        from app.services.agent.video.video_segments_service import merge_segment_videos
        from app.models.video_state import VideoSegmentStatus

        with patch("app.services.agent.video.video_segments_service.resolve_shot_durations",
                    new_callable=AsyncMock, return_value=None):
            result = await merge_segment_videos(
                segment_number=1,
                all_video_urls=[],
                target_duration=0.0,
                all_detailed_shot_ids=[],
                is_lipsync=False,
            )
            assert result.status == VideoSegmentStatus.FAILED


# ========== 5. _sync_prepare_merge_segment ==========

class TestSyncPrepareMergeSegment:

    @pytest.mark.asyncio
    async def test_s3_migration_and_merge(self):
        from app.services.agent.video_agent_service import _sync_prepare_merge_segment
        from app.models.video_state import VideoSegmentStatus
        from app.services.agent.video.video_segments_service import SegmentMergeResult

        mock_version = MagicMock()
        mock_version.uuid = "v1"
        mock_version.video_url = "https://external.com/video.mp4"
        mock_version.success = True
        mock_version.duration = 5.0
        mock_version.resolution = None
        mock_version.aspect_ratio = None

        mock_gen = MagicMock()
        mock_gen.detailed_shot_id = "shot1"
        mock_gen.generation_mode = "normal"

        segment_data = {"video_generations": [mock_gen]}

        fake_merge_result = SegmentMergeResult(
            merged_video_url="https://cdn/merged.mp4",
            status=VideoSegmentStatus.SUCCESS,
            success=True, error_msg=None,
            failed_video_count=0, total_video_count=1, shot_durations=[5.0],
        )

        with patch("app.services.agent.video_agent_service.s3_utils") as mock_s3, \
             patch("app.services.agent.video_agent_service.get_target_pixels_for_video",
                    return_value=(1920, 1080)), \
             patch("app.services.agent.video.video_segments_service.merge_segment_videos",
                    new_callable=AsyncMock, return_value=fake_merge_result) as mock_merge:
            mock_s3.ensure_video_on_our_s3 = AsyncMock(return_value="https://our-cdn/video.mp4")
            result, video_urls = await _sync_prepare_merge_segment(1, [mock_version], segment_data, 5.0)

            assert result.status == VideoSegmentStatus.SUCCESS
            assert len(video_urls) == 1
            call_kwargs = mock_merge.call_args[1]
            assert call_kwargs["all_video_urls"] == ["https://our-cdn/video.mp4"]
            assert call_kwargs["all_detailed_shot_ids"] == ["shot1"]

    @pytest.mark.asyncio
    async def test_failed_version_passes_none_url(self):
        """video_gen_version 无 video_url → all_video_urls 中对应位置为 None"""
        from app.services.agent.video_agent_service import _sync_prepare_merge_segment
        from app.models.video_state import VideoSegmentStatus
        from app.services.agent.video.video_segments_service import SegmentMergeResult

        v_ok = MagicMock(uuid="v1", video_url="https://ext/ok.mp4", success=True, duration=5.0, resolution=None, aspect_ratio=None)
        v_fail = MagicMock(uuid="v2", video_url=None, success=False, duration=None, resolution=None, aspect_ratio=None)
        v_ok2 = MagicMock(uuid="v3", video_url="https://ext/ok2.mp4", success=True, duration=4.0, resolution=None, aspect_ratio=None)

        gen1 = MagicMock(detailed_shot_id="s1", generation_mode="normal")
        gen2 = MagicMock(detailed_shot_id="s2", generation_mode="normal")
        gen3 = MagicMock(detailed_shot_id="s3", generation_mode="normal")
        segment_data = {"video_generations": [gen1, gen2, gen3]}

        fake_result = SegmentMergeResult(
            merged_video_url="https://cdn/merged.mp4",
            status=VideoSegmentStatus.PARTIAL_SUCCESS,
            success=True, error_msg="1/3 shot 失败",
            failed_video_count=1, total_video_count=3, shot_durations=[5.0, 3.0, 4.0],
        )

        with patch("app.services.agent.video_agent_service.s3_utils") as mock_s3, \
             patch("app.services.agent.video_agent_service.get_target_pixels_for_video",
                    return_value=(1920, 1080)), \
             patch("app.services.agent.video.video_segments_service.merge_segment_videos",
                    new_callable=AsyncMock, return_value=fake_result) as mock_merge:
            mock_s3.ensure_video_on_our_s3 = AsyncMock(side_effect=lambda video_url, **kw: f"https://our-cdn/{video_url.split('/')[-1]}")
            result, video_urls = await _sync_prepare_merge_segment(1, [v_ok, v_fail, v_ok2], segment_data, 12.0)

            call_kwargs = mock_merge.call_args[1]
            assert call_kwargs["all_video_urls"] == [
                "https://our-cdn/ok.mp4",
                None,
                "https://our-cdn/ok2.mp4",
            ]
            assert call_kwargs["all_detailed_shot_ids"] == ["s1", "s2", "s3"]

    @pytest.mark.asyncio
    async def test_is_lipsync_from_video_generation_versions(self):
        """generation_mode 在 version 行上，不在 video_generations 父行。"""
        from app.services.agent.video_agent_service import _sync_prepare_merge_segment
        from app.models.video_state import VideoSegmentStatus
        from app.services.agent.video.video_segments_service import SegmentMergeResult
        from app.models.tool_enums import GenerationMode

        mock_version = MagicMock()
        mock_version.uuid = "v1"
        mock_version.video_url = "https://external.com/lip.mp4"
        mock_version.success = True
        mock_version.duration = 4.0
        mock_version.resolution = None
        mock_version.aspect_ratio = None
        mock_version.generation_mode = GenerationMode.LIPSYNC.value

        mock_gen = MagicMock()
        mock_gen.detailed_shot_id = "shot1"
        mock_gen.generation_mode = None

        segment_data = {"video_generations": [mock_gen]}

        fake_merge_result = SegmentMergeResult(
            merged_video_url="https://cdn/merged.mp4",
            status=VideoSegmentStatus.SUCCESS,
            success=True, error_msg=None,
            failed_video_count=0, total_video_count=1, shot_durations=[4.0],
        )

        with patch("app.services.agent.video_agent_service.s3_utils") as mock_s3, \
             patch("app.services.agent.video_agent_service.get_target_pixels_for_video",
                    return_value=(1920, 1080)), \
             patch("app.services.agent.video.video_segments_service.merge_segment_videos",
                    new_callable=AsyncMock, return_value=fake_merge_result) as mock_merge:
            mock_s3.ensure_video_on_our_s3 = AsyncMock(return_value="https://our-cdn/lip.mp4")
            await _sync_prepare_merge_segment(7, [mock_version], segment_data, 3.67)

            assert mock_merge.call_args[1]["is_lipsync"] is True
