"""
真实模型验证：create_agent + ProviderStrategy(ImageGenerationResult) 在 Gemini 2.5+ 与 GPT-4.1-mini 下是否可用。

与 keyframe tool execution **第二轮**（工具已返回、需结构化 ImageGenerationResult）语义对齐，
使用与线上一致的 create_llm、ImageGenerationResult、ProviderStrategy。

Gemini 用例默认 **无 SystemMessage**、human 仅为工具返回 JSON 字符串（或极短多轮前缀），
不依赖 prompt 强调「禁止 markdown / 仅 JSON」；依赖 langchain-google-genai 4.x 将
ProviderStrategy 的 OpenAI 形 response_format 映射为 Gemini 原生 JSON schema。

加载环境见 tests/llm/conftest.py：.env.development → .env.local，并关闭 LangSmith tracing。

运行（需 GOOGLE_API_KEY；GPT 对照需 OPENAI_API_KEY）:
  cd Cuti-VideoAgent && conda run -n cuti-video-local python -m pytest tests/llm/test_provider_strategy_gemini_real.py -v -m integration

默认全量 pytest 可排除: -m "not integration"
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

# 项目根（tests/llm -> parents[2]）
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# 与线上 tool 返回形态一致的一段真实 JSON（缩短 metrics，保留关键字段）
_TOOL_RETURN_JSON = json.dumps(
    {
        "success": True,
        "image_url": "https://cdn.cuti.land/images/26ca3925-f88a-45f1-b306-3efb4e1bfa7c.webp",
        "generated_prompt": "Kenji from image 1, soft smile, stroking Kiko, lake and Mount Fuji blurred background.",
        "provider": "google",
        "model": "gemini-3.1-flash-image-preview",
        "message": "图像生成成功",
        "error_msg": None,
        "raw_error_msg": None,
        "reference_image_urls": [
            "https://cdn.cuti.land/images/bdee8ff3-8796-4508-aafd-e194c54a568c.webp",
        ],
        "seed": 958073846,
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "billing_cost": 0.067,
        "model_switched": None,
        "requested_model": None,
        "actual_model": None,
        "user_facing_message": None,
        "image_tool_metrics": {"total_attempts": 1, "success": True},
        "tool_duration_sec": 70.7,
        "tool_cost": 0.067,
    },
    ensure_ascii=False,
)

# 仅保留工具返回原文，不附加 system、不要求「结构化/禁止 markdown」等（ stress：靠 Gemini 原生 JSON schema + ProviderStrategy）
_HUMAN_BARE_TOOL_JSON_ONLY = _TOOL_RETURN_JSON

_HUMAN_POST_TOOL = f"""Tool generate_image_with_fallback_i2i 已返回如下 JSON（content 原文）：

{_TOOL_RETURN_JSON}

请输出符合模式的最终结构化结果。"""


async def _run_provider_strategy_agent(
    *,
    model_id: str,
    system_prompt: str | None = None,
    human_content: str | None = None,
):
    """与 keyframe 一致：create_agent + ProviderStrategy(ImageGenerationResult)，无 tools（仅测结构化终态）。"""
    from langchain.agents import create_agent
    from langchain.agents.structured_output import ProviderStrategy
    from langchain_core.messages import HumanMessage, SystemMessage

    from prompts.prompt_config import create_llm
    from app.models.image_result import ImageGenerationResult

    llm = create_llm(
        {
            "model": model_id,
            "temperature": 0.2,
            "timeout": 180,
            "max_tokens": 4096,
            "max_output_tokens": 8192,
        }
    )

    agent = await asyncio.to_thread(
        create_agent,
        model=llm,
        tools=[],
        response_format=ProviderStrategy(ImageGenerationResult),
    )

    human = human_content if human_content is not None else _HUMAN_POST_TOOL
    messages = []
    if system_prompt is not None and system_prompt.strip():
        messages.append(SystemMessage(content=system_prompt))
    messages.append(HumanMessage(content=human))
    result = await agent.ainvoke({"messages": messages})
    sr = result.get("structured_response")
    assert sr is not None, "structured_response 为空"
    assert isinstance(sr, ImageGenerationResult), type(sr)
    assert sr.success is True, sr
    assert sr.image_url and "cuti.land" in sr.image_url, sr.image_url
    assert sr.generated_prompt, sr.generated_prompt
    return sr


@pytest.mark.integration
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_gemini_25_flash_provider_strategy_image_result_real(dev_env_loaded):
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY 未配置（.env.development / .env.local）")

    sr = await _run_provider_strategy_agent(
        model_id="gemini-2.5-flash",
        system_prompt=None,
        human_content=_HUMAN_BARE_TOOL_JSON_ONLY,
    )
    assert sr.provider is not None or sr.model is not None


@pytest.mark.integration
@pytest.mark.requires_google
@pytest.mark.asyncio
@pytest.mark.parametrize("run_idx", list(range(5)))
async def test_gemini_25_flash_provider_strategy_bare_human_no_system_five_runs_real(
    dev_env_loaded, run_idx: int
):
    """无 SystemMessage，human 仅为工具 JSON 字符串；连跑 5 次压随机性。"""
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY 未配置（.env.development / .env.local）")

    sr = await _run_provider_strategy_agent(
        model_id="gemini-2.5-flash",
        system_prompt=None,
        human_content=_HUMAN_BARE_TOOL_JSON_ONLY,
    )
    assert sr.success is True, (run_idx, sr)
    assert sr.image_url and "cuti.land" in sr.image_url, (run_idx, sr.image_url)


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_gpt_41_mini_provider_strategy_image_result_real(dev_env_loaded):
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY 未配置（.env.development / .env.local）")

    sr = await _run_provider_strategy_agent(
        model_id="gpt-4.1-mini",
        system_prompt=None,
        human_content=_HUMAN_BARE_TOOL_JSON_ONLY,
    )
    assert sr.success is True


@pytest.mark.integration
@pytest.mark.requires_google
@pytest.mark.asyncio
async def test_gemini_25_flash_provider_strategy_with_tool_message_turn_real(dev_env_loaded):
    """
    更接近线上：messages 含 system + human(task) + ai(tool_calls) + tool(content)，
    再让模型输出 ProviderStrategy（与 LangSmith run 输入形态一致，无真实再调 tool）。
    """
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY 未配置")

    from langchain.agents import create_agent
    from langchain.agents.structured_output import ProviderStrategy
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from prompts.prompt_config import create_llm
    from app.models.image_result import ImageGenerationResult

    llm = create_llm(
        {"model": "gemini-2.5-flash", "temperature": 0.2, "timeout": 180, "max_output_tokens": 8192}
    )
    agent = await asyncio.to_thread(
        create_agent,
        model=llm,
        tools=[],
        response_format=ProviderStrategy(ImageGenerationResult),
    )

    human_task = "shot 27 I2I."
    tool_call_id = "call_integration_test_1"
    messages = [
        HumanMessage(content=human_task),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "generate_image_with_fallback_i2i",
                    "args": {"prompt": "test", "reference_image_urls": ["https://cdn.cuti.land/images/x.webp"]},
                    "id": tool_call_id,
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content=_TOOL_RETURN_JSON, tool_call_id=tool_call_id, name="generate_image_with_fallback_i2i"),
    ]

    result = await agent.ainvoke({"messages": messages})
    sr = result.get("structured_response")
    assert sr is not None
    assert isinstance(sr, ImageGenerationResult)
    assert sr.success is True
    assert sr.image_url
