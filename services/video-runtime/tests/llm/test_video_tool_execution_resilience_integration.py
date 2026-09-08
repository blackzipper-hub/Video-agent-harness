"""
视频 tool_execution：``ainvoke_structured_resilient`` 集成测试。

1) **主推**：全进程仅 **第一次** ``ChatOpenAI.ainvoke`` mock 为 ``BadRequestError``，之后一律走 **真实** OpenAI；验证 ``bad_request`` 同路由重试后 GPT 真链路成功（无需 GOOGLE）。
2) **备选**：GPT 侧 **每次** ``ainvoke`` 均 400，耗尽同路由重试后 **真实** ``gemini-3-flash-preview``（需 GOOGLE_API_KEY）。

运行::

    conda activate cuti-video-local
    cd Cuti-VideoAgent && python -m pytest tests/llm/test_video_tool_execution_resilience_integration.py -v -m integration
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_LL_DIR = Path(__file__).resolve().parent


def _bad_request_exc():
    try:
        from openai import BadRequestError
    except ImportError:
        pytest.skip("openai not installed")
    import httpx

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(400, request=req, json={"error": {"message": "invalid JSON body"}})
    return BadRequestError("bad", response=resp, body=None)


def _load_matrix_helpers():
    import importlib.util

    p = _LL_DIR / "test_structured_output_tool_execution_matrix_real.py"
    spec = importlib.util.spec_from_file_location("_m", p)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


from prompts.prompt_config import PROMPTS_CONFIG, PromptName  # noqa: E402
from app.models.tool_enums import DefaultValues  # noqa: E402
from app.services.agent.utils.llm_resilience import (  # noqa: E402
    StructuredResilienceKind,
    ainvoke_structured_resilient,
)
from app.tools.context_schemas import VideoGenerationContext  # noqa: E402


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_video_tool_execution_first_gpt_ainvoke_mock_400_then_real_openai_retry(dev_env_loaded):
    """仅第一次 ``ChatOpenAI.ainvoke`` 抛 400；同路由第二次为真实 OpenAI，应成功（不调 Gemini）。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    mod = _load_matrix_helpers()
    entry = PROMPTS_CONFIG[PromptName.VIDEO_CONSISTENCY_CHECK]

    system_text = await mod._system_text_from_tool_template(
        "video/video_generation/video_video_generation_tool_execution",
        mod._video_template_data,
    )
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=mod._VIDEO_TOOL_JSON),
    ]
    context = VideoGenerationContext(
        aspect_ratio=DefaultValues.VIDEO_ASPECT_RATIO,
        resolution=DefaultValues.VIDEO_RESOLUTION,
        start_image_url="https://example.com/start.jpg",
        audio_url="https://example.com/audio.mp3",
        duration=5,
        language="zh",
    )
    inputs = {"messages": messages}

    state = {"gpt_ainvokes": 0}
    _orig_ainvoke = ChatOpenAI.ainvoke

    async def _patched_first_fail_then_real(self, *args, **kwargs):
        state["gpt_ainvokes"] += 1
        if state["gpt_ainvokes"] == 1:
            raise _bad_request_exc()
        return await _orig_ainvoke(self, *args, **kwargs)

    with patch.object(ChatOpenAI, "ainvoke", _patched_first_fail_then_real):
        result = await ainvoke_structured_resilient(
            kind=StructuredResilienceKind.CREATE_AGENT,
            prompt_entry=entry,
            agent_inputs=inputs,
            agent_tools=[],
            context_schema=VideoGenerationContext,
            agent_invoke_context=context,
            wrap_agent_parse_fallback=True,
            log_context={"phase": "video_tool_execution_first_mock_then_real_openai"},
        )

    assert state["gpt_ainvokes"] >= 2, (
        "至少 1 次 mock 失败 + 1 次真实调用；若图内多次 forward 会更大 — " + str(state)
    )
    sr = result.get("structured_response")
    assert sr is not None, result
    assert sr.success is True, sr
    assert sr.video_url and "cuti.land" in sr.video_url, sr


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_real_video_tool_execution_gpt_always_bad_request_then_gemini3(dev_env_loaded):
    """GPT 每条 forward 均 400，耗尽同路由重试后 fallback **真实** gemini-3-flash-preview。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY")

    mod = _load_matrix_helpers()
    entry = PROMPTS_CONFIG[PromptName.VIDEO_CONSISTENCY_CHECK]

    system_text = await mod._system_text_from_tool_template(
        "video/video_generation/video_video_generation_tool_execution",
        mod._video_template_data,
    )
    messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=mod._VIDEO_TOOL_JSON),
    ]
    context = VideoGenerationContext(
        aspect_ratio=DefaultValues.VIDEO_ASPECT_RATIO,
        resolution=DefaultValues.VIDEO_RESOLUTION,
        start_image_url="https://example.com/start.jpg",
        audio_url="https://example.com/audio.mp3",
        duration=5,
        language="zh",
    )
    inputs = {"messages": messages}

    state = {"gpt_ainvokes": 0}

    async def _patched_openai_always_bad(self, *args, **kwargs):
        state["gpt_ainvokes"] += 1
        raise _bad_request_exc()

    with patch.object(ChatOpenAI, "ainvoke", _patched_openai_always_bad):
        result = await ainvoke_structured_resilient(
            kind=StructuredResilienceKind.CREATE_AGENT,
            prompt_entry=entry,
            agent_inputs=inputs,
            agent_tools=[],
            context_schema=VideoGenerationContext,
            agent_invoke_context=context,
            wrap_agent_parse_fallback=True,
            log_context={"phase": "video_tool_execution_always_bad_then_gemini"},
        )

    assert state["gpt_ainvokes"] >= 2, state
    sr = result.get("structured_response")
    assert sr is not None, result
    assert sr.success is True, sr
    assert sr.video_url and "cuti.land" in sr.video_url, sr
