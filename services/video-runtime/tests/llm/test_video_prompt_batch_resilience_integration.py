"""
视频 Prompt 两步（批量生成 + 评估修正）：Gemini 侧 ``ChatGoogleGenerativeAI.ainvoke`` 全部 mock 失败，
验证 ``execute_with_resilience`` 耗尽 Gemini 同路由重试后走真实 ``gpt-4.1-mini`` 成功。

运行::

    cd Cuti-VideoAgent && python -m pytest tests/llm/test_video_prompt_batch_resilience_integration.py -v -m integration
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None  # type: ignore[misc, assignment]

from app.models.video_state import DetailedShot, KeyframeVersion  # noqa: E402
from app.schemas.video_llm import (  # noqa: E402
    BatchPromptEvaluationResult,
    VideoGenerationPrompt,
)
from app.services.agent.video.video_generation_service import (  # noqa: E402
    evaluate_and_fix_batch_prompts,
    generate_batch_video_prompts,
)


def _sample_image_url() -> str:
    """稳定可访问的公网小图，供多模态占位符替换。"""
    return (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/"
        "PNG_transparency_demonstration_1.png/240px-PNG_transparency_demonstration_1.png"
    )


def _minimal_shot_and_keyframe() -> tuple[KeyframeVersion, DetailedShot]:
    url = _sample_image_url()
    shot = DetailedShot(
        shot_number=1,
        duration=5.0,
        shot_type="中景",
        scene_description="人物走进室内，光线柔和。",
        camera_movement="缓慢推轨",
        lighting="暖色侧光",
        character_ids=["c1"],
        dialogue="你好。",
        sound_effects="脚步声",
    )
    kf = KeyframeVersion(
        shot_number=1,
        t2i_prompt="test keyframe prompt",
        provider="test",
        keyframe_url=url,
        frame_index=0,
    )
    return kf, shot


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_video_batch_prompt_generation_gemini_ainvoke_fails_then_real_openai(
    dev_env_loaded,
):
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")
    if ChatGoogleGenerativeAI is None:
        pytest.skip("langchain_google_genai not installed")

    kf, shot = _minimal_shot_and_keyframe()
    user_input = "integration test：单镜头短片"

    gemini_calls = {"n": 0}
    gpt_calls = {"n": 0}
    _orig_openai_ainvoke = ChatOpenAI.ainvoke

    async def _gemini_fail(self, *args, **kwargs):
        gemini_calls["n"] += 1
        raise TimeoutError("mock gemini failure for video batch prompt integration test")

    async def _gpt_count(self, *args, **kwargs):
        gpt_calls["n"] += 1
        return await _orig_openai_ainvoke(self, *args, **kwargs)

    with (
        patch.object(ChatGoogleGenerativeAI, "ainvoke", _gemini_fail),
        patch.object(ChatOpenAI, "ainvoke", _gpt_count),
    ):
        _msgs, prompts = await generate_batch_video_prompts(
            keyframes_batch=[kf],
            shots_batch=[shot],
            user_input=user_input,
            llm=None,
            detected_language="zh",
            log_context={"phase": "integration_video_batch_prompt_resilience"},
        )

    assert gemini_calls["n"] >= 1, "应至少调用一次 Gemini ainvoke（mock 失败）"
    assert gpt_calls["n"] >= 1, "fallback 后应至少一次真实 OpenAI ainvoke"
    assert len(prompts) >= 1
    assert prompts[0].shot_number == 1
    assert prompts[0].i2v_prompt and len(prompts[0].i2v_prompt.strip()) > 10


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_video_eval_fix_gemini_ainvoke_fails_then_real_openai(dev_env_loaded):
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")
    if ChatGoogleGenerativeAI is None:
        pytest.skip("langchain_google_genai not installed")

    kf, _shot = _minimal_shot_and_keyframe()
    prompts_in = [
        VideoGenerationPrompt(
            shot_number=1,
            i2v_prompt=(
                "镜头从中景缓慢推近，人物走入室内，暖光，对话氛围。"
                "保持与首帧一致的服装与发型。"
            ),
            start_image_url=kf.keyframe_url,
            end_image_url=None,
            needs_end_image=False,
        )
    ]

    gemini_calls = {"n": 0}
    gpt_calls = {"n": 0}
    _orig_openai_ainvoke = ChatOpenAI.ainvoke

    async def _gemini_fail(self, *args, **kwargs):
        gemini_calls["n"] += 1
        raise TimeoutError("mock gemini failure for video eval_fix integration test")

    async def _gpt_count(self, *args, **kwargs):
        gpt_calls["n"] += 1
        return await _orig_openai_ainvoke(self, *args, **kwargs)

    with (
        patch.object(ChatGoogleGenerativeAI, "ainvoke", _gemini_fail),
        patch.object(ChatOpenAI, "ainvoke", _gpt_count),
    ):
        _msgs, evaluated = await evaluate_and_fix_batch_prompts(
            prompts=prompts_in,
            keyframes_batch=[kf],
            llm=None,
            schema=BatchPromptEvaluationResult,
            user_input="integration test eval-fix",
            detected_language="zh",
        )

    assert gemini_calls["n"] >= 1
    assert gpt_calls["n"] >= 1
    assert len(evaluated) == 1
    assert evaluated[0].shot_number == 1
    assert evaluated[0].i2v_prompt and len(evaluated[0].i2v_prompt.strip()) > 5
