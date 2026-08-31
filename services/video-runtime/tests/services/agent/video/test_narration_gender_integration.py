"""
narration_gender + TTS 真实集成测试（会调 WaveSpeed / Gemini API，产生费用）。

运行：
  cd Cuti-VideoAgent
  conda run -n cuti-video-local python -m pytest \\
    tests/services/agent/video/test_narration_gender_integration.py -v -s -m integration

仅 TTS（跳过 detail LLM）：
  conda run -n cuti-video-local python -m pytest \\
    tests/services/agent/video/test_narration_gender_integration.py -v -s -m integration -k tts
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

from app.models.image_result import VoiceID
from app.models.tool_enums import ContentCategory
from app.models.user_options import UserOption
from app.models.video_state import (
    CharacterProfile,
    DetailedShot,
    StoryboardScene,
    StoryChapter,
    StoryOutline,
    StoryStructure,
    VisualElementType,
)
from app.services.agent.video.narration_gender_utils import (
    NARRATION_GENDER_KEY,
    resolve_narration_gender_for_detail_shot,
)
from app.tools.context_schemas import SpeechGenerationContext


MALE_VOICE = VoiceID.CHINESE_MALE_ANNOUNCER.value
FEMALE_VOICE = VoiceID.CHINESE_NEWS_ANCHOR.value
SAMPLE_NARRATION = "我们始终坚信，卓越的设计，是通往未来的第一步。"


@dataclass
class _FakeToolRuntime:
    context: SpeechGenerationContext


def _require_wavespeed_key():
    if not os.getenv("WAVESPEED_API_KEY"):
        pytest.skip("WAVESPEED_API_KEY not set")


def _require_google_key():
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY not set")


def _male_presenter_character() -> CharacterProfile:
    return CharacterProfile(
        id="char_presenter_m",
        type=VisualElementType.CHARACTER,
        name="品牌主讲人",
        description="产品发布会男性讲解者",
        personality="专业、自信",
        appearance="三十岁商务男士，短发，深色西装",
        role="讲解者",
    )


def _female_presenter_character() -> CharacterProfile:
    return CharacterProfile(
        id="char_presenter_f",
        type=VisualElementType.CHARACTER,
        name="品牌女主播",
        description="产品发布会女性讲解者",
        personality="亲和、专业",
        appearance="二十八岁职业女性，齐肩发，浅色套装",
        role="讲解者",
    )


def _extract_tool_voice_ids(messages: List[Any]) -> List[str]:
    """从 agent messages 里提取 speech 工具调用使用的 voice_id。"""
    voice_ids: List[str] = []
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            if name != "generate_speech_with_fallback":
                continue
            args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {}) or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            vid = args.get("voice_id")
            if vid:
                voice_ids.append(vid)
    return voice_ids


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_tts_wrapper_corrects_to_male_voice():
    """真实 WaveSpeed TTS：speaker_gender=m 时即使用女声 ID 也应校正为男声。"""
    _require_wavespeed_key()
    from app.tools.narration.speech_tool_wrapper import _run_speech_loop

    runtime = _FakeToolRuntime(
        context=SpeechGenerationContext(
            detected_language="zh",
            speaker_gender="m",
            default_voice_id=MALE_VOICE,
            shot_number=1,
            target_duration=4.0,
        )
    )
    result = await _run_speech_loop(
        SAMPLE_NARRATION,
        FEMALE_VOICE,
        "neutral",
        1.0,
        0,
        1.0,
        runtime,  # type: ignore[arg-type]
    )
    assert result.success is True, result.message
    assert result.voice_id == MALE_VOICE, f"expected male voice, got {result.voice_id}"
    assert result.audio_url
    print(f"[male TTS] voice_id={result.voice_id} duration={result.duration}s url={result.audio_url}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_tts_wrapper_corrects_to_female_voice():
    """真实 WaveSpeed TTS：speaker_gender=f 时即使用男声 ID 也应校正为女声。"""
    _require_wavespeed_key()
    from app.tools.narration.speech_tool_wrapper import _run_speech_loop

    runtime = _FakeToolRuntime(
        context=SpeechGenerationContext(
            detected_language="zh",
            speaker_gender="f",
            default_voice_id=FEMALE_VOICE,
            shot_number=2,
            target_duration=4.0,
        )
    )
    result = await _run_speech_loop(
        SAMPLE_NARRATION,
        MALE_VOICE,
        "neutral",
        1.0,
        0,
        1.0,
        runtime,  # type: ignore[arg-type]
    )
    assert result.success is True, result.message
    assert result.voice_id == FEMALE_VOICE, f"expected female voice, got {result.voice_id}"
    assert result.audio_url
    print(f"[female TTS] voice_id={result.voice_id} duration={result.duration}s url={result.audio_url}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_narration_generation_uses_male_voice_from_detail_gender():
    """真实旁白 agent + TTS：detail 写入 narration_gender=m 应生成男声。"""
    _require_wavespeed_key()
    _require_google_key()
    from prompts.prompt_config import PromptName
    from prompts.prompt_loader import load_prompt_with_fallback_async
    from app.services.agent.video.narration_generation_service import (
        _generate_single_narration,
    )

    shot = DetailedShot(
        shot_number=1,
        duration=4.0,
        character_ids=["char_presenter_m"],
        narration=SAMPLE_NARRATION,
        narration_gender="m",
        additional_data={NARRATION_GENDER_KEY: "m"},
        scene_description="男性主讲人正对镜头讲解产品理念。",
        lighting="柔和暖光",
        is_bridge=False,
        generation_mode="lipsync",
    )
    state = {
        "detected_language": "zh",
        "conversation_id": 0,
        "thread_id": "integration-narration-gender-m",
        "run_id": "integration-run-m",
        "user_id": "integration-user",
    }
    prompt_template, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.VIDEO_NARRATION_GENERATION.value,
        local_template_name="video/narration/video_narration_generation",
        schema=None,
        include_raw=False,
    )
    chars = {"char_presenter_m": _male_presenter_character()}

    narration_version, messages = await _generate_single_narration(
        shot,
        "integration-story-outline",
        state,
        cached_prompt_template=prompt_template,
        characters_by_id=chars,
    )

    assert narration_version.success is True, narration_version.error_msg
    assert narration_version.audio_url
    assert narration_version.params
    assert narration_version.params.get("voice_id") == MALE_VOICE, narration_version.params
    assert narration_version.params.get("emotion") in {
        "happy", "sad", "angry", "fearful", "disgusted", "surprised", "neutral",
    }
    tool_voices = _extract_tool_voice_ids(messages)
    print(f"[e2e male] params={narration_version.params} tool_voices={tool_voices}")
    print(f"[e2e male] audio={narration_version.audio_url} duration={narration_version.duration}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_narration_generation_uses_female_voice_from_detail_gender():
    """真实旁白 agent + TTS：detail 写入 narration_gender=f 应生成女声。"""
    _require_wavespeed_key()
    _require_google_key()
    from prompts.prompt_config import PromptName
    from prompts.prompt_loader import load_prompt_with_fallback_async
    from app.services.agent.video.narration_generation_service import (
        _generate_single_narration,
    )

    shot = DetailedShot(
        shot_number=2,
        duration=4.0,
        character_ids=["char_presenter_f"],
        narration=SAMPLE_NARRATION,
        narration_gender="f",
        additional_data={NARRATION_GENDER_KEY: "f"},
        scene_description="女性主讲人正对镜头讲解产品理念。",
        lighting="柔和暖光",
        is_bridge=False,
        generation_mode="lipsync",
    )
    state = {
        "detected_language": "zh",
        "conversation_id": 0,
        "thread_id": "integration-narration-gender-f",
        "run_id": "integration-run-f",
        "user_id": "integration-user",
    }
    prompt_template, _ = await load_prompt_with_fallback_async(
        hub_name=PromptName.VIDEO_NARRATION_GENERATION.value,
        local_template_name="video/narration/video_narration_generation",
        schema=None,
        include_raw=False,
    )
    chars = {"char_presenter_f": _female_presenter_character()}

    narration_version, messages = await _generate_single_narration(
        shot,
        "integration-story-outline",
        state,
        cached_prompt_template=prompt_template,
        characters_by_id=chars,
    )

    assert narration_version.success is True, narration_version.error_msg
    assert narration_version.audio_url
    assert narration_version.params
    assert narration_version.params.get("voice_id") == FEMALE_VOICE, narration_version.params
    tool_voices = _extract_tool_voice_ids(messages)
    print(f"[e2e female] params={narration_version.params} tool_voices={tool_voices}")
    print(f"[e2e female] audio={narration_version.audio_url} duration={narration_version.duration}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_storyboard_detail_generates_narration_gender():
    """真实 detail LLM：Product Launch 男性讲解镜应产出 narration + narration_gender=m。"""
    pytest.skip(
        "storyboard craft migrated to storyboard-director deep-agent; "
        "mustache build_prompt_for_storyboard_detail_generation_node removed"
    )
    _require_google_key()
    from prompts.prompt_config import PROMPTS_CONFIG, PromptName
    from app.services.agent.utils.llm_resilience import (
        StructuredResilienceKind,
        ainvoke_structured_resilient,
    )

    male_char = _male_presenter_character()
    scene = StoryboardScene(
        uuid="scene-1",
        scene_number=1,
        title="主讲人开场",
        description="男性品牌主讲人面对镜头介绍产品设计理念。",
        duration=4.0,
        camera_angle="平视",
        character_action="男性主讲人自信讲解，手势自然",
        visual_style="明亮商务风格",
        transition_style="切",
        character_ids=[male_char.id],
        is_bridge=False,
        generation_mode="lipsync",
    )
    outline = StoryOutline(
        title="卓越设计产品发布",
        theme="设计驱动未来",
        structure=StoryStructure(
            chapters=[
                StoryChapter(
                    id="ch1",
                    title="开场",
                    description="主讲人介绍产品理念",
                    duration=4.0,
                    order=0,
                )
            ]
        ),
        key_message="卓越设计是通往未来的第一步",
        total_duration=4.0,
        style_guide="现代商务",
        description="产品发布会短片",
    )
    user_option = UserOption(content_category=ContentCategory.PRODUCT_LAUNCH)

    messages, _ = await build_prompt_for_storyboard_detail_generation_node(
        story_outline=outline,
        analysis_data=None,
        scenes_data=[scene],
        characters_data=[male_char],
        images=[],
        context_scenes={"prev": [], "next": []},
        user_option=user_option,
        detected_language="zh",
        user_input=(
            "这是 Product Launch 人物讲解镜，硬性要求："
            "镜头1必须填写 narration 旁白（中文，约20字，传达「卓越设计是通往未来的第一步」），"
            "同时 narration_gender 必须为 m（男性主讲人 char_presenter_m）。"
            "禁止将讲解内容只写在 dialogue 而留空 narration。"
        ),
        cached_prompt_template=None,
        cached_llm=None,
    )

    from langchain_core.messages import HumanMessage

    messages.append(
        HumanMessage(
            content=(
                "再次强调：shot_number=1 必须输出非空 narration + narration_gender=m；"
                "讲解者是男性品牌主讲人（char_presenter_m）。"
            )
        )
    )

    result = await ainvoke_structured_resilient(
        prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_STORYBOARD_DETAIL_GENERATION],
        kind=StructuredResilienceKind.CREATE_AGENT,
        agent_inputs={"messages": messages},
        log_context={"phase": "integration_detail_narration_gender"},
    )
    structured = result.get("structured_response")
    assert structured is not None
    assert structured.shots, "detail LLM 应返回至少 1 个 shot"
    shot_llm = structured.shots[0]

    print(
        f"[detail LLM] narration={shot_llm.narration!r} dialogue={getattr(shot_llm, 'dialogue', None)!r} "
        f"narration_gender={getattr(shot_llm, 'narration_gender', None)!r}"
    )

    narration_text = (shot_llm.narration or "").strip()
    if not narration_text:
        narration_text = (getattr(shot_llm, "dialogue", None) or "").strip()
    assert narration_text, f"讲解镜应有 narration 或 dialogue 文本: {shot_llm.model_dump()}"

    chars_by_id = {male_char.id: male_char}
    resolved_gender = resolve_narration_gender_for_detail_shot(
        shot_llm,
        scene.character_ids,
        chars_by_id,
        narration_text,
    )
    assert resolved_gender == "m", (
        f"男性讲解镜 narration_gender 应为 m，LLM={getattr(shot_llm, 'narration_gender', None)!r} "
        f"resolved={resolved_gender!r}"
    )
    if (shot_llm.narration or "").strip():
        assert getattr(shot_llm, "narration_gender", None) == "m", (
            "有 narration 时 LLM 应显式输出 narration_gender=m"
        )
