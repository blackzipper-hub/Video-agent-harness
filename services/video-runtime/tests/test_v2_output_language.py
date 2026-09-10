from pathlib import Path

from app.chat.v2.language import (
    LanguageSkillConflictError,
    active_language_skill,
    explicit_language_skill,
    language_contract,
    output_language_instruction,
    resolve_video_language_contract,
    resolve_output_language,
    resolve_spoken_language,
    video_language_instruction,
)
import pytest
from app.chat.v2.skill_catalog import SkillCatalog


@pytest.mark.parametrize("text,spoken,subtitle", [
    ("为当前完整成片添加 HyperFrames 字幕，根据实际英文对白生成中文字幕，保持画面和原声不变。", "en-US", "zh-CN"),
    ("添加字幕，根据实际中文对白生成英文字幕，保持原声不变。", "zh-CN", "en-US"),
    ("Add captions in Chinese based on English dialogue.", "en-US", "zh-CN"),
])
def test_subtitle_translation_does_not_conflict_with_source_language(text, spoken, subtitle):
    result = resolve_video_language_contract(text, ui_locale="zh")
    assert result["spoken_language"] == spoken
    assert result["subtitle_language"] == subtitle


def test_default_captions_follow_speech_not_ui_or_previous_translation():
    result = resolve_video_language_contract("给视频添加字幕", ui_locale="zh", current={
        "content_language": "zh-CN", "spoken_language": "en-US", "subtitle_language": "zh-CN",
    })
    assert result["subtitle_language"] == "en-US"


def test_product_ad_workflow_category_maps_to_legacy_product_launch():
    from app.models.tool_enums import ContentCategory

    assert ContentCategory.PRODUCT_LAUNCH.value == "Product Launch"


def test_chinese_message_overrides_english_ui_language():
    assert resolve_output_language("请生成一个商业广告", requested="en") == "zh"


def test_ui_language_preserves_chinese_for_language_neutral_followup():
    assert resolve_output_language("OK", requested="zh", current="en") == "zh"


def test_explicit_english_skill_wins_over_chinese_message_and_alias_is_canonical():
    selected = explicit_language_skill("$eng 请生成英文广告")
    assert selected == "language-en"
    assert resolve_output_language(
        "请生成英文广告", requested="zh", language_skill=selected,
    ) == "en"


def test_language_skill_conflict_is_rejected():
    with pytest.raises(LanguageSkillConflictError):
        explicit_language_skill("$zh $eng generate an ad")


def test_active_language_skill_and_structured_contract():
    assert active_language_skill(["cuti-product-workflow", "language-zh"]) == "language-zh"
    assert language_contract("en", provider_prompt_language="zh-CN") == {
        "user_visible_language": "en-US",
        "spoken_language": "en-US",
        "on_screen_text_language": "en-US",
        "provider_prompt_language": "zh-CN",
    }


def test_explicit_english_dialogue_is_independent_from_chinese_output():
    request = "制作一支中文策划的产品广告，对话保持英文"
    assert resolve_output_language(request, requested="zh") == "zh"
    assert resolve_spoken_language(request, output_language="zh") == "en-US"
    instruction = output_language_instruction("zh", user_request=request)
    assert "Simplified Chinese" in instruction
    assert "Spoken narration/dialogue language: en-US" in instruction
    assert "never translate spoken lines" in instruction


def test_explicit_chinese_voiceover_is_independent_from_english_output():
    request = "Create an English shot plan, but use Chinese narration"
    assert resolve_spoken_language(request, output_language="en") == "zh-CN"


def test_video_language_contract_separates_ui_content_speech_and_subtitles():
    contract = resolve_video_language_contract(
        "请用英文展示全部策划，人物说中文，并配英文字幕",
        ui_locale="zh",
    )
    assert contract == {
        "ui_locale": "zh-CN",
        "content_language": "en-US",
        "spoken_language": "zh-CN",
        "subtitle_language": "en-US",
        "provider_prompt_language": "auto",
    }
    rendered = video_language_instruction(contract)
    assert "every user-visible" in rendered
    assert "content_language=en-US" in rendered


def test_followup_keeps_project_content_language_without_explicit_switch():
    current = resolve_video_language_contract(
        "Write all content in English", ui_locale="en",
    )
    updated = resolve_video_language_contract(
        "请把第二个镜头改快一点", ui_locale="zh", current=current,
    )
    assert updated["ui_locale"] == "zh-CN"
    assert updated["content_language"] == "en-US"


def test_followup_keeps_independent_spoken_language_without_explicit_switch():
    current = resolve_video_language_contract(
        "请用中文展示全部策划，人物对白使用英文",
        ui_locale="zh",
    )
    updated = resolve_video_language_contract(
        "把第二个镜头改快一点",
        ui_locale="zh",
        current=current,
    )
    assert updated["content_language"] == "zh-CN"
    assert updated["spoken_language"] == "en-US"


def test_run_language_skill_has_priority_over_inline_spoken_language():
    assert resolve_spoken_language(
        "$eng 对话使用中文",
        output_language="en",
        language_skill="language-en",
    ) == "en-US"


def test_language_skills_are_discoverable_as_run_scoped_skills():
    catalog = SkillCatalog([Path(__file__).parents[1] / "skills" / "external"])
    discovered = {item.name: item for item in catalog.discover()}
    assert discovered["language-zh"].metadata["scope"]["type"] == "run"
    assert discovered["language-en"].metadata["scope"]["type"] == "run"
