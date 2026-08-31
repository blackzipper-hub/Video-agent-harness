"""
矩阵：Tool / Provider × 图内恢复、同路由重试、乐观路径；首轮错误由 harness 注入，后续为真实 API。

运行并刷新报告（需 OPENAI_API_KEY）::

    cd Cuti-VideoAgent && pytest tests/llm/test_structured_recovery_matrix_report.py -v

报告输出：``tests/llm/reports/structured_recovery_matrix_report.md``
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain_core.messages import HumanMessage, SystemMessage

from prompts.prompt_loader import create_llm_from_model_config
from tests.llm.structured_recovery_harness import (
    ScenarioResult,
    TinyAnswer,
    render_report_md,
    run_provider_strategy_resilience_retry,
    run_tool_strategy_ingraph_recovery,
    summarize_messages,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REPORT_FILE = _PROJECT_ROOT / "tests" / "llm" / "reports" / "structured_recovery_matrix_report.md"


def _gpt_mini_factory():
    return create_llm_from_model_config(
        {"model": "gpt-4.1-mini", "temperature": 0, "timeout": 180}
    )


async def _run_all_scenarios() -> list[ScenarioResult]:
    results: list[ScenarioResult] = []

    # ---- 1) ToolStrategy：图内 ToolMessage 报错后再调真实模型 ----
    try:
        out, msgs = await run_tool_strategy_ingraph_recovery(
            inner_factory=_gpt_mini_factory,
            schema=TinyAnswer,
            system_text=(
                "You use the structured output tool. "
                "If you see a tool error, fix and call the tool again. "
                "Final field answer must be exactly: ok"
            ),
            human_text="go",
            bad_args={"answer": 42},
        )
        sr = out.get("structured_response")
        prev_tool_errors = [
            m for m in msgs if getattr(m, "type", None) == "tool" and "Error:" in (getattr(m, "content", "") or "")
        ]
        results.append(
            ScenarioResult(
                scenario_id="tool_ingraph_invalid_args_then_llm_fixes",
                strategy="tool",
                status="passed",
                detail=(
                    f"图内纠错：共 {len(msgs)} 条消息；"
                    f"含 Error 前缀的 ToolMessage 条数={len(prev_tool_errors)}（LangChain 注入的请修复提示）。"
                ),
                message_summaries=summarize_messages(msgs),
                structured_preview=repr(sr),
            )
        )
    except Exception as e:
        results.append(
            ScenarioResult(
                scenario_id="tool_ingraph_invalid_args_then_llm_fixes",
                strategy="tool",
                status="failed",
                error=str(e),
            )
        )

    # ---- 2) ToolStrategy：乐观路径（无注入）----
    try:
        inner = _gpt_mini_factory()
        agent = await asyncio.to_thread(
            create_agent,
            inner,
            tools=[],
            response_format=ToolStrategy(TinyAnswer, handle_errors=True),
        )
        out = await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(
                        content="Call structured tool once. Field answer must be exactly: ok"
                    ),
                    HumanMessage(content="ping"),
                ]
            }
        )
        msgs = list(out.get("messages") or [])
        sr = out.get("structured_response")
        results.append(
            ScenarioResult(
                scenario_id="tool_happy_path_real_only",
                strategy="tool",
                status="passed",
                detail="单次或图内短路径成功（无人工坏 tool）",
                message_summaries=summarize_messages(msgs),
                structured_preview=repr(sr),
            )
        )
    except Exception as e:
        results.append(
            ScenarioResult(
                scenario_id="tool_happy_path_real_only",
                strategy="tool",
                status="failed",
                error=str(e),
            )
        )

    # ---- 3) ProviderStrategy：无图内纠错，依赖 execute_with_resilience 整次重试 ----
    try:
        out, trace_lines = await run_provider_strategy_resilience_retry(
            inner_factory=_gpt_mini_factory,
            schema=TinyAnswer,
            system_text="Native JSON output per schema. Field answer must be exactly: ok",
            human_text="ping",
        )
        sr = out.get("structured_response")
        results.append(
            ScenarioResult(
                scenario_id="provider_first_bad_json_then_execute_with_resilience_retry",
                strategy="provider",
                status="passed",
                detail=(
                    "首轮 patch 注入非 JSON → StructuredOutputValidationError；"
                    "execute_with_resilience 同路由第二次调用 invoke_fn（新 LLM、无 patch）成功。"
                ),
                message_summaries=trace_lines,
                structured_preview=repr(sr),
            )
        )
    except Exception as e:
        results.append(
            ScenarioResult(
                scenario_id="provider_first_bad_json_then_execute_with_resilience_retry",
                strategy="provider",
                status="failed",
                error=str(e),
            )
        )

    # ---- 4) ProviderStrategy：乐观路径 ----
    try:
        inner = _gpt_mini_factory()
        agent = await asyncio.to_thread(
            create_agent,
            inner,
            tools=[],
            response_format=ProviderStrategy(TinyAnswer),
        )
        out = await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(
                        content="JSON per schema. Field answer must be exactly: ok"
                    ),
                    HumanMessage(content="ping"),
                ]
            }
        )
        msgs = list(out.get("messages") or [])
        sr = out.get("structured_response")
        results.append(
            ScenarioResult(
                scenario_id="provider_happy_path_real_only",
                strategy="provider",
                status="passed",
                detail="Provider 原生结构化一次成功",
                message_summaries=summarize_messages(msgs),
                structured_preview=repr(sr),
            )
        )
    except Exception as e:
        results.append(
            ScenarioResult(
                scenario_id="provider_happy_path_real_only",
                strategy="provider",
                status="failed",
                error=str(e),
            )
        )

    # ---- 5) execute_with_resilience：瞬态错误同路由重试（整次 ainvoke 重跑）----
    try:
        from app.services.agent.utils.llm_resilience import (
            ResilienceContext,
            execute_with_resilience,
        )

        n = {"c": 0}

        async def invoke_fn(route, mc):
            _ = mc
            n["c"] += 1
            if n["c"] == 1:
                raise TimeoutError("simulated transient")
            inner = _gpt_mini_factory()
            agent = await asyncio.to_thread(
                create_agent,
                inner,
                tools=[],
                response_format=ProviderStrategy(TinyAnswer),
            )
            return await agent.ainvoke(
                {
                    "messages": [
                        SystemMessage(content="answer field exactly: ok"),
                        HumanMessage(content="p"),
                    ]
                }
            )

        ctx = ResilienceContext(same_route_extra_retries=1)
        out = await execute_with_resilience(
            invoke_fn,
            routes=[("gpt-4.1-mini", "provider")],
            route_model_configs=[{"model": "gpt-4.1-mini"}],
            context=ctx,
        )
        sr = out.get("structured_response")
        results.append(
            ScenarioResult(
                scenario_id="execute_with_resilience_transient_timeout_same_route_retry",
                strategy="provider",
                status="passed",
                detail=f"invoke_fn 调用次数={n['c']}（1 次模拟超时 + 1 次真实成功）",
                message_summaries=[f"invoke_fn_calls={n['c']}", repr(sr)],
                structured_preview=repr(sr),
            )
        )
    except Exception as e:
        results.append(
            ScenarioResult(
                scenario_id="execute_with_resilience_transient_timeout_same_route_retry",
                strategy="provider",
                status="failed",
                error=str(e),
            )
        )

    return results


@pytest.mark.integration
@pytest.mark.requires_openai
@pytest.mark.asyncio
async def test_structured_recovery_matrix_and_write_report(dev_env_loaded):
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY")

    results = await _run_all_scenarios()
    body = render_report_md(
        results,
        title="Structured recovery matrix (Tool vs Provider) — 真实 API 摘录",
    )
    header = (
        "<!-- AUTO-GENERATED by tests/llm/test_structured_recovery_matrix_report.py -->\n\n"
        "刷新：`cd Cuti-VideoAgent && PYTHONPATH=. pytest tests/llm/test_structured_recovery_matrix_report.py -v`"
        "（需 `OPENAI_API_KEY`）。\n\n"
    )
    _REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REPORT_FILE.write_text(header + body, encoding="utf-8")

    failed = [r for r in results if r.status != "passed"]
    assert not failed, f"scenarios failed: {[r.scenario_id for r in failed]}"
