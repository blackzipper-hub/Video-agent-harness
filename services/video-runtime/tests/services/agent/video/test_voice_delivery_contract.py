"""Voice delivery contract unit tests."""
from types import SimpleNamespace

from app.services.agent.video.voice_delivery_contract import (
    VOICE_SEEDANCE_DIALOGUE,
    VOICE_SILENCE,
    VOICE_TTS_NARRATION,
    classify_voice_delivery,
    infer_contract_from_dialogue_narration,
    should_generate_narration_tts,
    voice_contract_patch,
)
from app.services.agent.video.narration_generation_service import (
    _filter_shots_for_narration_tts,
)
from app.models.video_state import DetailedShot
from app.models.tool_enums import GenerationMode


def _shot(**kwargs):
    base = dict(
        shot_number=1,
        duration=5.0,
        scene_description="d",
        camera_movement="固定",
        lighting="暖",
        visual_effects="无",
        transition="切",
        dialogue="",
        sound_effects="",
        narration=None,
        character_ids=[],
        generation_mode=GenerationMode.NORMAL.value,
        additional_data=None,
    )
    base.update(kwargs)
    return DetailedShot(**base)


def test_classify_om_directions():
    s = _shot(
        dialogue="停下",
        additional_data=voice_contract_patch(
            speaker_directions="Seedance口型对白；旁白静默",
            delivery_note="No TTS. Seedance quoted dialogue.",
        ),
    )
    assert classify_voice_delivery(s) == VOICE_SEEDANCE_DIALOGUE
    assert should_generate_narration_tts(s) is False

    n = _shot(
        narration="社畜林霜穿成了女配。",
        additional_data=voice_contract_patch(
            speaker_directions="TTS旁白",
            delivery_note="TTS narration only.",
            provider_text="社畜林霜穿成了女配。",
        ),
    )
    assert classify_voice_delivery(n) == VOICE_TTS_NARRATION
    assert should_generate_narration_tts(n) is True

    z = _shot(
        additional_data=voice_contract_patch(
            speaker_directions="silence / title only",
            delivery_note="No TTS. No dialogue.",
        ),
    )
    assert classify_voice_delivery(z) == VOICE_SILENCE


def test_filter_skips_seedance_dialogue_even_if_narration_mistyped():
    bad = _shot(
        shot_number=2,
        dialogue="请停下",
        narration="请停下",  # mistaken copy
        additional_data=voice_contract_patch(
            speaker_directions="Seedance口型对白；旁白静默",
            delivery_note="No TTS.",
        ),
    )
    good = _shot(
        shot_number=3,
        narration="穿书旁白一句。",
        additional_data=voice_contract_patch(
            speaker_directions="TTS旁白",
            delivery_note="TTS旁白。",
        ),
    )
    kept, _ = _filter_shots_for_narration_tts([bad, good], content_category="Default")
    assert [s.shot_number for s in kept] == [3]


def test_infer_contract():
    sp, dn = infer_contract_from_dialogue_narration("你好", None)
    assert "Seedance" in sp or "对白" in sp
    assert "No TTS" in dn or "TTS" in dn.upper() or "tts" in dn.lower() or "No TTS" in dn


def test_should_use_seedance_in_clip_dialogue_by_dialogue_field():
    from app.services.agent.video.voice_delivery_contract import (
        should_use_seedance_in_clip_dialogue,
    )

    assert should_use_seedance_in_clip_dialogue(
        _shot(dialogue="闭嘴！", narration=None)
    )
    assert not should_use_seedance_in_clip_dialogue(
        _shot(dialogue="", narration="旁白一句")
    )
    assert not should_use_seedance_in_clip_dialogue(
        _shot(
            dialogue="hi",
            additional_data=voice_contract_patch(
                speaker_directions="silence / title only",
                delivery_note="No TTS. No dialogue.",
            ),
        )
    )
