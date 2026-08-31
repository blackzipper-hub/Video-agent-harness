"""
真实 API 烟囱：结构化策略（provider / tool）、LangGraph 内错误恢复 vs ``execute_with_resilience`` 同路由重试。

**LangChain ``create_agent``（``langchain/agents/factory.py``）**

- **ToolStrategy + ``handle_errors=True``**（默认）  
  模型返回结构化 tool call 后，若 **parse/校验失败**，图内会追加一条 **ToolMessage**：
  ``"Error: …\\n Please fix your mistakes."``（见 ``STRUCTURED_OUTPUT_ERROR_TEMPLATE``），然后 **同一 agent.ainvoke 内**再让模型走下一轮。
  多 tool、``MultipleStructuredOutputsError`` 时同理（由 ``_handle_structured_output_error`` 决定）。

- **ProviderStrategy**  
  原生 JSON 若解析失败，会 **直接抛出** ``StructuredOutputValidationError``，**没有** 与 ToolStrategy 相同的「把错误写进 ToolMessage 再训一轮」路径。

**本仓库**

- **wrap_agent_parse_fallback / invoke_agent_with_parse_fallback**（``prompt_utils.py``）：在 **Provider** 解析失败时，尝试从异常里抽 **第一段 JSON** 兜底（仍是一次 ``ainvoke`` 的结果处理，不是 LangGraph 多轮）。
- **execute_with_resilience**：对 **整条** ``invoke_fn(route)``（通常是一次 ``agent.ainvoke``）按异常 **分桶**（超时/429/解析/…），在 **同一 (model, strategy)** 上最多 **same_route_extra_retries** 次 **完整重试**（重新从零跑一遍 ainvoke，**不**等价于 ToolStrategy 图内追加错因 message 的那一种「续聊」）。

运行（需 key）::

    cd Cuti-VideoAgent && pytest tests/llm/test_structured_output_recovery_real.py -v
"""
from __future__ import annotations

import os

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.services.agent.utils.llm_resilience import (
    StructuredResilienceKind,
    ainvoke_structured_resilient,
)


class _TinyStructured(BaseModel):
    """最小结构化字段，供真实模型走 provider 或 tool 终态。"""

    answer: str = Field(min_length=1, description="One short word")


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_ainvoke_structured_resilient_provider_strategy_explicit_entry(dev_env_loaded):
    """条目级 ``structured_output_strategy: provider``：单策略 + 真实 GPT 结构化成功。"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    prompt_entry = {
        "file": "test_structured_recovery",
        "description": "structured strategy smoke",
        "schema": _TinyStructured,
        "model_config": {
            "model": "gpt-4.1-mini",
            "temperature": 0,
            "timeout": 120,
        },
        "resilience": {
            "structured_output_strategy": "provider",
            "model_fallback_chain": [{"model": "gpt-4.1-mini"}],
        },
    }
    agent_inputs = {
        "messages": [
            SystemMessage(
                content="Reply with structured output only. "
                "The answer field must be exactly the word: ok"
            ),
            HumanMessage(content="ping"),
        ]
    }
    out = await ainvoke_structured_resilient(
        prompt_entry=prompt_entry,
        kind=StructuredResilienceKind.CREATE_AGENT,
        agent_inputs=agent_inputs,
        agent_tools=[],
        include_raw=False,
    )
    sr = out.get("structured_response")
    assert sr is not None and isinstance(sr, _TinyStructured)
    assert sr.answer.strip().lower() == "ok"


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_real_ainvoke_structured_resilient_tool_strategy_explicit_entry(dev_env_loaded):
    """条目级 ``structured_output_strategy: tool``：ToolStrategy(handle_errors=True) 真实成功。

    若首轮 tool 参数非法，LangGraph 会在 **本次** ``ainvoke`` 内追加错误 ToolMessage 并再调模型；
    本测不强制触发该分支（行为依赖模型，见模块文档）。
    """
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    prompt_entry = {
        "file": "test_structured_recovery_tool",
        "description": "tool strategy smoke",
        "schema": _TinyStructured,
        "model_config": {
            "model": "gpt-4.1-mini",
            "temperature": 0,
            "timeout": 120,
        },
        "resilience": {
            "structured_output_strategy": "tool",
            "model_fallback_chain": [{"model": "gpt-4.1-mini"}],
        },
    }
    agent_inputs = {
        "messages": [
            SystemMessage(
                content="You must call the structured response tool. "
                "Field answer must be exactly: ok"
            ),
            HumanMessage(content="ping"),
        ]
    }
    out = await ainvoke_structured_resilient(
        prompt_entry=prompt_entry,
        kind=StructuredResilienceKind.CREATE_AGENT,
        agent_inputs=agent_inputs,
        agent_tools=[],
        include_raw=False,
    )
    sr = out.get("structured_response")
    assert sr is not None and isinstance(sr, _TinyStructured)
    assert sr.answer.strip().lower() == "ok"
