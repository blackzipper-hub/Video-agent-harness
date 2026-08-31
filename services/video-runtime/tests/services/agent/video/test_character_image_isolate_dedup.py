"""
角色-图片匹配「一图一角色去重 / 单一元素提取」逻辑单测。

只针对 _match_characters_with_images 在拿到 LLM 匹配结果之后的分流逻辑，
mock 掉 LLM 调用（build_prompt_for_character_image_matching / ainvoke_structured_resilient），
不依赖真实模型与网络。
"""
import pytest
from unittest.mock import patch, AsyncMock

import app.services.agent.video.main_character_design_service as svc
from app.services.agent.video.main_character_design_service import (
    _match_characters_with_images,
    CharacterImageMatch,
    CharacterImageMatchingResult,
)
from app.models.video_state import CharacterProfile, ImageUserInput


def _make_characters():
    return [
        CharacterProfile(
            id="char-1", type="character", name="小明", description="男孩", personality="开朗",
            appearance="短发", role="主角", style="真实摄影风格", body_type="中等",
        ),
        CharacterProfile(
            id="char-2", type="character", name="小红", description="女孩", personality="温柔",
            appearance="长发", role="配角", style="真实摄影风格", body_type="娇小",
        ),
    ]


def _patch_llm(matches):
    """构造 mock：build_prompt 返回固定 messages，ainvoke 返回带 matches 的结构化结果。"""
    fake_messages = ["sys", "human"]
    agent_result = {
        "structured_response": CharacterImageMatchingResult(matches=matches),
        "messages": fake_messages,
    }
    return (
        patch.object(svc, "build_prompt_for_character_image_matching",
                     new=AsyncMock(return_value=(fake_messages, None))),
        patch.object(svc, "ainvoke_structured_resilient",
                     new=AsyncMock(return_value=agent_result)),
    )


@pytest.mark.asyncio
async def test_shared_group_photo_routes_extra_char_to_isolate():
    """一张合照被两个角色命中：第一个直接复用，第二个走单一元素提取（isolate）。"""
    characters = _make_characters()
    group_url = "https://img.example.com/group.jpg"
    images = [ImageUserInput(url=group_url)]

    matches = [
        CharacterImageMatch(character_id="char-1", character_name="小明",
                            matched_image_indices=[1], match_reason="命中", confidence=90, style_match=True),
        CharacterImageMatch(character_id="char-2", character_name="小红",
                            matched_image_indices=[1], match_reason="命中", confidence=88, style_match=True),
    ]
    p_build, p_invoke = _patch_llm(matches)
    with p_build, p_invoke, patch.object(svc, "ENABLE_DIRECT_UPLOAD_REUSE", True), patch.object(
        svc, "ENABLE_CHARACTER_STUDIO_BACKGROUND", False
    ):
        _msgs, result = await _match_characters_with_images(characters, images)

    matched = result["matched_characters"]
    isolate = result["isolate_characters"]

    assert len(matched) == 1, "同一张合照只允许一个角色直接复用"
    assert matched[0][0].id == "char-1"
    assert matched[0][1] == group_url

    assert len(isolate) == 1, "另一个共用合照的角色应走单一元素提取"
    assert isolate[0][0].id == "char-2"
    assert isolate[0][1] == [group_url]

    assert result["unmatched_characters"] == []
    assert result["style_regen_characters"] == []
    # 合照已被使用，不计入未使用图片
    assert all(img.url != group_url for img in result["unused_images"])


@pytest.mark.asyncio
async def test_distinct_photos_both_direct_reuse():
    """两个角色命中两张不同的图：都可直接复用，不走 isolate。"""
    characters = _make_characters()
    url1, url2 = "https://img.example.com/a.jpg", "https://img.example.com/b.jpg"
    images = [ImageUserInput(url=url1), ImageUserInput(url=url2)]

    matches = [
        CharacterImageMatch(character_id="char-1", character_name="小明",
                            matched_image_indices=[1], match_reason="命中", confidence=90, style_match=True),
        CharacterImageMatch(character_id="char-2", character_name="小红",
                            matched_image_indices=[2], match_reason="命中", confidence=90, style_match=True),
    ]
    p_build, p_invoke = _patch_llm(matches)
    with p_build, p_invoke, patch.object(svc, "ENABLE_DIRECT_UPLOAD_REUSE", True), patch.object(
        svc, "ENABLE_CHARACTER_STUDIO_BACKGROUND", False
    ):
        _msgs, result = await _match_characters_with_images(characters, images)

    assert len(result["matched_characters"]) == 2
    assert len(result["isolate_characters"]) == 0


@pytest.mark.asyncio
async def test_switch_off_routes_all_matched_to_isolate():
    """关闭直接复用开关：所有命中图的角色都走单一元素提取。"""
    characters = _make_characters()
    url1, url2 = "https://img.example.com/a.jpg", "https://img.example.com/b.jpg"
    images = [ImageUserInput(url=url1), ImageUserInput(url=url2)]

    matches = [
        CharacterImageMatch(character_id="char-1", character_name="小明",
                            matched_image_indices=[1], match_reason="命中", confidence=90, style_match=True),
        CharacterImageMatch(character_id="char-2", character_name="小红",
                            matched_image_indices=[2], match_reason="命中", confidence=90, style_match=True),
    ]
    p_build, p_invoke = _patch_llm(matches)
    with p_build, p_invoke, patch.object(svc, "ENABLE_DIRECT_UPLOAD_REUSE", False), patch.object(
        svc, "ENABLE_CHARACTER_STUDIO_BACKGROUND", False
    ):
        _msgs, result = await _match_characters_with_images(characters, images)

    assert len(result["matched_characters"]) == 0
    assert len(result["isolate_characters"]) == 2


@pytest.mark.asyncio
async def test_style_mismatch_still_goes_to_style_regen():
    """风格不匹配仍走 style_regen，不受去重逻辑影响。"""
    characters = _make_characters()[:1]
    url1 = "https://img.example.com/a.jpg"
    images = [ImageUserInput(url=url1)]

    matches = [
        CharacterImageMatch(character_id="char-1", character_name="小明",
                            matched_image_indices=[1], match_reason="风格不符", confidence=90, style_match=False),
    ]
    p_build, p_invoke = _patch_llm(matches)
    with p_build, p_invoke, patch.object(svc, "ENABLE_DIRECT_UPLOAD_REUSE", True), patch.object(
        svc, "ENABLE_CHARACTER_STUDIO_BACKGROUND", False
    ):
        _msgs, result = await _match_characters_with_images(characters, images)

    assert len(result["style_regen_characters"]) == 1
    assert len(result["matched_characters"]) == 0
    assert len(result["isolate_characters"]) == 0


@pytest.mark.asyncio
async def test_studio_background_routes_solo_photo_to_isolate():
    """启用 studio 背景时，单人独照也不直接复用，统一走 I2I isolate。"""
    characters = _make_characters()[:1]
    url1 = "https://img.example.com/solo.jpg"
    images = [ImageUserInput(url=url1)]

    matches = [
        CharacterImageMatch(character_id="char-1", character_name="小明",
                            matched_image_indices=[1], match_reason="命中", confidence=90, style_match=True),
    ]
    p_build, p_invoke = _patch_llm(matches)
    with p_build, p_invoke, patch.object(svc, "ENABLE_DIRECT_UPLOAD_REUSE", True), patch.object(
        svc, "ENABLE_CHARACTER_STUDIO_BACKGROUND", True
    ):
        _msgs, result = await _match_characters_with_images(characters, images)

    assert len(result["matched_characters"]) == 0
    assert len(result["isolate_characters"]) == 1
    assert result["isolate_characters"][0][0].id == "char-1"
    assert result["isolate_characters"][0][1] == [url1]


@pytest.mark.asyncio
async def test_studio_background_shared_group_photo_both_isolate():
    """启用 studio 背景时，合照两角色均走 isolate，无直接复用。"""
    characters = _make_characters()
    group_url = "https://img.example.com/group.jpg"
    images = [ImageUserInput(url=group_url)]

    matches = [
        CharacterImageMatch(character_id="char-1", character_name="小明",
                            matched_image_indices=[1], match_reason="命中", confidence=90, style_match=True),
        CharacterImageMatch(character_id="char-2", character_name="小红",
                            matched_image_indices=[1], match_reason="命中", confidence=88, style_match=True),
    ]
    p_build, p_invoke = _patch_llm(matches)
    with p_build, p_invoke, patch.object(svc, "ENABLE_DIRECT_UPLOAD_REUSE", True), patch.object(
        svc, "ENABLE_CHARACTER_STUDIO_BACKGROUND", True
    ):
        _msgs, result = await _match_characters_with_images(characters, images)

    assert len(result["matched_characters"]) == 0
    assert len(result["isolate_characters"]) == 2


@pytest.mark.asyncio
async def test_t2i_character_prompt_includes_studio_background():
    """T2I 无参考图时，人物角色 prompt 也应包含 studio 浅灰白渐变背景指令。"""
    from prompts.prompt_loader import load_prompt_with_fallback_async
    from prompts.prompt_config import PromptName
    from app.services.agent.video.main_character_design_service import (
        build_prompt_for_character_image_generation,
    )

    character = _make_characters()[0]
    prompt_template, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.VIDEO_MAIN_CHARACTER_IMAGE_GENERATION.value,
        local_template_name="video/main_character_design/video_main_character_image_generation",
        schema=None,
        include_raw=False,
    )
    messages = await build_prompt_for_character_image_generation(
        character=character,
        images=[],
        prompt_template=prompt_template,
        user_option=None,
        story_outline=None,
        user_input="",
        detected_language="zh",
    )
    system_text = ""
    for m in messages:
        if m.__class__.__name__ == "SystemMessage":
            system_text = m.content if isinstance(m.content, str) else str(m.content)
            break

    assert "Studio 背景（T2I 模式）" in system_text
    assert "light gray-to-white gradient" in system_text
    assert "studio_background_normalization" not in system_text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
