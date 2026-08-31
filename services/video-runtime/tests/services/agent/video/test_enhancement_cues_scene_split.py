"""大纲 enhancement_cues → 场景语义拆镜（1 章 → N 镜）。

同时回归：无 cue 时仍走 duration split_threshold（SD2=8）。
"""

from types import SimpleNamespace

from app.models.user_options import UserOption, VideoGenerationTool
from app.models.video_state import EnhancementCue, StoryChapter
from app.services.agent.video.scene_generation_service import (
    _compute_scene_structure_for_chapter,
    _target_scene_count_for_chapter,
    _visual_enhancement_cues,
)


def _tx_one_segment(duration: float, uuid: str = "u0"):
    seg = SimpleNamespace(
        id=0, start=0.0, end=duration, text="x", duration=duration, uuid=uuid,
    )
    return SimpleNamespace(
        task="transcribe",
        language="zh",
        duration=duration,
        text="x",
        segments=[seg],
        audio_url="https://x",
    )


def _tokyo_s2_cues():
    return [
        EnhancementCue(
            type="broll",
            description="DOG-EYE: izakaya doorway amber lanterns; stranger pets dog",
        ),
        EnhancementCue(
            type="broll",
            description="DOG-EYE: ramen alley noren steam; man follows",
        ),
        EnhancementCue(
            type="overlay",
            description="Title: 它低头选路，我只好跟着",
        ),
        EnhancementCue(
            type="broll",
            description="konbini glass fluorescent spill; man offers treat",
        ),
    ]


def test_visual_cues_exclude_overlay():
    ch = StoryChapter(
        id="c",
        title="t",
        description="d",
        duration=11.0,
        order=0,
        enhancement_cues=_tokyo_s2_cues(),
    )
    cues = _visual_enhancement_cues(ch)
    assert len(cues) == 3
    assert all(c["type"] == "broll" for c in cues)


def test_visual_cues_exclude_animation_include_hard_event():
    ch = StoryChapter(
        id="c",
        title="t",
        description="d",
        duration=8.0,
        order=0,
        enhancement_cues=[
            EnhancementCue(type="broll", description="door slam", timestamp_hint="0-2"),
            EnhancementCue(type="animation", description="kinetic title burst", timestamp_hint="2"),
            EnhancementCue(type="hard_event", description="hand slaps desk", timestamp_hint="4-6"),
        ],
    )
    cues = _visual_enhancement_cues(ch)
    assert len(cues) == 2
    assert {c["type"] for c in cues} == {"broll", "hard_event"}


def test_llm_scene_folds_machine_fields_into_additional_data():
    from app.models.video_state import ShotLanguage, StoryboardSceneForLLM, ScenesCollectionForLLM
    from app.services.agent.video.scene_generation_service import _llm_scenes_collection_to_runtime
    from app.services.agent.video.visual_field_contract import (
        ACTION_BEATS_KEY,
        HERO_MOMENT_KEY,
        SHOT_LANGUAGE_KEY,
    )

    llm = ScenesCollectionForLLM(
        scenes=[
            StoryboardSceneForLLM(
                scene_number=1,
                title="t",
                description="SUBJECT: … SUBJECT MOTION: slap desk",
                duration=5.0,
                camera_angle="中景",
                character_action="slap",
                visual_style="v",
                transition_style="切",
                character_ids=["c1"],
                shot_language=ShotLanguage(
                    shot_size="medium",
                    camera_movement="dolly_in",
                    lens_mm=35,
                ),
                action_beats=["hand hits desk", "papers flutter"],
                hero_moment=True,
            )
        ]
    )
    runtime = _llm_scenes_collection_to_runtime(llm)
    assert len(runtime.scenes) == 1
    ad = runtime.scenes[0].additional_data or {}
    assert ad[SHOT_LANGUAGE_KEY]["shot_size"] == "medium"
    assert ad[ACTION_BEATS_KEY] == ["hand hits desk", "papers flutter"]
    assert ad[HERO_MOMENT_KEY] is True
    dumped = runtime.scenes[0].model_dump()
    assert "shot_language" not in dumped
    assert "action_beats" not in dumped


def test_target_count_duration_only_when_no_cues():
    # 16s / 8 → 2；无 cue 不扩
    assert _target_scene_count_for_chapter(16.0, duration_based_count=2, visual_cue_count=0) == 2
    assert _target_scene_count_for_chapter(11.0, duration_based_count=2, visual_cue_count=1) == 2


def test_target_count_cues_expand_within_min_wall_clock():
    # 11s，duration_based=2，3 条 broll，min 3s → 最多 3 镜
    n = _target_scene_count_for_chapter(11.0, duration_based_count=2, visual_cue_count=3)
    assert n == 3


def test_tokyo_s2_like_chapter_splits_to_three_scenes_seedance2():
    """同东京 s2：~11s + 3 broll → 3 镜（不是仅 duration 的 2 镜）。"""
    opt = UserOption(video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V)
    tx = _tx_one_segment(11.0)
    ch = StoryChapter(
        id="s2",
        title="狗选路",
        description="它低头选路，我只好跟着",
        duration=11.0,
        order=1,
        audio_segment_ids=["u0"],
        enhancement_cues=_tokyo_s2_cues(),
    )
    structure = _compute_scene_structure_for_chapter(ch, tx, user_option=opt)
    assert len(structure) == 3
    assert abs(sum(s["duration"] for s in structure) - 11.0) < 0.01
    # 每镜应绑上一条视觉线索
    bound = [s.get("enhancement_cue", {}).get("description", "") for s in structure]
    assert all(bound)
    assert "izakaya" in bound[0].lower() or "lantern" in bound[0].lower()
    assert "ramen" in bound[1].lower() or "noren" in bound[1].lower()
    assert "konbini" in bound[2].lower() or "fluorescent" in bound[2].lower()


def test_no_cues_still_uses_duration_threshold_8():
    """无 cue：16s SD2 仍 2×8，不被旧 4s 逻辑切碎。"""
    opt = UserOption(video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V)
    tx = _tx_one_segment(16.0)
    ch = StoryChapter(
        id="ch0",
        title="t",
        description="d",
        duration=16.0,
        order=0,
        audio_segment_ids=["u0"],
        enhancement_cues=None,
    )
    structure = _compute_scene_structure_for_chapter(ch, tx, user_option=opt)
    assert len(structure) == 2
    assert all(abs(s["duration"] - 8.0) < 0.01 for s in structure)
