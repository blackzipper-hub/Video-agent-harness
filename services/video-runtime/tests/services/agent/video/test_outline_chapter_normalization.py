"""
单元测试：有 sections 时 _convert_llm_mode_outline_to_outline 的章节数与段落 1:1 后置处理。
- LLM 返回章节数 > 段落数：截断为段落数，前 n 章均有 duration/audio_segment_ids。
- LLM 返回章节数 < 段落数：按段落补足，补的章有 title/description/duration/audio_section_uuid/audio_segment_ids。
- LLM 返回章节数 = 段落数：行为不变。
- 无 sections：不进行 1:1 规范化；漏标片段下标时挂到第一章（convert 兜底）。
"""
import pytest
from typing import List, Any

from app.models.video_state import (
    StoryOutlineForLLMMode,
    StoryStructureForLLMMode,
    StoryChapterForLLMMode,
    AudioSegment,
)
from app.services.agent.video.outline_generation_service import (
    _convert_llm_mode_outline_to_outline,
)


def _make_section(start_time: float, end_time: float, section_type: str, uuid_str: str = None):
    import uuid
    o = type("Section", (), {})()
    o.start_time = start_time
    o.end_time = end_time
    o.section_type = section_type
    o.uuid = uuid_str or str(uuid.uuid4())
    o.suggested_context = f"context_{section_type}"
    o.section_emotion = "neutral"
    return o


def _make_segment(seg_id: int, start: float, end: float, uuid_str: str = None):
    import uuid
    return AudioSegment(
        id=seg_id,
        start=start,
        end=end,
        duration=round(end - start, 3),
        text=f"seg{seg_id}",
        emotion="neutral",
        tempo="medium",
        uuid=uuid_str or str(uuid.uuid4()),
    )


def _make_llm_chapter(ch_id: str, order: int, title: str, description: str):
    return StoryChapterForLLMMode(
        id=ch_id,
        title=title,
        description=description,
        order=order,
        audio_segment_indices=None,
    )


def _make_llm_chapter_with_indices(
    ch_id: str, order: int, title: str, description: str, indices: List[int]
):
    return StoryChapterForLLMMode(
        id=ch_id,
        title=title,
        description=description,
        order=order,
        audio_segment_indices=indices,
    )


@pytest.fixture
def four_sections():
    return [
        _make_section(0.0, 2.0, "Intro", "sec-0"),
        _make_section(2.0, 8.0, "Verse 1", "sec-1"),
        _make_section(8.0, 20.0, "Chorus 1", "sec-2"),
        _make_section(20.0, 25.0, "Outro", "sec-3"),
    ]


@pytest.fixture
def segments_for_sections():
    # 覆盖 0-25s，每个 section 内至少一个 segment
    return [
        _make_segment(0, 0.0, 1.0, "seg-0"),
        _make_segment(1, 1.0, 2.0, "seg-1"),
        _make_segment(2, 2.0, 5.0, "seg-2"),
        _make_segment(3, 5.0, 8.0, "seg-3"),
        _make_segment(4, 8.0, 14.0, "seg-4"),
        _make_segment(5, 14.0, 20.0, "seg-5"),
        _make_segment(6, 20.0, 25.0, "seg-6"),
    ]


class TestOutlineChapterNormalization:
    """有 sections 时章节数与段落 1:1 的截断/补足。"""

    def test_llm_returns_more_chapters_than_sections_truncate(
        self, four_sections: List[Any], segments_for_sections: List[AudioSegment]
    ):
        """LLM 返回 6 章，只有 4 个 section → 截断为 4 章，每章均有 duration > 0。"""
        chapters_llm = [
            _make_llm_chapter("c0", 0, "开场", "desc0"),
            _make_llm_chapter("c1", 1, "发展", "desc1"),
            _make_llm_chapter("c2", 2, "高潮", "desc2"),
            _make_llm_chapter("c3", 3, "结尾", "desc3"),
            _make_llm_chapter("c4", 4, "多余章1", "desc4"),
            _make_llm_chapter("c5", 5, "多余章2", "desc5"),
        ]
        llm_outline = StoryOutlineForLLMMode(
            title="Test",
            theme="",
            structure=StoryStructureForLLMMode(chapters=chapters_llm),
            key_message="",
            total_duration=25,
            style_guide="",
            description="",
        )
        outline = _convert_llm_mode_outline_to_outline(
            llm_outline,
            sections=four_sections,
            segments=segments_for_sections,
        )
        chapters = outline.structure.chapters
        assert len(chapters) == 4, "应截断为 4 章"
        for i, c in enumerate(chapters):
            assert c.order == i
            assert c.duration > 0, f"第 {i} 章应有 duration"
            assert c.audio_section_uuid is not None
            assert c.audio_segment_ids is not None and len(c.audio_segment_ids) >= 0
        assert outline.total_duration > 0

    def test_llm_returns_fewer_chapters_than_sections_pad(
        self, four_sections: List[Any], segments_for_sections: List[AudioSegment]
    ):
        """LLM 返回 2 章，有 4 个 section → 补足至 4 章，补的章有 title/description/duration。"""
        chapters_llm = [
            _make_llm_chapter("c0", 0, "第一章", "desc0"),
            _make_llm_chapter("c1", 1, "第二章", "desc1"),
        ]
        llm_outline = StoryOutlineForLLMMode(
            title="Test",
            theme="",
            structure=StoryStructureForLLMMode(chapters=chapters_llm),
            key_message="",
            total_duration=25,
            style_guide="",
            description="",
        )
        outline = _convert_llm_mode_outline_to_outline(
            llm_outline,
            sections=four_sections,
            segments=segments_for_sections,
        )
        chapters = outline.structure.chapters
        assert len(chapters) == 4, "应补足至 4 章"
        for i, c in enumerate(chapters):
            assert c.order == i
            assert c.duration >= 0
            if i < 2:
                assert c.title in ("第一章", "第二章")
            else:
                assert c.title in ("Chorus 1", "Outro"), "补足章 title 来自 section_type"
        assert outline.total_duration > 0

    def test_llm_returns_same_number_unchanged(
        self, four_sections: List[Any], segments_for_sections: List[AudioSegment]
    ):
        """LLM 返回 4 章，4 个 section → 行为不变，每章对应段落。"""
        chapters_llm = [
            _make_llm_chapter("c0", 0, "Intro", "d0"),
            _make_llm_chapter("c1", 1, "Verse", "d1"),
            _make_llm_chapter("c2", 2, "Chorus", "d2"),
            _make_llm_chapter("c3", 3, "Outro", "d3"),
        ]
        llm_outline = StoryOutlineForLLMMode(
            title="Test",
            theme="",
            structure=StoryStructureForLLMMode(chapters=chapters_llm),
            key_message="",
            total_duration=25,
            style_guide="",
            description="",
        )
        outline = _convert_llm_mode_outline_to_outline(
            llm_outline,
            sections=four_sections,
            segments=segments_for_sections,
        )
        chapters = outline.structure.chapters
        assert len(chapters) == 4
        assert [c.title for c in chapters] == ["Intro", "Verse", "Chorus", "Outro"]
        assert [c.order for c in chapters] == [0, 1, 2, 3]
        for c in chapters:
            assert c.duration > 0 and c.audio_section_uuid is not None

    def test_no_sections_no_normalization(self, segments_for_sections: List[AudioSegment]):
        """无 sections 时不进行 1:1 规范化，章节数保持 LLM 返回。"""
        chapters_llm = [
            _make_llm_chapter("c0", 0, "A", "d0"),
            _make_llm_chapter("c1", 1, "B", "d1"),
        ]
        llm_outline = StoryOutlineForLLMMode(
            title="Test",
            theme="",
            structure=StoryStructureForLLMMode(chapters=chapters_llm),
            key_message="",
            total_duration=25,
            style_guide="",
            description="",
        )
        outline = _convert_llm_mode_outline_to_outline(
            llm_outline,
            sections=None,
            segments=segments_for_sections,
        )
        chapters = outline.structure.chapters
        assert len(chapters) == 2
        assert [c.title for c in chapters] == ["A", "B"]
        assert [c.order for c in chapters] == [0, 1]

    def test_no_sections_unassigned_segment_indices_appended_to_first_chapter(self):
        """无 sections：LLM 漏标某片段下标 → 程序挂到第一章（与 convert 兜底一致）。"""
        segs = [
            _make_segment(0, 0.0, 1.0, "s0"),
            _make_segment(1, 1.0, 2.0, "s1"),
            _make_segment(2, 2.0, 3.0, "s2"),
        ]
        u0, u1, u2 = segs[0].uuid, segs[1].uuid, segs[2].uuid
        chapters_llm = [
            _make_llm_chapter_with_indices("c0", 0, "A", "d0", [0]),
            _make_llm_chapter_with_indices("c1", 1, "B", "d1", [2]),
        ]
        llm_outline = StoryOutlineForLLMMode(
            title="Test",
            theme="",
            structure=StoryStructureForLLMMode(chapters=chapters_llm),
            key_message="",
            total_duration=3,
            style_guide="",
            description="",
        )
        outline = _convert_llm_mode_outline_to_outline(
            llm_outline,
            sections=None,
            segments=segs,
        )
        ch0, ch1 = outline.structure.chapters
        assert ch0.audio_segment_ids == [u0, u1]
        assert ch1.audio_segment_ids == [u2]

    def test_sections_but_no_segments_no_pad_truncate(self, four_sections: List[Any]):
        """有 sections 但 segments 为空时，不因 segments 报错；截断/补足仍按 section 数。"""
        chapters_llm = [
            _make_llm_chapter("c0", 0, "One", "d0"),
            _make_llm_chapter("c1", 1, "Two", "d1"),
            _make_llm_chapter("c2", 2, "Three", "d2"),
            _make_llm_chapter("c3", 3, "Four", "d3"),
            _make_llm_chapter("c4", 4, "Five", "d4"),
        ]
        llm_outline = StoryOutlineForLLMMode(
            title="Test",
            theme="",
            structure=StoryStructureForLLMMode(chapters=chapters_llm),
            key_message="",
            total_duration=25,
            style_guide="",
            description="",
        )
        outline = _convert_llm_mode_outline_to_outline(
            llm_outline,
            sections=four_sections,
            segments=[],
        )
        chapters = outline.structure.chapters
        assert len(chapters) == 4
        for i, c in enumerate(chapters):
            assert c.order == i
            assert c.duration >= 0
            assert c.audio_segment_ids is None or len(c.audio_segment_ids) == 0
