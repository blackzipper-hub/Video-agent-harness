"""should_enable_lipsync_for_run：BGM / 纯音乐 / 无人声分段不得走口型。"""
from types import SimpleNamespace

from app.models.tool_enums import ContentCategory, GenerationMode
from app.models.user_options import UserOption, should_enable_lipsync_for_run
from app.services.agent.video.per_shot_generation_routing_service import (
    assign_generation_mode_to_shots,
)


def _opt(*, lipsync_coverage: int = 50) -> UserOption:
    return UserOption(lipsync_coverage=lipsync_coverage, content_category=ContentCategory.DEFAULT)


def test_instrumental_bgm_intent_disables_lipsync_despite_coverage():
    assert should_enable_lipsync_for_run(
        _opt(lipsync_coverage=100),
        music_intent="instrumental_bgm",
    ) is False


def test_bgm_parallel_mode_disables_lipsync():
    assert should_enable_lipsync_for_run(
        _opt(lipsync_coverage=100),
        music_workflow_mode="bgm_parallel",
    ) is False


def test_instrumental_transcription_disables_lipsync():
    tx = SimpleNamespace(is_instrumental=True, segments=[SimpleNamespace(vocal_presence=False, text="")])
    assert should_enable_lipsync_for_run(_opt(lipsync_coverage=100), audio_transcription=tx) is False


def test_short_drama_disables_lipsync_despite_coverage():
    opt = UserOption(
        lipsync_coverage=50,
        content_category=ContentCategory.SHORT_DRAMA,
    )
    assert should_enable_lipsync_for_run(opt) is False


def test_assign_strips_lipsync_when_not_allowed_without_transcription():
    shots = [SimpleNamespace(generation_mode=GenerationMode.LIPSYNC.value, audio_segment_ids=[], character_ids=["c1"])]
    assign_generation_mode_to_shots(
        shots,
        None,
        character_type_map={"c1": "character"},
        allow_lipsync=False,
    )
    assert shots[0].generation_mode == GenerationMode.NORMAL.value
