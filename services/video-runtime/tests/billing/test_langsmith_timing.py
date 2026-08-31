"""
验证 LangSmith read_run 的聚合延迟
===================================
关键问题：read_run(run_id).total_cost 多久才能反映 resume 的成本？

运行:
    conda run -n cuti-video-local pytest tests/billing/test_langsmith_timing.py -v -s
"""
import asyncio
import logging
import uuid
from typing import TypedDict, Annotated
import operator

import pytest

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_read_run_aggregation_timing():
    """
    LangGraph interrupt → resume 后，多次 read_run 看 total_tokens 是否会逐渐更新。
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, AIMessage
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt, Command
    from langsmith import Client

    class State(TypedDict):
        messages: Annotated[list, operator.add]
        step: str

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)

    async def step1(state: State) -> State:
        result = await llm.ainvoke(state["messages"])
        user_response = interrupt("confirm?")
        return {"messages": [result], "step": f"done_{user_response}"}

    async def step2(state: State) -> State:
        result = await llm.ainvoke([HumanMessage(content="Say 'step2 done'.")])
        return {"messages": [result], "step": "step2_done"}

    builder = StateGraph(State)
    builder.add_node("s1", step1)
    builder.add_node("s2", step2)
    builder.add_edge(START, "s1")
    builder.add_edge("s1", "s2")
    builder.add_edge("s2", END)
    graph = builder.compile(checkpointer=MemorySaver())

    run_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}, "run_id": run_id}

    print(f"\nrun_id: {run_id}")

    # Phase 1 (will interrupt)
    async for _ in graph.astream(
        {"messages": [HumanMessage(content="What is 2+2?")], "step": ""},
        config=config, stream_mode="updates",
    ):
        pass
    print("Phase 1 (interrupt) done")

    # Phase 2 (resume)
    await asyncio.sleep(1)
    async for _ in graph.astream(
        Command(resume="yes"),
        config=config, stream_mode="updates",
    ):
        pass
    print("Phase 2 (resume) done")

    # 多次查询 read_run，看聚合延迟
    client = Client()
    delays = [3, 5, 10, 15, 20, 30]

    print(f"\n{'delay':>6s} | {'total_tokens':>12s} | {'total_cost':>12s} | {'child_runs':>10s} | note")
    print("-" * 70)

    cumulative_wait = 0
    for delay in delays:
        await asyncio.sleep(delay - cumulative_wait if delay > cumulative_wait else delay)
        cumulative_wait = delay

        try:
            run = client.read_run(run_id=run_id)
            # 也查 child runs 数量
            children = list(client.list_runs(parent_run_id=run_id))
            child_count = len(children)
            child_total = sum(c.total_tokens or 0 for c in children)

            note = ""
            if run.total_tokens and run.total_tokens > 30:
                note = "✅ 已包含 resume"
            elif run.total_tokens:
                note = "⚠️  只有 Phase 1"

            print(f"{delay:>5d}s | {run.total_tokens or 0:>12d} | ${float(run.total_cost or 0):>10.7f} | {child_count:>10d} | {note}")
            print(f"       | child_total_tokens={child_total}")

        except Exception as e:
            print(f"{delay:>5d}s | ERROR: {e}")

    # 最后用 trace_id 查一次
    try:
        trace_runs = list(client.list_runs(trace_id=run_id))
        print(f"\ntrace_id 查询 ({len(trace_runs)} runs):")
        for tr in trace_runs:
            is_root = "ROOT" if tr.parent_run_id is None else "child"
            print(f"  {tr.id}: name={tr.name}, tokens={tr.total_tokens}, cost={tr.total_cost}, {is_root}")
    except Exception as e:
        print(f"\ntrace_id 查询 failed: {e}")
