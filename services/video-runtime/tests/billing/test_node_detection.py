"""
验证 LangGraph 中 on_chain_end 能否检测 node 结束
=================================================
问题：如何知道一个 LangGraph node 执行完了？on_chain_end 是否只在 node 结束时触发？

运行:
    conda run -n cuti-video-local pytest tests/billing/test_node_detection.py -v -s
"""
import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, TypedDict, Annotated
import operator

import pytest

logger = logging.getLogger(__name__)


class ChainEventHandler:
    """记录所有 on_chain_start/end 和 on_llm_end 事件，分析 node 边界"""

    raise_error = False
    run_inline = False
    ignore_llm = False
    ignore_chain = False
    ignore_agent = False
    ignore_retriever = True
    ignore_retry = True
    ignore_chat_model = False
    ignore_custom_event = True

    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    async def on_chain_start(self, serialized, inputs, *, run_id, **kwargs):
        self.events.append({
            "type": "chain_start",
            "run_id": str(run_id),
            "name": kwargs.get("name", serialized.get("name", "?") if serialized else "?"),
            "parent_run_id": str(kwargs.get("parent_run_id", "")) if kwargs.get("parent_run_id") else None,
            "tags": kwargs.get("tags", []),
        })

    async def on_chain_end(self, outputs, *, run_id, **kwargs):
        self.events.append({
            "type": "chain_end",
            "run_id": str(run_id),
            "parent_run_id": str(kwargs.get("parent_run_id", "")) if kwargs.get("parent_run_id") else None,
            "tags": kwargs.get("tags", []),
        })

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.events.append({
            "type": "chat_model_start",
            "run_id": str(run_id),
            "parent_run_id": str(kwargs.get("parent_run_id", "")) if kwargs.get("parent_run_id") else None,
        })

    async def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        pass

    async def on_llm_new_token(self, token, *, run_id, **kwargs):
        pass

    async def on_llm_end(self, response, *, run_id, **kwargs):
        self.events.append({
            "type": "llm_end",
            "run_id": str(run_id),
            "parent_run_id": str(kwargs.get("parent_run_id", "")) if kwargs.get("parent_run_id") else None,
        })

    async def on_llm_error(self, error, *, run_id, **kwargs):
        pass

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        self.events.append({
            "type": "tool_start",
            "run_id": str(run_id),
            "name": kwargs.get("name", "?"),
            "parent_run_id": str(kwargs.get("parent_run_id", "")) if kwargs.get("parent_run_id") else None,
        })

    async def on_tool_end(self, output, *, run_id, **kwargs):
        self.events.append({
            "type": "tool_end",
            "run_id": str(run_id),
            "name": kwargs.get("name", getattr(output, "name", "?")),
            "parent_run_id": str(kwargs.get("parent_run_id", "")) if kwargs.get("parent_run_id") else None,
        })

    async def on_tool_error(self, error, *, run_id, **kwargs):
        pass

    async def on_chain_error(self, error, *, run_id, **kwargs):
        pass


@pytest.mark.asyncio
async def test_langgraph_node_boundary_detection():
    """
    验证 LangGraph 的 on_chain_start/end 事件，看能否检测 node 边界。
    
    关键问题：
    1. on_chain_end 触发的层级是什么？（graph level? node level? inner chain level?）
    2. 如何区分 node-level chain_end 和 inner chain_end？
    3. tags / name / parent_run_id 能否区分？
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, AIMessage
    from langchain_core.tools import tool
    from langgraph.graph import StateGraph, START, END
    from langgraph.prebuilt import ToolNode

    class State(TypedDict):
        messages: Annotated[list, operator.add]
        result: str

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)

    @tool
    def get_info(query: str) -> str:
        """Get information about a topic."""
        return f"Info about {query}: it's great."

    # Node A: 调用 LLM
    async def node_a(state: State) -> State:
        result = await llm.ainvoke(state["messages"])
        return {"messages": [result], "result": "node_a_done"}

    # Node B: 调用 LLM（第二个节点）
    async def node_b(state: State) -> State:
        msgs = [HumanMessage(content=f"Summarize: {state['result']}. One word.")]
        result = await llm.ainvoke(msgs)
        return {"messages": [result], "result": result.content}

    builder = StateGraph(State)
    builder.add_node("node_a", node_a)
    builder.add_node("node_b", node_b)
    builder.add_edge(START, "node_a")
    builder.add_edge("node_a", "node_b")
    builder.add_edge("node_b", END)
    graph = builder.compile()

    cb = ChainEventHandler()
    root_run_id = str(uuid.uuid4())

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="What is Python?")], "result": ""},
        config={"callbacks": [cb], "run_id": root_run_id},
    )

    # 分析事件
    print(f"\n{'='*80}")
    print(f"root_run_id: {root_run_id}")
    print(f"总事件数: {len(cb.events)}")
    print(f"{'='*80}")

    for i, e in enumerate(cb.events):
        indent = "  " if e.get("parent_run_id") else ""
        name = e.get("name", "")
        tags = e.get("tags", [])
        parent = e.get("parent_run_id", "")
        parent_short = parent[:8] if parent else "ROOT"

        print(f"  [{i:2d}] {e['type']:20s} name={name:15s} parent={parent_short:10s} tags={tags}")

    # 识别 node-level chain_end
    print(f"\n{'='*80}")
    print("Node-level 事件分析:")
    print(f"{'='*80}")

    chain_starts = [e for e in cb.events if e["type"] == "chain_start"]
    chain_ends = [e for e in cb.events if e["type"] == "chain_end"]
    llm_ends = [e for e in cb.events if e["type"] == "llm_end"]

    print(f"  chain_start 次数: {len(chain_starts)}")
    print(f"  chain_end 次数: {len(chain_ends)}")
    print(f"  llm_end 次数: {len(llm_ends)}")

    # 找 parent = root_run_id 的 chain_end（这应该是 node-level）
    root_children_end = [e for e in chain_ends if e.get("parent_run_id") == root_run_id]
    print(f"\n  parent=root_run_id 的 chain_end 次数: {len(root_children_end)}")
    for e in root_children_end:
        print(f"    run_id={e['run_id'][:8]}, tags={e.get('tags', [])}")

    # 找带 "graph:step:" tag 的事件
    step_events = [e for e in cb.events if any("graph:step:" in t for t in e.get("tags", []))]
    print(f"\n  带 'graph:step:' tag 的事件: {len(step_events)}")
    for e in step_events:
        print(f"    {e['type']:20s} tags={e.get('tags', [])}")

    # 找带 node name 的 chain_start
    named_starts = [e for e in chain_starts if e.get("name") in ("node_a", "node_b")]
    print(f"\n  name='node_a'/'node_b' 的 chain_start: {len(named_starts)}")
    for e in named_starts:
        print(f"    name={e['name']}, run_id={e['run_id'][:8]}")

    # 对应的 chain_end
    named_run_ids = {e["run_id"] for e in named_starts}
    named_ends = [e for e in chain_ends if e["run_id"] in named_run_ids]
    print(f"  对应的 chain_end: {len(named_ends)}")

    print(f"\n📝 结论:")
    if named_ends:
        print(f"  ✅ 可以通过 chain_start name 匹配 node name，再在 chain_end 用 run_id 匹配")
    if root_children_end:
        print(f"  ✅ 或通过 parent_run_id == root_run_id 找 node-level chain_end")
    if step_events:
        print(f"  ✅ 或通过 tags 中 'graph:step:xxx' 识别 node 事件")
