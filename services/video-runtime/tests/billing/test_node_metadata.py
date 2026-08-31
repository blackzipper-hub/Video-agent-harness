"""
验证 LangGraph callback 中 node 元数据的获取方式
================================================
重点验证：
1. on_chain_end 中 metadata["langgraph_node"] 是否可用（官方 API）
2. parent_run_id == root_run_id 检测 node 边界（不依赖 tags）
3. on_chain_start name 参数获取 node 名称（不依赖 tags）
4. 哪种方式最稳定、不依赖 LangGraph 内部实现

运行:
    conda run -n cuti-video-local pytest tests/billing/test_node_metadata.py -v -s
"""
import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, TypedDict, Annotated, Optional
import operator

import pytest

logger = logging.getLogger(__name__)


class MetadataTrackingHandler:
    """记录所有 callback 事件的 metadata、tags、parent_run_id、name"""

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
        self.root_run_id: Optional[str] = None

    async def on_chain_start(self, serialized, inputs, *, run_id, **kwargs):
        parent = kwargs.get("parent_run_id")
        metadata = kwargs.get("metadata", {})
        name = kwargs.get("name", serialized.get("name", "?") if serialized else "?")
        tags = kwargs.get("tags", [])

        # 记录 root_run_id（第一个 chain_start 且没有 parent 的就是 root）
        if not self.root_run_id and parent is None:
            self.root_run_id = str(run_id)

        self.events.append({
            "type": "chain_start",
            "run_id": str(run_id),
            "name": name,
            "parent_run_id": str(parent) if parent else None,
            "tags": tags,
            "metadata": dict(metadata) if metadata else {},
            "langgraph_node": metadata.get("langgraph_node") if metadata else None,
        })

    async def on_chain_end(self, outputs, *, run_id, **kwargs):
        parent = kwargs.get("parent_run_id")
        metadata = kwargs.get("metadata", {})
        tags = kwargs.get("tags", [])

        self.events.append({
            "type": "chain_end",
            "run_id": str(run_id),
            "parent_run_id": str(parent) if parent else None,
            "tags": tags,
            "metadata": dict(metadata) if metadata else {},
            "langgraph_node": metadata.get("langgraph_node") if metadata else None,
        })

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        metadata = kwargs.get("metadata", {})
        self.events.append({
            "type": "chat_model_start",
            "run_id": str(run_id),
            "parent_run_id": str(kwargs["parent_run_id"]) if kwargs.get("parent_run_id") else None,
            "metadata": dict(metadata) if metadata else {},
            "langgraph_node": metadata.get("langgraph_node") if metadata else None,
        })

    async def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        pass

    async def on_llm_new_token(self, token, *, run_id, **kwargs):
        pass

    async def on_llm_end(self, response, *, run_id, **kwargs):
        metadata = kwargs.get("metadata", {})
        self.events.append({
            "type": "llm_end",
            "run_id": str(run_id),
            "parent_run_id": str(kwargs["parent_run_id"]) if kwargs.get("parent_run_id") else None,
            "metadata": dict(metadata) if metadata else {},
            "langgraph_node": metadata.get("langgraph_node") if metadata else None,
        })

    async def on_llm_error(self, error, *, run_id, **kwargs):
        pass

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        metadata = kwargs.get("metadata", {})
        self.events.append({
            "type": "tool_start",
            "run_id": str(run_id),
            "name": kwargs.get("name", "?"),
            "parent_run_id": str(kwargs["parent_run_id"]) if kwargs.get("parent_run_id") else None,
            "metadata": dict(metadata) if metadata else {},
            "langgraph_node": metadata.get("langgraph_node") if metadata else None,
        })

    async def on_tool_end(self, output, *, run_id, **kwargs):
        metadata = kwargs.get("metadata", {})
        self.events.append({
            "type": "tool_end",
            "run_id": str(run_id),
            "name": kwargs.get("name", getattr(output, "name", "?")),
            "parent_run_id": str(kwargs["parent_run_id"]) if kwargs.get("parent_run_id") else None,
            "metadata": dict(metadata) if metadata else {},
            "langgraph_node": metadata.get("langgraph_node") if metadata else None,
        })

    async def on_tool_error(self, error, *, run_id, **kwargs):
        pass

    async def on_chain_error(self, error, *, run_id, **kwargs):
        pass


@pytest.mark.asyncio
async def test_langgraph_node_metadata_in_callbacks():
    """
    验证 LangGraph 官方 metadata["langgraph_node"] 在各种 callback 中是否可用。
    
    关键验证点：
    1. on_chain_start/end 中 metadata.langgraph_node 能拿到当前 node name
    2. on_llm_end 中 metadata.langgraph_node 能拿到所在 node name
    3. on_tool_end 中 metadata.langgraph_node 能拿到所在 node name
    4. parent_run_id 方式 vs metadata 方式 对比
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool
    from langgraph.graph import StateGraph, START, END

    class State(TypedDict):
        messages: Annotated[list, operator.add]
        step: str

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)

    @tool
    def lookup_info(query: str) -> str:
        """Look up information about a topic."""
        return f"Result for {query}: 42"

    llm_with_tools = llm.bind_tools([lookup_info])

    # Node: analyze (LLM call that may use tool)
    async def analyze_node(state: State) -> State:
        result = await llm_with_tools.ainvoke(state["messages"])
        return {"messages": [result], "step": "analyzed"}

    # Node: summarize (simple LLM call)
    async def summarize_node(state: State) -> State:
        msgs = [HumanMessage(content=f"Say OK in one word.")]
        result = await llm.ainvoke(msgs)
        return {"messages": [result], "step": "summarized"}

    # Build graph: analyze → summarize
    from langgraph.prebuilt import ToolNode
    tool_node = ToolNode([lookup_info])

    builder = StateGraph(State)
    builder.add_node("analyze", analyze_node)
    builder.add_node("tools", tool_node)
    builder.add_node("summarize", summarize_node)
    builder.add_edge(START, "analyze")

    def should_continue(state: State):
        last_msg = state["messages"][-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            return "tools"
        return "summarize"

    builder.add_conditional_edges("analyze", should_continue, {"tools": "tools", "summarize": "summarize"})
    builder.add_edge("tools", "analyze")
    builder.add_edge("summarize", END)
    graph = builder.compile()

    cb = MetadataTrackingHandler()
    root_run_id = str(uuid.uuid4())

    # Force tool usage with a specific prompt
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="Use the lookup_info tool to find info about 'Python'. Then summarize.")], "step": ""},
        config={"callbacks": [cb], "run_id": root_run_id},
    )

    # ====== Analysis ======
    print(f"\n{'='*100}")
    print(f"root_run_id: {root_run_id}")
    print(f"handler.root_run_id: {cb.root_run_id}")
    print(f"总事件数: {len(cb.events)}")
    print(f"{'='*100}")

    # 1. Print all events with metadata
    print(f"\n--- 所有事件 (with metadata.langgraph_node) ---")
    for i, e in enumerate(cb.events):
        lg_node = e.get("langgraph_node", "")
        name = e.get("name", "")
        parent = e.get("parent_run_id", "")
        parent_short = parent[:8] if parent else "ROOT"
        tags = e.get("tags", [])
        meta_keys = list(e.get("metadata", {}).keys())

        print(f"  [{i:2d}] {e['type']:20s} "
              f"lg_node={str(lg_node or ''):15s} "
              f"name={str(name or ''):15s} "
              f"parent={str(parent_short or ''):10s} "
              f"tags={tags}  "
              f"meta_keys={meta_keys}")

    # 2. Verify langgraph_node in on_chain_end
    chain_ends = [e for e in cb.events if e["type"] == "chain_end"]
    chain_ends_with_lg_node = [e for e in chain_ends if e.get("langgraph_node")]
    print(f"\n--- on_chain_end 分析 ---")
    print(f"  总数: {len(chain_ends)}")
    print(f"  有 langgraph_node: {len(chain_ends_with_lg_node)}")
    for e in chain_ends_with_lg_node:
        print(f"    langgraph_node={e['langgraph_node']}, tags={e.get('tags', [])}")

    # 3. Verify langgraph_node in on_llm_end
    llm_ends = [e for e in cb.events if e["type"] == "llm_end"]
    llm_ends_with_lg_node = [e for e in llm_ends if e.get("langgraph_node")]
    print(f"\n--- on_llm_end 分析 ---")
    print(f"  总数: {len(llm_ends)}")
    print(f"  有 langgraph_node: {len(llm_ends_with_lg_node)}")
    for e in llm_ends_with_lg_node:
        print(f"    langgraph_node={e['langgraph_node']}")

    # 4. Verify langgraph_node in on_tool_end
    tool_ends = [e for e in cb.events if e["type"] == "tool_end"]
    tool_ends_with_lg_node = [e for e in tool_ends if e.get("langgraph_node")]
    print(f"\n--- on_tool_end 分析 ---")
    print(f"  总数: {len(tool_ends)}")
    print(f"  有 langgraph_node: {len(tool_ends_with_lg_node)}")
    for e in tool_ends_with_lg_node:
        print(f"    langgraph_node={e['langgraph_node']}, name={e.get('name', '?')}")

    # 5. Compare: parent_run_id vs metadata for node detection
    print(f"\n--- 方案对比: parent_run_id vs metadata ---")
    
    # Method A: parent_run_id == root_run_id (chain_end only)
    method_a = [e for e in chain_ends if e.get("parent_run_id") == root_run_id]
    print(f"  方案 A (parent_run_id==root): {len(method_a)} 个 chain_end")
    for e in method_a:
        print(f"    tags={e.get('tags', [])}, lg_node={e.get('langgraph_node')}")

    # Method B: metadata.langgraph_node (chain_end with node name)
    our_nodes = {"analyze", "summarize", "tools"}
    method_b = [e for e in chain_ends if e.get("langgraph_node") in our_nodes]
    print(f"  方案 B (metadata.langgraph_node in our_nodes): {len(method_b)} 个 chain_end")
    for e in method_b:
        print(f"    langgraph_node={e['langgraph_node']}, tags={e.get('tags', [])}")

    # Method C: name tracking (chain_start name → chain_end run_id)
    named_starts = [e for e in cb.events if e["type"] == "chain_start" and e.get("name") in our_nodes]
    named_run_ids = {e["run_id"] for e in named_starts}
    method_c = [e for e in chain_ends if e["run_id"] in named_run_ids]
    print(f"  方案 C (name tracking via chain_start): {len(method_c)} 个 chain_end")

    # 6. Print all metadata keys found
    all_meta_keys = set()
    for e in cb.events:
        all_meta_keys.update(e.get("metadata", {}).keys())
    print(f"\n--- 所有出现过的 metadata keys ---")
    print(f"  {sorted(all_meta_keys)}")

    # Assertions
    print(f"\n{'='*100}")
    print("📝 断言验证:")

    # ===== 关键发现: metadata 只在 *_start callbacks 中有，*_end 中没有！=====

    # A1: langgraph_node 在 on_chain_START 中可用
    chain_starts_with_lg = [e for e in cb.events if e["type"] == "chain_start" and e.get("langgraph_node")]
    assert len(chain_starts_with_lg) > 0, "on_chain_start 中应该有 langgraph_node"
    print(f"  ✅ on_chain_start 中有 langgraph_node ({len(chain_starts_with_lg)} 次)")

    # A2: langgraph_node 在 on_chat_model_start 中可用
    cm_starts_with_lg = [e for e in cb.events if e["type"] == "chat_model_start" and e.get("langgraph_node")]
    assert len(cm_starts_with_lg) > 0, "on_chat_model_start 中应该有 langgraph_node"
    print(f"  ✅ on_chat_model_start 中有 langgraph_node ({len(cm_starts_with_lg)} 次)")

    # A3: ❌ langgraph_node 在 on_llm_end / on_chain_end / on_tool_end 中 NOT 可用
    assert len(llm_ends_with_lg_node) == 0, "on_llm_end 不应该有 langgraph_node"
    assert len(chain_ends_with_lg_node) == 0, "on_chain_end 不应该有 langgraph_node"
    assert len(tool_ends_with_lg_node) == 0, "on_tool_end 不应该有 langgraph_node"
    print(f"  ⚠️ on_llm_end / on_chain_end / on_tool_end 中 metadata 为空 (langgraph_node 不可用)")

    # A4: 方案 A (parent_run_id) 能检测 node 边界
    assert len(method_a) >= 2, f"方案 A 应至少检测到 2 个 node (got {len(method_a)})"
    print(f"  ✅ 方案 A (parent_run_id==root in chain_end) 检测到 {len(method_a)} 个 node 边界")

    # A5: 方案 B (metadata in chain_end) 不可行
    assert len(method_b) == 0, "方案 B (metadata in chain_end) 不应该有结果"
    print(f"  ❌ 方案 B (metadata.langgraph_node in chain_end) = 0 → 不可行!")

    # A6: 方案 C (name tracking: start name → end run_id) 可行
    assert len(method_c) >= 2, f"方案 C 应至少检测到 2 个 node (got {len(method_c)})"
    print(f"  ✅ 方案 C (name tracking via chain_start) 检测到 {len(method_c)} 个 node 边界")

    # A7: langgraph_node 值正确（是我们定义的 node 名称）
    lg_node_values = {e["langgraph_node"] for e in cb.events if e.get("langgraph_node")}
    print(f"  所有 langgraph_node 值: {lg_node_values}")
    assert lg_node_values.issubset(our_nodes), f"langgraph_node 值应是我们定义的 node name: {lg_node_values}"
    print(f"  ✅ langgraph_node 值都是我们定义的 node 名称")

    # A8: tool_start 也有 langgraph_node
    tool_starts_with_lg = [e for e in cb.events if e["type"] == "tool_start" and e.get("langgraph_node")]
    assert len(tool_starts_with_lg) > 0, "on_tool_start 中应该有 langgraph_node"
    print(f"  ✅ on_tool_start 中有 langgraph_node ({len(tool_starts_with_lg)} 次)")

    print(f"\n{'='*100}")
    print(f"📝 核心发现:")
    print(f"  1. metadata (包括 langgraph_node) 只在 *_start callbacks 中有")
    print(f"  2. *_end callbacks (on_llm_end, on_chain_end, on_tool_end) 的 metadata 为空!")
    print(f"  3. 要在 end callback 中知道 node → 必须用 run_id 映射或 parent_run_id")
    print(f"")
    print(f"📝 推荐方案 (组合):")
    print(f"  - on_chain_start: 记录 {{run_id: node_name}} 映射 (从 name 参数或 metadata.langgraph_node)")
    print(f"  - on_chain_end: parent_run_id == root_run_id → node 边界 → 查映射得到 node name")
    print(f"  - on_chat_model_start: 记录 {{run_id: langgraph_node}} (知道 LLM 属于哪个 node)")
    print(f"  - on_llm_end: 查 parent_run_id → 从映射得知所在 node")
    print(f"  ❌ 不用 tags['graph:step:N'] — LangGraph 内部实现")
    print(f"  ❌ 不用 metadata 在 end callbacks — 不可用")
