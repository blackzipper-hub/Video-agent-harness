"""assign_generation_mode_to_shots：不得将 normal/empty_shot/未设置 升格为 lipsync。"""
from types import SimpleNamespace

from app.models.tool_enums import GenerationMode
from app.services.agent.video.per_shot_generation_routing_service import (
    assign_generation_mode_to_shots,
)


def _seg(uuid: str, *, vocal_presence=True, text=""):
    return SimpleNamespace(uuid=uuid, vocal_presence=vocal_presence, text=text)


def _tx(*segments):
    return SimpleNamespace(segments=list(segments))


def _shot(*, gm=None, seg_uuids=None, char_ids=None):
    return SimpleNamespace(
        generation_mode=gm,
        audio_segment_ids=list(seg_uuids or ["s1"]),
        character_ids=list(char_ids or ["c1"]),
    )


def test_normal_with_vocal_and_full_coverage_stays_normal():
    shots = [_shot(gm=GenerationMode.NORMAL.value)]
    assign_generation_mode_to_shots(
        shots,
        _tx(_seg("s1")),
        content_category="Lip-Sync MV",
        lipsync_coverage=100,
        character_type_map={"c1": "character"},
    )
    assert shots[0].generation_mode == GenerationMode.NORMAL.value


def test_empty_shot_with_vocal_stays_empty_shot():
    shots = [_shot(gm=GenerationMode.EMPTY_SHOT.value)]
    assign_generation_mode_to_shots(
        shots,
        _tx(_seg("s1")),
        lipsync_coverage=100,
        character_type_map={"c1": "character"},
    )
    assert shots[0].generation_mode == GenerationMode.EMPTY_SHOT.value


def test_unset_generation_mode_becomes_normal_not_lipsync():
    shots = [_shot(gm=None)]
    assign_generation_mode_to_shots(
        shots,
        _tx(_seg("s1")),
        lipsync_coverage=100,
        character_type_map={"c1": "character"},
    )
    assert shots[0].generation_mode == GenerationMode.NORMAL.value


def test_upstream_lipsync_with_vocal_preserved():
    shots = [_shot(gm=GenerationMode.LIPSYNC.value)]
    assign_generation_mode_to_shots(
        shots,
        _tx(_seg("s1")),
        character_type_map={"c1": "character"},
        allow_lipsync=True,
    )
    assert shots[0].generation_mode == GenerationMode.LIPSYNC.value


def test_upstream_lipsync_without_vocal_downgrades_to_normal():
    shots = [_shot(gm=GenerationMode.LIPSYNC.value)]
    assign_generation_mode_to_shots(
        shots,
        _tx(_seg("s1", vocal_presence=False, text="")),
        character_type_map={"c1": "character"},
    )
    assert shots[0].generation_mode == GenerationMode.NORMAL.value


def test_upstream_lipsync_without_narration_and_no_transcription_downgrades_to_normal():
    shot = _shot(gm=GenerationMode.LIPSYNC.value, seg_uuids=[])
    shot.narration = ""
    shots = [shot]
    assign_generation_mode_to_shots(
        shots,
        None,
        character_type_map={"c1": "character"},
    )
    assert shots[0].generation_mode == GenerationMode.NORMAL.value


def test_upstream_lipsync_with_narration_and_no_transcription_preserved():
    shot = _shot(gm=GenerationMode.LIPSYNC.value, seg_uuids=[])
    shot.narration = "产品介绍开场白"
    shots = [shot]
    assign_generation_mode_to_shots(
        shots,
        None,
        character_type_map={"c1": "character"},
    )
    assert shots[0].generation_mode == GenerationMode.LIPSYNC.value


def test_non_person_shot_forced_empty_shot():
    shots = [_shot(gm=GenerationMode.LIPSYNC.value)]
    assign_generation_mode_to_shots(
        shots,
        _tx(_seg("s1")),
        lipsync_coverage=100,
        character_type_map={"c1": "location"},
    )
    assert shots[0].generation_mode == GenerationMode.EMPTY_SHOT.value


def test_non_person_shot_empty_shot_without_audio_transcription():
    shots = [_shot(gm=GenerationMode.LIPSYNC.value, seg_uuids=[])]
    assign_generation_mode_to_shots(
        shots,
        None,
        character_type_map={"c1": "object"},
    )
    assert shots[0].generation_mode == GenerationMode.EMPTY_SHOT.value


def test_product_launch_non_person_with_narration_stays_normal():
    shot = _shot(gm=GenerationMode.NORMAL.value, seg_uuids=[], char_ids=["c1"])
    shot.narration = "智趣生活，由此开启。"
    shots = [shot]
    assign_generation_mode_to_shots(
        shots,
        None,
        content_category="Product Launch",
        character_type_map={"c1": "object"},
    )
    assert shots[0].generation_mode == GenerationMode.NORMAL.value


def test_product_launch_non_person_without_narration_stays_empty_shot():
    shot = _shot(gm=GenerationMode.NORMAL.value, seg_uuids=[], char_ids=["c1"])
    shot.narration = ""
    shots = [shot]
    assign_generation_mode_to_shots(
        shots,
        None,
        content_category="Product Launch",
        character_type_map={"c1": "object"},
    )
    assert shots[0].generation_mode == GenerationMode.EMPTY_SHOT.value
