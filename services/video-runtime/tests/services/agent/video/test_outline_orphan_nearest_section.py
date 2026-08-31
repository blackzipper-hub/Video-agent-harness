"""
大纲：有 sections 时 orphan 音频切片按「距曲式闭区间最近」归章；与 _segments_in_section 末节扩窗一致。
覆盖：中间缝 orphan、尾 orphan（与 is_last 扩窗配合）、tie-break、convert 全链路。
"""
import pytest
import uuid

from app.models.video_state import (
    AudioSegment,
    AudioTranscription,
    StoryOutline,
    StoryStructure,
    StoryChapter,
    StoryOutlineForLLMMode,
    StoryStructureForLLMMode,
    StoryChapterForLLMMode,
)
from app.services.agent.video.outline_generation_service import (
    _assign_orphan_audio_segments_to_nearest_chapters,
    _convert_llm_mode_outline_to_outline,
    _process_audio_driven_outline,
)


def _sec(st: float, et: float, section_type: str, u: str = None):
    o = type("Section", (), {})()
    o.start_time = st
    o.end_time = et
    o.section_type = section_type
    o.uuid = u or str(uuid.uuid4())
    o.suggested_context = "ctx"
    o.section_emotion = "n"
    return o


def _seg(i: int, start: float, end: float, u: str) -> AudioSegment:
    return AudioSegment(
        id=i,
        start=start,
        end=end,
        text="t",
        duration=round(end - start, 3),
        uuid=u,
    )


class TestAssignOrphanNearest:
    def test_gap_segment_mid_timeline_goes_to_nearest_section_tie_smaller_index(self):
        """[0,10) 与 [20,30) 之间 start=15 的片：到两窗闭区间距离均为 5 → 归 order=0。"""
        sections = [_sec(0.0, 10.0, "A"), _sec(20.0, 30.0, "B")]
        segments = [
            _seg(0, 0.0, 5.0, "u0"),
            _seg(1, 5.0, 10.0, "u1"),
            _seg(2, 15.0, 16.0, "orphan"),
            _seg(3, 20.0, 30.0, "u3"),
        ]
        chapters = [
            StoryChapter(id="c0", title="A", description="", duration=10.0, order=0, audio_segment_ids=["u0", "u1"]),
            StoryChapter(id="c1", title="B", description="", duration=10.0, order=1, audio_segment_ids=["u3"]),
        ]
        _assign_orphan_audio_segments_to_nearest_chapters(chapters, segments, sections)
        assert "orphan" in chapters[0].audio_segment_ids
        assert "orphan" not in (chapters[1].audio_segment_ids or [])
        assert chapters[0].duration == 10.0 + 1.0

    def test_point_just_inside_second_section(self):
        sections = [_sec(0.0, 10.0, "A"), _sec(10.0, 40.0, "B")]
        segments = [
            _seg(0, 0.0, 10.0, "a"),
            _seg(1, 11.0, 20.0, "mid"),
        ]
        chapters = [
            StoryChapter(id="c0", title="A", description="", duration=10.0, order=0, audio_segment_ids=["a"]),
            StoryChapter(id="c1", title="B", description="", duration=30.0, order=1, audio_segment_ids=[]),
        ]
        chapters[1].audio_segment_ids = None
        _assign_orphan_audio_segments_to_nearest_chapters(chapters, segments, sections)
        assert chapters[1].audio_segment_ids == ["mid"]

    def test_tail_orphan_with_short_last_section_end_extended_by_timeline(self):
        """末节曲式 [210,259) + 切片到 260：闭区间距离用扩窗后的 et。"""
        tail = _seg(2, 259.5, 260.0, "tail")
        prev = _seg(1, 250.0, 259.5, "prev")
        s0 = _seg(0, 0.0, 10.0, "s0")
        sections = [_sec(0.0, 10.0, "Intro"), _sec(210.0, 259.0, "Out")]
        segments = [s0, prev, tail]
        chapters = [
            StoryChapter(id="c0", title="Intro", description="", duration=10.0, order=0, audio_segment_ids=["s0"]),
            StoryChapter(
                id="c1",
                title="Out",
                description="",
                duration=49.0,
                order=1,
                audio_segment_ids=["prev"],
            ),
        ]
        _assign_orphan_audio_segments_to_nearest_chapters(chapters, segments, sections)
        assert "tail" in chapters[1].audio_segment_ids
        assert abs(chapters[1].duration - (49.0 + 0.5)) < 0.01

    def test_skips_when_chapter_section_count_mismatch(self):
        sections = [_sec(0.0, 10.0, "A")]
        segments = [_seg(0, 5.0, 10.0, "x")]
        chapters = [
            StoryChapter(id="c0", title="A", description="", duration=10.0, order=0, audio_segment_ids=[]),
            StoryChapter(id="c1", title="Extra", description="", duration=1.0, order=1, audio_segment_ids=[]),
        ]
        _assign_orphan_audio_segments_to_nearest_chapters(chapters, segments, sections)
        assert chapters[0].audio_segment_ids == []


class TestConvertWithOrphanIntegration:
    def test_convert_middle_gap_orphan_assigned(self):
        llm_outline = StoryOutlineForLLMMode(
            title="T",
            theme="",
            structure=StoryStructureForLLMMode(
                chapters=[
                    StoryChapterForLLMMode(id="c0", title="A", description="d", order=0),
                    StoryChapterForLLMMode(id="c1", title="B", description="d", order=1),
                ]
            ),
            key_message="",
            total_duration=30,
            style_guide="",
            description="",
        )
        sections = [_sec(0.0, 10.0, "A"), _sec(20.0, 30.0, "B")]
        segments = [
            _seg(0, 0.0, 10.0, "u0"),
            _seg(1, 15.0, 16.0, "gap"),
            _seg(2, 20.0, 30.0, "u2"),
        ]
        outline = _convert_llm_mode_outline_to_outline(
            llm_outline, sections=sections, segments=segments
        )
        ch0, ch1 = outline.structure.chapters
        all_u = (ch0.audio_segment_ids or []) + (ch1.audio_segment_ids or [])
        assert set(all_u) == {"u0", "gap", "u2"}
        assert "gap" in ch0.audio_segment_ids


@pytest.mark.asyncio
async def test_process_audio_driven_outline_orphan():
    sections = [_sec(0.0, 10.0, "A"), _sec(20.0, 30.0, "B")]
    segments = [
        _seg(0, 0.0, 10.0, "u0"),
        _seg(1, 15.0, 16.0, "gap"),
        _seg(2, 20.0, 30.0, "u2"),
    ]
    tx = AudioTranscription(
        task="t",
        language="zh",
        duration=30.0,
        text="",
        segments=segments,
        audio_url="https://x",
    )
    story = StoryOutline(
        title="T",
        theme="",
        structure=StoryStructure(
            chapters=[
                StoryChapter(id="c0", title="A", description="", duration=10.0, order=0, audio_segment_ids=None),
                StoryChapter(id="c1", title="B", description="", duration=10.0, order=1, audio_segment_ids=None),
            ]
        ),
        key_message="",
        total_duration=30.0,
        style_guide="",
        description="",
    )
    out = await _process_audio_driven_outline(story, tx, sections=sections)
    all_u = []
    for c in out.structure.chapters:
        all_u.extend(c.audio_segment_ids or [])
    assert set(all_u) == {"u0", "gap", "u2"}
