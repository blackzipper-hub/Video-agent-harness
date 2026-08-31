"""
结构化恢复矩阵测试：可编程首轮错误 + 后续真实模型；摘要消息链供报告落盘。
"""
from __future__ import annotations

import json
import textwrap
import types
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping

import asyncio
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field


class TinyAnswer(BaseModel):
    """与 report 测试共用：单字段字符串。"""

    answer: str = Field(min_length=1, description="Short answer token")


def _tool_name_for_schema(schema: type[BaseModel]) -> str:
    return str(getattr(schema, "__name__", "response_format"))


@dataclass
class ScenarioResult:
    scenario_id: str
    strategy: Literal["provider", "tool"]
    status: Literal["passed", "failed", "skipped"]
    detail: str = ""
    message_summaries: list[str] = field(default_factory=list)
    structured_preview: str = ""
    error: str = ""


def summarize_messages(msgs: list[BaseMessage], *, max_content: int = 220) -> list[str]:
    """可读摘要：类型、tool_calls、content 截断。"""
    out: list[str] = []
    for i, m in enumerate(msgs):
        t = getattr(m, "type", type(m).__name__)
        line_parts: list[str] = [f"[{i}] {t}"]
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            for tc in tcs:
                name = tc.get("name", "?")
                args = tc.get("args", tc.get("arguments", {}))
                if isinstance(args, str):
                    a_preview = args[:max_content]
                else:
                    try:
                        a_preview = json.dumps(args, ensure_ascii=False)[:max_content]
                    except Exception:
                        a_preview = str(args)[:max_content]
                line_parts.append(f"tool_call:{name} args={a_preview}")
        raw = getattr(m, "content", None)
        if raw:
            if isinstance(raw, str):
                c = raw.strip().replace("\n", " ")[:max_content]
            else:
                c = str(raw)[:max_content]
            if c:
                line_parts.append(f"content={c!r}")
        out.append(" | ".join(line_parts))
    return out


def attach_provider_first_response_plain_bad(
    llm: BaseChatModel, *, bad_text: str = "NOT_VALID_JSON_FOR_PROVIDER"
) -> None:
    """首轮模型输出非 JSON；第二轮起真实 API（单次 ``agent.ainvoke`` 内）。"""
    ctr: dict[str, int] = {"n": 0}
    o_a = llm._agenerate
    o_s = llm._generate
    bad_msg = AIMessage(content=bad_text)

    async def _patched_a(
        self: BaseChatModel,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        ctr["n"] += 1
        if ctr["n"] == 1:
            return ChatResult(generations=[ChatGeneration(message=bad_msg)])
        return await o_a(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _patched_s(
        self: BaseChatModel,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        ctr["n"] += 1
        if ctr["n"] == 1:
            return ChatResult(generations=[ChatGeneration(message=bad_msg)])
        return o_s(messages, stop=stop, run_manager=run_manager, **kwargs)

    llm._agenerate = types.MethodType(_patched_a, llm)
    llm._generate = types.MethodType(_patched_s, llm)


def attach_flaky_tool_first_turn(
    llm: BaseChatModel,
    *,
    schema: type[BaseModel],
    bad_args: dict[str, Any],
) -> None:
    """首轮 ``_agenerate`` 注入非法 structured tool 调用；之后走真实模型（同一次 ``agent.ainvoke`` 内第二趟）。"""
    name = _tool_name_for_schema(schema)
    first_msg = AIMessage(
        content="",
        tool_calls=[{"name": name, "id": "flaky_tc_1", "args": dict(bad_args)}],
    )
    ctr: dict[str, int] = {"n": 0}
    orig_agenerate = llm._agenerate

    async def _patched_agenerate(
        self: BaseChatModel,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        ctr["n"] += 1
        if ctr["n"] == 1:
            return ChatResult(generations=[ChatGeneration(message=first_msg)])
        return await orig_agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    llm._agenerate = types.MethodType(_patched_agenerate, llm)


def build_inner_llm(factory: Callable[[], BaseChatModel]) -> BaseChatModel:
    return factory()


async def run_tool_strategy_ingraph_recovery(
    *,
    inner_factory: Callable[[], BaseChatModel],
    schema: type[BaseModel] = TinyAnswer,
    system_text: str = "You MUST fix structured output when the tool reports an error. Final answer must be exactly: yes",
    human_text: str = "ping",
    bad_args: dict[str, Any] | None = None,
) -> tuple[Any, list[BaseMessage]]:
    inner = build_inner_llm(inner_factory)
    bad_args = bad_args if bad_args is not None else {"answer": 999999}
    attach_flaky_tool_first_turn(inner, schema=schema, bad_args=bad_args)
    llm = inner
    agent = await asyncio.to_thread(
        create_agent,
        llm,
        tools=[],
        response_format=ToolStrategy(schema, handle_errors=True),
    )
    from langchain_core.runnables import RunnableConfig

    out = await agent.ainvoke(
        {"messages": [SystemMessage(content=system_text), HumanMessage(content=human_text)]},
        config=RunnableConfig(recursion_limit=12),
    )
    msgs = list(out.get("messages") or [])
    return out, msgs


async def run_provider_strategy_resilience_retry(
    *,
    inner_factory: Callable[[], BaseChatModel],
    schema: type[BaseModel] = TinyAnswer,
    system_text: str = "Reply with JSON per schema. Field answer must be exactly: ok",
    human_text: str = "ping",
) -> tuple[Any, list[str]]:
    """第一次 ``ainvoke`` 因首轮非法文本失败；同路由第二次 ``ainvoke`` 使用纯真实 LLM。"""
    from app.services.agent.utils.llm_resilience import (
        ResilienceContext,
        execute_with_resilience,
    )

    summaries: list[str] = []
    outer_attempt = [0]

    async def invoke_fn(route: tuple[str, Any], mc: Mapping[str, Any]) -> Any:
        _ = mc
        outer_attempt[0] += 1
        inner = build_inner_llm(inner_factory)
        if outer_attempt[0] == 1:
            attach_provider_first_response_plain_bad(inner)
        llm = inner
        agent = await asyncio.to_thread(
            create_agent,
            llm,
            tools=[],
            response_format=ProviderStrategy(schema),
        )
        try:
            out = await agent.ainvoke(
                {
                    "messages": [
                        SystemMessage(content=system_text),
                        HumanMessage(content=human_text),
                    ]
                }
            )
        except Exception as e:
            summaries.append(
                f"--- invoke_pass={outer_attempt[0]} FAILED: {type(e).__name__}: {e!s}"
            )
            raise
        msgs = list(out.get("messages") or [])
        summaries.extend(
            [f"--- invoke_pass={outer_attempt[0]} ---", *summarize_messages(msgs)]
        )
        return out

    ctx = ResilienceContext(same_route_extra_retries=1)
    out = await execute_with_resilience(
        invoke_fn,
        routes=[("gpt", "provider")],
        route_model_configs=[{"model": "gpt-4.1-mini"}],
        context=ctx,
    )
    final = list(out.get("messages") or [])
    summaries.extend(["--- final merged trace ---", *summarize_messages(final)])
    return out, summaries


def render_report_md(results: list[ScenarioResult], *, title: str) -> str:
    lines = [f"# {title}", "", "| Case | Strategy | Status | Notes |", "|------|----------|--------|-------|"]
    for r in results:
        note = (r.detail or "")[:200]
        if r.error:
            note = f"ERR: {r.error[:160]}"
        lines.append(f"| {r.scenario_id} | {r.strategy} | {r.status} | {note} |")
    lines.append("")
    for r in results:
        lines.append(f"## {r.scenario_id} ({r.strategy})")
        lines.append("")
        lines.append(f"- **status**: {r.status}")
        if r.structured_preview:
            lines.append(f"- **structured**: `{r.structured_preview}`")
        if r.detail:
            lines.append(textwrap.dedent(f"\n{r.detail}\n"))
        if r.message_summaries:
            lines.append("**Messages (chronological):**")
            lines.append("")
            lines.append("```")
            lines.extend(r.message_summaries)
            lines.append("```")
        lines.append("")
    return "\n".join(lines)
