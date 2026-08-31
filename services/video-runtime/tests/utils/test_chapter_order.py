"""章节 order 统一约定：存库 0-based，展示 order+1。"""
import pytest

from app.models.video_state import (
    StoryChapterForLLMMode,
    StoryOutlineForLLMMode,
    StoryStructureForLLMMode,
    AudioSegment,
)
from app.services.agent.video.outline_generation_service import (
    _convert_llm_mode_outline_to_outline,
)
from app.utils.chapter_order import (
    chapter_display_number,
    reindex_chapter_orders_inplace,
    sort_chapters_by_order,
)


def _make_llm_chapter(ch_id: str, order: int, title: str) -> StoryChapterForLLMMode:
    return StoryChapterForLLMMode(
        id=ch_id,
        title=title,
        description=f"desc-{title}",
        order=order,
    )


class TestChapterOrderUtils:
    def test_reindex_from_one_based(self):
        chapters = [
            {"order": 1, "title": "A"},
            {"order": 2, "title": "B"},
        ]
        reindex_chapter_orders_inplace(chapters)
        assert [c["order"] for c in chapters] == [0, 1]

    def test_reindex_sorts_before_assign(self):
        chapters = [
            {"order": 2, "title": "C"},
            {"order": 0, "title": "A"},
            {"order": 1, "title": "B"},
        ]
        reindex_chapter_orders_inplace(chapters)
        assert [c["title"] for c in chapters] == ["A", "B", "C"]
        assert [c["order"] for c in chapters] == [0, 1, 2]

    def test_sort_chapters_by_order(self):
        class Ch:
            def __init__(self, order, title):
                self.order = order
                self.title = title

        ordered = sort_chapters_by_order([Ch(2, "c"), Ch(0, "a"), Ch(1, "b")])
        assert [c.title for c in ordered] == ["a", "b", "c"]

    def test_display_number(self):
        assert chapter_display_number(0) == 1
        assert chapter_display_number(1) == 2
        assert chapter_display_number(None, 0) == 1


class TestOutlineConvertOrderNormalization:
    def test_video_driven_llm_one_based_becomes_zero_based(self):
        llm_outline = StoryOutlineForLLMMode(
            title="Test",
            theme="",
            structure=StoryStructureForLLMMode(
                chapters=[_make_llm_chapter("c0", 1, "视界启程")]
            ),
            key_message="",
            total_duration=30,
            style_guide="",
            description="",
        )
        outline = _convert_llm_mode_outline_to_outline(
            llm_outline,
            sections=None,
            segments=None,
            target_duration=30,
        )
        assert len(outline.structure.chapters) == 1
        assert outline.structure.chapters[0].order == 0

    def test_video_driven_multi_chapter_one_based(self):
        llm_outline = StoryOutlineForLLMMode(
            title="Test",
            theme="",
            structure=StoryStructureForLLMMode(
                chapters=[
                    _make_llm_chapter("c0", 1, "第一章"),
                    _make_llm_chapter("c1", 2, "第二章"),
                    _make_llm_chapter("c2", 3, "第三章"),
                ]
            ),
            key_message="",
            total_duration=30,
            style_guide="",
            description="",
        )
        outline = _convert_llm_mode_outline_to_outline(
            llm_outline,
            sections=None,
            segments=None,
            target_duration=30,
        )
        assert [c.order for c in outline.structure.chapters] == [0, 1, 2]

    def test_audio_sections_same_count_one_based_llm(self):
        def _section(start, end, stype, uid):
            o = type("Section", (), {})()
            o.start_time = start
            o.end_time = end
            o.section_type = stype
            o.uuid = uid
            o.suggested_context = stype
            o.section_emotion = "neutral"
            return o

        sections = [
            _section(0, 10, "Intro", "s0"),
            _section(10, 20, "Chorus", "s1"),
        ]
        segments = [
            AudioSegment(
                id=0, start=0, end=10, duration=10, text="a",
                emotion="neutral", tempo="medium", uuid="seg0",
            ),
            AudioSegment(
                id=1, start=10, end=20, duration=10, text="b",
                emotion="neutral", tempo="medium", uuid="seg1",
            ),
        ]
        llm_outline = StoryOutlineForLLMMode(
            title="Test",
            theme="",
            structure=StoryStructureForLLMMode(
                chapters=[
                    _make_llm_chapter("c0", 1, "Intro章"),
                    _make_llm_chapter("c1", 2, "Chorus章"),
                ]
            ),
            key_message="",
            total_duration=20,
            style_guide="",
            description="",
        )
        outline = _convert_llm_mode_outline_to_outline(
            llm_outline,
            sections=sections,
            segments=segments,
        )
        assert [c.order for c in outline.structure.chapters] == [0, 1]
