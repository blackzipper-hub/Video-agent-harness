"""章节/场景时长程序兜底：复现并锁定 30s 短片不再出现 30/30/-30。"""
from typing import List

from app.agent_config.duration import allocate_durations_summing_to_target
from app.models.video_state import (
    StoryOutline,
    StoryStructure,
    StoryChapter,
    StoryboardScene,
)
from app.services.agent.video.outline_generation_service import _normalize_outline_duration
from app.services.agent.video.scene_generation_service import (
    _normalize_video_driven_scene_durations,
)


def _chapters(n: int) -> List[StoryChapter]:
    return [
        StoryChapter(
            id=f"c{i}",
            title=f"章{i}",
            description=f"d{i}",
            duration=0.0,
            order=i,
        )
        for i in range(n)
    ]


def _outline(n: int) -> StoryOutline:
    return StoryOutline(
        title="t",
        theme="",
        structure=StoryStructure(chapters=_chapters(n)),
        key_message="",
        total_duration=0.0,
        style_guide="",
        description="",
    )


class TestAllocateDurations:
    def test_30s_3_chapters_unit5_no_negative(self):
        durs = allocate_durations_summing_to_target(30, 3, 5)
        assert durs == [10.0, 10.0, 10.0]
        assert sum(durs) == 30
        assert all(d > 0 for d in durs)

    def test_30s_2_chapters_unit5(self):
        durs = allocate_durations_summing_to_target(30, 2, 5)
        assert durs == [15.0, 15.0]
        assert sum(durs) == 30

    def test_never_negative_even_with_many_parts(self):
        durs = allocate_durations_summing_to_target(30, 10, 5)
        assert len(durs) == 10
        assert abs(sum(durs) - 30) < 1e-9
        assert all(d >= 0 for d in durs)


class TestNormalizeOutlineDuration:
    def test_reproduces_old_bug_case_now_fixed(self, monkeypatch):
        """旧逻辑：MIN=30 + 3章 + target=30 → 30/30/-30；纠正后全为正且 sum=30。"""
        monkeypatch.setattr(
            "app.agent_config.duration.get_video_driven_duration_values",
            lambda _uo=None: [5, 8, 10],
        )
        outline = _outline(3)
        out = _normalize_outline_duration(outline, 30.0, user_option=None)
        durs = [c.duration for c in out.structure.chapters]
        assert abs(sum(durs) - 30.0) < 1e-6
        assert out.total_duration == 30.0
        assert all(d > 0 for d in durs)
        assert min(durs) >= 8.0  # preferred unit 8

    def test_single_chapter_gets_full_target(self, monkeypatch):
        monkeypatch.setattr(
            "app.agent_config.duration.get_video_driven_duration_values",
            lambda _uo=None: [4, 5, 6, 7, 8],
        )
        out = _normalize_outline_duration(_outline(1), 30.0, user_option=None)
        assert out.structure.chapters[0].duration == 30.0


class TestNormalizeVideoDrivenScenes:
    def _scenes(self, n: int, dur: float = 5.0) -> List[StoryboardScene]:
        return [
            StoryboardScene(
                scene_number=i + 1,
                title=f"s{i}",
                description="d",
                duration=dur,
                camera_angle="中景",
                character_action="a",
                visual_style="v",
                transition_style="切",
                character_ids=[],
            )
            for i in range(n)
        ]

    def test_sum_equals_chapter_and_truncate(self):
        scenes = self._scenes(6, 5.0)
        out = _normalize_video_driven_scene_durations(scenes, 10.0, [5, 8, 10])
        # min_slot=5 → max_scenes=2 for 10s chapter
        assert len(out) == 2
        assert abs(sum(s.duration for s in out) - 10.0) < 1e-9
        assert all(s.duration > 0 for s in out)

    def test_short_drama_keeps_llm_two_scenes_in_8s_chapter(self):
        """LLM 按 cue 拆出 2 场@4s，程序不得用 count_unit=5 压成 1 场。"""
        scenes = [
            StoryboardScene(
                scene_number=1, title="端杯", description="女主端起毒酒杯", duration=4.0,
                camera_angle="中景", character_action="端杯递给对方", visual_style="v",
                transition_style="切", character_ids=[],
            ),
            StoryboardScene(
                scene_number=2, title="对白", description="女主说这杯有毒", duration=4.0,
                camera_angle="特写", character_action="举杯说话", visual_style="v",
                transition_style="切", character_ids=[],
            ),
        ]
        out = _normalize_video_driven_scene_durations(
            scenes, 8.0, [4, 5, 8, 10], content_category="Short Drama"
        )
        assert len(out) == 2
        assert abs(sum(s.duration for s in out) - 8.0) < 1e-6


    def test_negative_chapter_skipped(self):
        scenes = self._scenes(3, 3.0)
        out = _normalize_video_driven_scene_durations(scenes, -30.0, [3, 5])
        assert [s.duration for s in out] == [3.0, 3.0, 3.0]


class TestCorrectOutlineKeepsLlm:
    def test_keep_uneven_llm_durations(self, monkeypatch):
        from app.services.agent.video.outline_generation_service import (
            _correct_outline_duration_if_needed,
        )
        monkeypatch.setattr(
            "app.agent_config.duration.get_video_driven_duration_values",
            lambda _uo=None: [4, 5, 8, 10],
        )
        outline = _outline(3)
        outline.structure.chapters[0].duration = 8.0
        outline.structure.chapters[1].duration = 12.0
        outline.structure.chapters[2].duration = 10.0
        out = _correct_outline_duration_if_needed(outline, 30.0, user_option=None)
        assert [c.duration for c in out.structure.chapters] == [8.0, 12.0, 10.0]

    def test_correct_negative_and_bad_sum(self, monkeypatch):
        from app.services.agent.video.outline_generation_service import (
            _correct_outline_duration_if_needed,
        )
        monkeypatch.setattr(
            "app.agent_config.duration.get_video_driven_duration_values",
            lambda _uo=None: [5, 8, 10],
        )
        outline = _outline(3)
        outline.structure.chapters[0].duration = 30.0
        outline.structure.chapters[1].duration = 30.0
        outline.structure.chapters[2].duration = -30.0
        out = _correct_outline_duration_if_needed(outline, 30.0, user_option=None)
        durs = [c.duration for c in out.structure.chapters]
        assert all(d > 0 for d in durs)
        assert abs(sum(durs) - 30.0) < 1e-6


class TestSceneKeepLlmUneven:
    def test_keep_llm_when_sum_matches(self):
        scenes = [
            StoryboardScene(
                scene_number=1, title="a", description="d", duration=8.0,
                camera_angle="m", character_action="a", visual_style="v",
                transition_style="c", character_ids=[],
            ),
            StoryboardScene(
                scene_number=2, title="b", description="d", duration=12.0,
                camera_angle="m", character_action="a", visual_style="v",
                transition_style="c", character_ids=[],
            ),
        ]
        out = _normalize_video_driven_scene_durations(scenes, 20.0, [4, 5, 8, 10, 12])
        assert [s.duration for s in out] == [8.0, 12.0]

    def test_reject_sub_api_min_even_when_sum_matches(self):
        """sum=30 但含 2/2.5/3 → 不得原样保留（生成端会 ceil 到 4 撑爆成片）。"""
        specs = [2.0, 3.0, 3.0, 5.0, 5.0, 4.0, 3.0, 2.5, 2.5]
        scenes = [
            StoryboardScene(
                scene_number=i + 1, title=f"s{i}", description="d", duration=d,
                camera_angle="m", character_action="a", visual_style="v",
                transition_style="c", character_ids=[],
            )
            for i, d in enumerate(specs)
        ]
        assert abs(sum(specs) - 30.0) < 1e-6
        out = _normalize_video_driven_scene_durations(
            scenes, 30.0, [4, 5, 8, 10, 12], content_category="Short Drama"
        )
        assert abs(sum(s.duration for s in out) - 30.0) < 0.15
        assert all(s.duration >= 4.0 - 1e-6 for s in out)
        # 9 场无法在 30s@api_min=4 下放下 → 截到 ≤7
        assert len(out) <= 7


class TestShortDramaSceneDensity:
    def test_min_scenes_helper_for_prompt_guidance(self):
        from app.agent_config.duration import short_drama_min_scenes
        # OM-ish: fewer denser scenes (unit≈8), not 3–4s chop
        assert short_drama_min_scenes(8) == 1
        assert short_drama_min_scenes(10) == 1
        assert short_drama_min_scenes(12) == 2
        assert short_drama_min_scenes(30) == 4

    def test_short_drama_does_not_invent_scenes_for_12s_single(self):
        """Short Drama：LLM 返回 1 场 12s 时程序不 densify，只做时长 normalize。"""
        scenes = [
            StoryboardScene(
                scene_number=1,
                title="宴会危机",
                description="女主站立端毒酒杯说话",
                duration=12.0,
                camera_angle="全景推中景",
                character_action="笔挺站立端杯",
                visual_style="宫廷",
                transition_style="切",
                character_ids=["c1"],
            )
        ]
        out = _normalize_video_driven_scene_durations(
            scenes, 12.0, [4, 5, 8, 10, 12], content_category="Short Drama"
        )
        assert len(out) == 1
        assert abs(out[0].duration - 12.0) < 1e-6
        assert "节拍" not in (out[0].description or "")

    def test_non_short_drama_keeps_single_long_scene(self):
        scenes = [
            StoryboardScene(
                scene_number=1, title="a", description="d", duration=12.0,
                camera_angle="m", character_action="a", visual_style="v",
                transition_style="c", character_ids=[],
            )
        ]
        out = _normalize_video_driven_scene_durations(scenes, 12.0, [4, 5, 8, 10, 12])
        assert len(out) == 1
        assert out[0].duration == 12.0
