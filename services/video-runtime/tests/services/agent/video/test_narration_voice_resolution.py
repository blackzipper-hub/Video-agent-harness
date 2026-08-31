"""narration_gender：detail 节点产出，TTS 只读。"""
from app.models.image_result import VoiceID
from app.models.video_state import CharacterProfile, DetailedShot, VisualElementType
from app.services.agent.video.narration_gender_utils import (
    NARRATION_GENDER_KEY,
    infer_gender_from_character,
    narration_gender_from_shot,
    normalize_narration_gender,
    resolve_narration_gender_for_detail_shot,
)
from app.services.agent.video.narration_generation_service import (
    _filter_shots_for_narration_tts,
    _resolve_default_voice_id_for_shot,
)
from app.models.tool_enums import GenerationMode
from app.tools.context_schemas import SpeechGenerationContext
from app.tools.narration.speech_tool_wrapper import _resolve_voice_id


def _male_character() -> CharacterProfile:
    return CharacterProfile(
        id="char_1",
        type=VisualElementType.CHARACTER,
        name="产品经理",
        description="品牌发布会主讲人",
        personality="专业",
        appearance="三十岁商务男士，短发，深色西装",
        role="讲解者",
    )


class _ShotLlm:
    shot_number = 1
    narration_gender = "m"


def test_normalize_narration_gender():
    assert normalize_narration_gender("m") == "m"
    assert normalize_narration_gender("男") == "m"
    assert normalize_narration_gender("f") == "f"
    assert normalize_narration_gender("calm") is None


def test_resolve_narration_gender_prefers_llm_field():
    chars = {"char_1": _male_character()}
    gender = resolve_narration_gender_for_detail_shot(
        _ShotLlm(), ["char_1"], chars, "我们始终坚信卓越设计。",
    )
    assert gender == "m"


def test_resolve_narration_gender_fallback_from_character():
    class _ShotLlmNoGender:
        shot_number = 2
        narration_gender = None

    chars = {"char_1": _male_character()}
    gender = resolve_narration_gender_for_detail_shot(
        _ShotLlmNoGender(), ["char_1"], chars, "旁白文本",
    )
    assert gender == "m"


def test_narration_gender_from_shot_reads_additional_data():
    shot = DetailedShot(
        shot_number=1,
        duration=5,
        character_ids=[],
        additional_data={NARRATION_GENDER_KEY: "m"},
    )
    assert narration_gender_from_shot(shot) == "m"


def test_narration_gender_from_shot_prefers_top_level_field():
    shot = DetailedShot(
        shot_number=1,
        duration=5,
        character_ids=[],
        narration_gender="f",
        additional_data={NARRATION_GENDER_KEY: "m"},
    )
    assert narration_gender_from_shot(shot) == "f"


def test_tts_default_voice_uses_detail_narration_gender():
    shot = DetailedShot(
        shot_number=1,
        duration=5,
        character_ids=["char_1"],
        narration_gender="m",
    )
    voice = _resolve_default_voice_id_for_shot(shot, "zh")
    assert voice == VoiceID.CHINESE_MALE_ANNOUNCER.value


def test_tts_voice_correction_uses_detail_narration_gender():
    ctx = SpeechGenerationContext(
        detected_language="zh",
        speaker_gender="m",
        default_voice_id=VoiceID.CHINESE_MALE_ANNOUNCER.value,
    )
    assert _resolve_voice_id(VoiceID.CHINESE_NEWS_ANCHOR.value, ctx) == (
        VoiceID.CHINESE_MALE_ANNOUNCER.value
    )


def test_infer_gender_from_character_male():
    assert infer_gender_from_character(_male_character()) == "m"


def test_filter_narration_tts_skips_empty_shot_by_default():
    shot = DetailedShot(
        shot_number=1,
        duration=3,
        character_ids=[],
        narration="片头 slogan",
        generation_mode=GenerationMode.EMPTY_SHOT.value,
    )
    with_tts, skipped = _filter_shots_for_narration_tts([shot])
    assert with_tts == []
    assert len(skipped) == 1


def test_filter_narration_tts_includes_empty_shot_for_product_launch():
    shot = DetailedShot(
        shot_number=10,
        duration=3,
        character_ids=[],
        narration="智趣生活，触手可及。",
        generation_mode=GenerationMode.EMPTY_SHOT.value,
    )
    with_tts, skipped = _filter_shots_for_narration_tts(
        [shot], content_category="Product Launch"
    )
    assert len(with_tts) == 1
    assert skipped == []
