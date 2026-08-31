"""
真实 HTTP：Gemini **2.5** 在 **已绑定业务/占位 tools**（function calling）时，再叠 **ProviderStrategy**（response MIME JSON）
会 400；**Gemini 3 预览** 据 `ai.google.dev`「Structured outputs with tools」可与 tools 同请求（仍以真实调用为准）。

与线上一致：``create_agent(..., tools=video_tools, response_format=ProviderStrategy(...))`` 在 fallback 到
2.5 时会触发 Google API 拒绝；改用 ``ToolStrategy`` 或换 **3 预览** 模型可能避免。

``tests/llm/test_structured_output_tool_execution_matrix_real.py`` 里矩阵用 ``tools=[]``，不会触发本冲突。

运行（需 GOOGLE_API_KEY，见 tests/llm/conftest.py）::

    conda activate cuti-video-local
    cd Cuti-VideoAgent && python -m pytest tests/llm/test_gemini_bound_tools_structured_output_real.py -v -m integration
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompts.prompt_config import create_llm  # noqa: E402
from app.models.image_result import ImageGenerationResult  # noqa: E402


_TOOL_RETURN_JSON = json.dumps(
    {
        "success": True,
        "image_url": "https://cdn.cuti.land/images/26ca3925-f88a-45f1-b306-3efb4e1bfa7c.webp",
        "generated_prompt": "Kenji from image 1, soft smile, lake and Mount Fuji background.",
        "provider": "google",
        "model": "gemini-3.1-flash-image-preview",
        "message": "图像生成成功",
        "error_msg": None,
        "raw_error_msg": None,
        "reference_image_urls": ["https://cdn.cuti.land/images/bdee8ff3-8796-4508-aafd-e194c54a568c.webp"],
        "seed": 958073846,
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "billing_cost": 0.067,
        "image_tool_metrics": {"total_attempts": 1, "success": True},
        "tool_duration_sec": 70.7,
        "tool_cost": 0.067,
    },
    ensure_ascii=False,
)


@tool
def _integration_placeholder_tool(query: str) -> str:
    """占位 tool：仅用于让 bind_tools 非空，模拟线上 video_tools / keyframe_tools。"""
    return f"noop:{query[:32]}"


def _provider_incompatibility_message(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".lower()


@pytest.mark.integration
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_gemini_bound_tools_provider_strategy_http_rejected(dev_env_loaded):
    """真实请求：Gemini + 非空 tools + ProviderStrategy → API 拒绝（与线上 400 同源）。"""
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY")

    llm = create_llm(
        {
            "model": "gemini-2.5-flash",
            "temperature": 0.2,
            "timeout": 180,
            "max_output_tokens": 8192,
        }
    )
    agent = await asyncio.to_thread(
        create_agent,
        model=llm,
        tools=[_integration_placeholder_tool],
        response_format=ProviderStrategy(ImageGenerationResult),
    )
    messages = [HumanMessage(content=_TOOL_RETURN_JSON)]

    with pytest.raises(Exception) as excinfo:
        await agent.ainvoke({"messages": messages})

    msg = _provider_incompatibility_message(excinfo.value)
    assert (
        "application/json" in msg
        or "mime type" in msg
        or "function calling" in msg
        or "invalidargument" in msg.replace("_", "")
        or "400" in msg
    ), f"unexpected error (expected Gemini provider+tools incompatibility): {excinfo.value!r}"


@pytest.mark.integration
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_gemini_bound_tools_tool_strategy_http_success(dev_env_loaded):
    """真实请求：相同 bind_tools + ToolStrategy → 应返回 structured_response（修复后路线）。"""
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY")

    llm = create_llm(
        {
            "model": "gemini-2.5-flash",
            "temperature": 0.2,
            "timeout": 180,
            "max_output_tokens": 8192,
        }
    )
    agent = await asyncio.to_thread(
        create_agent,
        model=llm,
        tools=[_integration_placeholder_tool],
        response_format=ToolStrategy(ImageGenerationResult, handle_errors=True),
    )
    messages = [
        HumanMessage(
            content=(
                "The image tool already returned the following JSON. "
                "Emit the final structured result only via the structured response tool.\n\n"
                + _TOOL_RETURN_JSON
            )
        ),
    ]
    result = await agent.ainvoke({"messages": messages})
    sr = result.get("structured_response")
    assert sr is not None, result
    assert isinstance(sr, ImageGenerationResult), type(sr)
    assert sr.success is True, sr
    assert sr.image_url and "cuti.land" in sr.image_url, sr


@pytest.mark.integration
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_gemini3_flash_preview_bound_tools_provider_strategy_http_success(dev_env_loaded):
    """
    Gemini 3 预览：文档称可 ``tools`` + ``response_mime_type: application/json`` 同请求。
    使用与 2.5 拒绝用例相同的 ``create_agent`` + 非空 tools + ProviderStrategy；若模型未开放或
    LangChain 未对齐请求体，可能仍失败（见跳过 / 断言信息）。
    """
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY")

    model_id = "gemini-3-flash-preview"
    llm = create_llm(
        {
            "model": model_id,
            "temperature": 0.2,
            "timeout": 240,
            "max_output_tokens": 8192,
        }
    )
    agent = await asyncio.to_thread(
        create_agent,
        model=llm,
        tools=[_integration_placeholder_tool],
        response_format=ProviderStrategy(ImageGenerationResult),
    )
    messages = [
        HumanMessage(
            content=(
                "The image tool returned the following JSON. Reply with structured output matching ImageGenerationResult.\n\n"
                + _TOOL_RETURN_JSON
            )
        ),
    ]
    try:
        result = await agent.ainvoke({"messages": messages})
    except Exception as e:
        msg = _provider_incompatibility_message(e)
        if "404" in msg or "not found" in msg or "was not found" in msg:
            pytest.skip(f"model unavailable or rename: {model_id!r} — {e!r}")
        if (
            "application/json" in msg
            or "mime type" in msg
            or "function calling" in msg
            or "invalidargument" in msg.replace("_", "")
        ):
            pytest.fail(
                f"Gemini 3 still rejected provider+tools (SDK/LC may not emit new API yet): {e!r}"
            )
        raise

    sr = result.get("structured_response")
    assert sr is not None, result
    assert isinstance(sr, ImageGenerationResult), type(sr)
    assert sr.success is True, sr
    assert sr.image_url and "cuti.land" in sr.image_url, sr
