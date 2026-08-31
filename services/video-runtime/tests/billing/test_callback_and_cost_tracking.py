"""
计费相关验证测试 —— 真实 LLM 调用
========================================
验证 callback、streaming、token usage、LangSmith run_id 等行为。
所有 test 均为真实 API 调用，需要 OPENAI_API_KEY / GOOGLE_API_KEY / LANGCHAIN_API_KEY。

运行:
    # 全部跑（跳过需要额外环境的）
    pytest tests/billing/test_callback_and_cost_tracking.py -v -s

    # 单个
    pytest tests/billing/test_callback_and_cost_tracking.py::test_openai_ainvoke_on_llm_end -v -s
    pytest tests/billing/test_callback_and_cost_tracking.py::test_openai_astream_on_llm_end -v -s
    pytest tests/billing/test_callback_and_cost_tracking.py::test_gemini_astream_on_llm_end -v -s
    pytest tests/billing/test_callback_and_cost_tracking.py::test_langsmith_resume_same_run_id -v -s
    pytest tests/billing/test_callback_and_cost_tracking.py::test_callback_cost_vs_langsmith -v -s
    pytest tests/billing/test_callback_and_cost_tracking.py::test_langgraph_astream_callback_propagation -v -s
    pytest tests/billing/test_callback_and_cost_tracking.py::test_openai_structured_output_callback -v -s
    pytest tests/billing/test_callback_and_cost_tracking.py::test_gemini_ainvoke_cost -v -s
"""
import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

logger = logging.getLogger(__name__)


# ==================== 通用 callback handler（收集 on_llm_end 数据） ====================

@dataclass
class LLMEndRecord:
    """记录一次 on_llm_end 的数据"""
    run_id: str
    model_name: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    has_usage_metadata: bool = False
    raw_usage_metadata: Any = None
    raw_response_metadata: Any = None


class TestCostCallbackHandler:
    """
    测试用 async callback handler，收集每次 on_llm_end 的 token usage 信息。
    实现 AsyncCallbackHandler 所需的关键属性和方法。
    """

    # LangChain 要求的属性
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
        self.llm_end_records: List[LLMEndRecord] = []
        self.llm_start_count: int = 0
        self.llm_new_token_count: int = 0
        self.tool_start_count: int = 0
        self.tool_end_records: List[Dict[str, Any]] = []
        self.chat_model_start_count: int = 0

    # ---------- callback 方法（LangChain 会调的） ----------

    async def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self.llm_start_count += 1
        logger.info(f"[CB] on_llm_start: run_id={run_id}")

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.chat_model_start_count += 1
        logger.info(f"[CB] on_chat_model_start: run_id={run_id}")

    async def on_llm_new_token(self, token, *, run_id, **kwargs):
        self.llm_new_token_count += 1

    async def on_llm_end(self, response, *, run_id, **kwargs):
        """核心：提取 token usage"""
        record = LLMEndRecord(run_id=str(run_id))
        try:
            gen = response.generations[0][0]
            msg = gen.message
            record.raw_response_metadata = getattr(msg, "response_metadata", None)
            if record.raw_response_metadata:
                record.model_name = (
                    record.raw_response_metadata.get("model_name")
                    or record.raw_response_metadata.get("model")
                )
            usage = getattr(msg, "usage_metadata", None)
            if usage:
                record.has_usage_metadata = True
                record.raw_usage_metadata = usage
                # usage_metadata 可能是 dict 也可能是对象
                if isinstance(usage, dict):
                    record.input_tokens = usage.get("input_tokens", 0) or 0
                    record.output_tokens = usage.get("output_tokens", 0) or 0
                    record.total_tokens = usage.get("total_tokens", 0) or 0
                else:
                    record.input_tokens = getattr(usage, "input_tokens", 0) or 0
                    record.output_tokens = getattr(usage, "output_tokens", 0) or 0
                    record.total_tokens = getattr(usage, "total_tokens", 0) or 0
        except Exception as e:
            logger.warning(f"[CB] on_llm_end 提取失败: {e}", exc_info=True)

        self.llm_end_records.append(record)
        logger.info(
            f"[CB] on_llm_end: model={record.model_name}, "
            f"in={record.input_tokens}, out={record.output_tokens}, "
            f"total={record.total_tokens}, has_usage={record.has_usage_metadata}"
        )

    async def on_llm_error(self, error, *, run_id, **kwargs):
        logger.error(f"[CB] on_llm_error: {error}")

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        self.tool_start_count += 1

    async def on_tool_end(self, output, *, run_id, **kwargs):
        self.tool_end_records.append({"run_id": str(run_id)})

    async def on_tool_error(self, error, *, run_id, **kwargs):
        pass

    async def on_chain_start(self, serialized, inputs, *, run_id, **kwargs):
        pass

    async def on_chain_end(self, outputs, *, run_id, **kwargs):
        pass

    async def on_chain_error(self, error, *, run_id, **kwargs):
        pass

    # ---------- 辅助 ----------

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.llm_end_records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.llm_end_records)

    def summary(self) -> str:
        lines = [
            "=== Callback Summary ===",
            f"  on_chat_model_start: {self.chat_model_start_count}",
            f"  on_llm_start: {self.llm_start_count}",
            f"  on_llm_new_token: {self.llm_new_token_count}",
            f"  on_llm_end: {len(self.llm_end_records)}",
            f"  on_tool_end: {len(self.tool_end_records)}",
        ]
        for i, r in enumerate(self.llm_end_records):
            lines.append(
                f"    [{i}] model={r.model_name}, in={r.input_tokens}, out={r.output_tokens}, "
                f"total={r.total_tokens}, has_usage={r.has_usage_metadata}"
            )
        lines.append(f"  合计: in={self.total_input_tokens}, out={self.total_output_tokens}")
        return "\n".join(lines)


# ==============================================================================
# Test 1: OpenAI ainvoke + on_llm_end
# ==============================================================================

@pytest.mark.asyncio
async def test_openai_ainvoke_on_llm_end():
    """
    验证：ChatOpenAI.ainvoke() 后 on_llm_end 是否触发，token usage 是否可用。
    预期：✅ on_llm_end 触发，usage_metadata 有数据。
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage

    cb = TestCostCallbackHandler()
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    result = await llm.ainvoke(
        [HumanMessage(content="Say 'hello' and nothing else.")],
        config={"callbacks": [cb]},
    )

    print("\n" + cb.summary())
    print(f"AIMessage.content: {result.content}")
    print(f"AIMessage.usage_metadata: {result.usage_metadata}")

    assert len(cb.llm_end_records) == 1, f"Expected 1 on_llm_end, got {len(cb.llm_end_records)}"
    r = cb.llm_end_records[0]
    assert r.has_usage_metadata, "ainvoke should have usage_metadata"
    assert r.input_tokens > 0, "input_tokens > 0"
    assert r.output_tokens > 0, "output_tokens > 0"
    print("✅ PASS: OpenAI ainvoke → on_llm_end 有 token usage")


# ==============================================================================
# Test 2: OpenAI astream + on_llm_end (stream_usage=True vs False)
# ==============================================================================

@pytest.mark.asyncio
async def test_openai_astream_on_llm_end():
    """
    验证：ChatOpenAI(stream_usage=True).astream() 后 on_llm_end 是否触发、是否有 token usage。
    这是核心——streaming 下 callback 能否拿到 token usage。

    对比 stream_usage=True 和 stream_usage=False。
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage

    results = {}

    for stream_usage in [True, False]:
        cb = TestCostCallbackHandler()
        llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=stream_usage)

        chunks = []
        async for chunk in llm.astream(
            [HumanMessage(content="Count from 1 to 5, one per line.")],
            config={"callbacks": [cb]},
        ):
            if hasattr(chunk, "content") and chunk.content:
                chunks.append(chunk.content)

        full = "".join(chunks)
        print(f"\n--- stream_usage={stream_usage} ---")
        print(cb.summary())
        print(f"  new_token count: {cb.llm_new_token_count}")
        print(f"  content: {full[:80]}...")

        assert len(cb.llm_end_records) == 1, f"Expected 1 on_llm_end, got {len(cb.llm_end_records)}"
        r = cb.llm_end_records[0]
        results[stream_usage] = r

        if stream_usage:
            assert r.has_usage_metadata, "stream_usage=True 但 on_llm_end 没有 usage_metadata！"
            assert r.input_tokens > 0, "input > 0"
            assert r.output_tokens > 0, "output > 0"
            print(f"✅ stream_usage=True → on_llm_end 有 usage (in={r.input_tokens}, out={r.output_tokens})")
        else:
            if r.has_usage_metadata and r.input_tokens > 0:
                print(f"⚠️  stream_usage=False 也有 usage（某些 langchain 版本默认返回）")
            else:
                print(f"✅ stream_usage=False → on_llm_end 无 usage（符合预期）")

    # 对比
    if results[True].has_usage_metadata and results[False].has_usage_metadata:
        print("\n📝 两个都有 usage，stream_usage 参数可能在此版本不影响")
    elif results[True].has_usage_metadata and not results[False].has_usage_metadata:
        print("\n📝 只有 stream_usage=True 有 usage，必须在所有 ChatOpenAI 实例中加 stream_usage=True")


# ==============================================================================
# Test 3: Gemini astream + on_llm_end
# ==============================================================================

@pytest.mark.asyncio
async def test_gemini_astream_on_llm_end():
    """
    验证：ChatGoogleGenerativeAI.astream() 后 on_llm_end 是否触发。
    Gemini 默认返回 usage_metadata，不需要 stream_usage 参数。
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage

    cb = TestCostCallbackHandler()
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", timeout=60)

    chunks = []
    async for chunk in llm.astream(
        [HumanMessage(content="Say 'hello' in 3 languages.")],
        config={"callbacks": [cb]},
    ):
        if hasattr(chunk, "content") and chunk.content:
            chunks.append(chunk.content)

    full = "".join(chunks)
    print(f"\n--- Gemini astream ---")
    print(cb.summary())
    print(f"  content: {full[:100]}")

    assert len(cb.llm_end_records) >= 1, f"Expected ≥1 on_llm_end, got {len(cb.llm_end_records)}"
    r = cb.llm_end_records[0]

    if r.has_usage_metadata:
        assert r.input_tokens > 0 or r.output_tokens > 0, "tokens > 0"
        print(f"✅ Gemini astream → on_llm_end 有 usage (in={r.input_tokens}, out={r.output_tokens})")
    else:
        print(f"❌ Gemini astream → on_llm_end 没有 usage_metadata！")
        print(f"   raw_response_metadata: {r.raw_response_metadata}")
        # 不 assert fail — 记录结果即可


# ==============================================================================
# Test 4: Gemini ainvoke + cost 计算
# ==============================================================================

@pytest.mark.asyncio
async def test_gemini_ainvoke_cost():
    """
    验证：Gemini ainvoke 的 token usage 正确，成本计算可行。
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage
    from app.services.tool_service import ToolService
    from app.models.tool_enums import LLMModel

    cb = TestCostCallbackHandler()
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", timeout=60)

    result = await llm.ainvoke(
        [HumanMessage(content="What is the capital of France? Answer in one word.")],
        config={"callbacks": [cb]},
    )

    print(f"\n--- Gemini ainvoke ---")
    print(cb.summary())
    print(f"  content: {result.content}")
    print(f"  usage_metadata (from message): {result.usage_metadata}")

    assert len(cb.llm_end_records) >= 1
    r = cb.llm_end_records[0]

    if r.has_usage_metadata:
        cost = ToolService.calculate_cost(
            cost_type=LLMModel.GEMINI_2_5_FLASH,
            prompt_tokens=r.input_tokens,
            completion_tokens=r.output_tokens,
        )
        print(f"  cost: ${cost:.8f}")
        print(f"✅ Gemini ainvoke → callback 有 usage，成本 ${cost:.8f}")
    else:
        print(f"⚠️  Gemini ainvoke 无 usage_metadata")


# ==============================================================================
# Test 5: OpenAI with_structured_output + ainvoke + callback
# ==============================================================================

@pytest.mark.asyncio
async def test_openai_structured_output_callback():
    """
    验证：llm.with_structured_output().ainvoke() 传 callbacks 是否生效。
    模拟 agent_router_service 中 router 分析的场景。
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    from pydantic import BaseModel, Field

    class AnalysisResult(BaseModel):
        sentiment: str = Field(description="positive, negative, or neutral")
        confidence: float = Field(description="confidence 0-1")

    cb = TestCostCallbackHandler()
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)
    structured = llm.with_structured_output(AnalysisResult, include_raw=True)

    raw_result = await structured.ainvoke(
        [HumanMessage(content="I love this product!")],
        config={"callbacks": [cb]},
    )

    print(f"\n--- with_structured_output + ainvoke ---")
    print(cb.summary())
    print(f"  parsed: {raw_result['parsed']}")

    assert len(cb.llm_end_records) >= 1, "on_llm_end should fire"
    r = cb.llm_end_records[0]
    if r.has_usage_metadata:
        print(f"✅ with_structured_output → callback 有 usage (in={r.input_tokens}, out={r.output_tokens})")
    else:
        print(f"⚠️  with_structured_output → callback 无 usage_metadata")


# ==============================================================================
# Test 6: LangGraph astream 中 callback 是否传播到子节点 LLM 调用
# ==============================================================================

@pytest.mark.asyncio
async def test_langgraph_astream_callback_propagation():
    """
    验证：LangGraph graph.astream() 传 config={"callbacks": [cb]} 时，
    graph 节点内的 LLM 调用是否也触发 callback。

    这模拟 agent_router_service.astream() 的真实场景：
    - callbacks 在 RunnableConfig 中传入 graph.astream(config=config)
    - graph 节点内调用 llm.ainvoke() 或 llm.astream()

    关键问题：节点内 agent.ainvoke(inputs) 不带 config 时 callback 是否传播？
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, AIMessage
    from langgraph.graph import StateGraph, START, END
    from typing import TypedDict, Annotated
    import operator

    class GraphState(TypedDict):
        messages: Annotated[list, operator.add]
        result: str

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)

    # 节点 1：直接调 llm.ainvoke（不手动传 config）
    async def node_llm_direct(state: GraphState) -> GraphState:
        """模拟 agent 节点中直接调用 LLM"""
        msgs = state["messages"]
        # 注意：这里没有传 config，测试 callback 是否从 graph 继承
        result = await llm.ainvoke(msgs)
        return {"messages": [result], "result": result.content}

    # 节点 2：通过 astream 调用 LLM
    async def node_llm_stream(state: GraphState) -> GraphState:
        """模拟 agent 节点中 streaming 调用 LLM"""
        msgs = [HumanMessage(content=f"Summarize: {state['result']}. Reply in 5 words.")]
        chunks = []
        async for chunk in llm.astream(msgs):
            if chunk.content:
                chunks.append(chunk.content)
        return {"messages": [AIMessage(content="".join(chunks))], "result": "".join(chunks)}

    # 构建 graph
    builder = StateGraph(GraphState)
    builder.add_node("direct", node_llm_direct)
    builder.add_node("stream", node_llm_stream)
    builder.add_edge(START, "direct")
    builder.add_edge("direct", "stream")
    builder.add_edge("stream", END)
    graph = builder.compile()

    # 方式 A：通过 config 传 callback
    cb_a = TestCostCallbackHandler()
    result_a = await graph.ainvoke(
        {"messages": [HumanMessage(content="What is 2+2?")], "result": ""},
        config={"callbacks": [cb_a]},
    )

    print(f"\n--- LangGraph ainvoke (config callbacks) ---")
    print(cb_a.summary())
    print(f"  Final result: {result_a['result'][:80]}")

    # 验证
    print(f"\n📝 分析:")
    if len(cb_a.llm_end_records) >= 2:
        print(f"  ✅ 两个节点的 LLM 调用都触发了 callback（{len(cb_a.llm_end_records)} 次 on_llm_end）")
        for i, r in enumerate(cb_a.llm_end_records):
            print(f"    [{i}] has_usage={r.has_usage_metadata}, in={r.input_tokens}, out={r.output_tokens}")
    elif len(cb_a.llm_end_records) == 1:
        print(f"  ⚠️  只有 1 次 on_llm_end — 只有一个节点的 LLM 触发了 callback")
        print(f"     → 节点内的 llm.ainvoke() 不传 config 时 callback 不会传播！")
        print(f"     → 需要在每个节点内手动传 config")
    else:
        print(f"  ❌ 0 次 on_llm_end — callback 完全不生效")

    # 也测试 astream
    cb_b = TestCostCallbackHandler()
    async for event in graph.astream(
        {"messages": [HumanMessage(content="What is 3+3?")], "result": ""},
        config={"callbacks": [cb_b]},
        stream_mode="updates",
    ):
        pass  # 消费所有事件

    print(f"\n--- LangGraph astream (config callbacks) ---")
    print(cb_b.summary())

    if len(cb_b.llm_end_records) >= 2:
        print(f"  ✅ astream: 两个节点都触发 callback ({len(cb_b.llm_end_records)} 次)")
    elif len(cb_b.llm_end_records) == 1:
        print(f"  ⚠️  astream: 只有 1 次 on_llm_end")
    else:
        print(f"  ❌ astream: 0 次 on_llm_end")


# ==============================================================================
# Test 7: LangSmith resume 同一个 run_id 的行为
# ==============================================================================

@pytest.mark.asyncio
async def test_langsmith_resume_same_run_id():
    """
    验证：当用同一个 run_id 发起两次 LLM 调用（模拟 main + resume），
    LangSmith 里 read_run(run_id) 返回的 cost 和 token 是什么情况。

    关键影响：
    - 如果 LangSmith 只记录第一次调用 → resume 的成本丢失
    - 如果第二次调用报错 → resume 不能用相同 run_id
    - 如果两次合并 → billing 无需特殊处理
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage

    run_id_shared = str(uuid.uuid4())
    run_id_control = str(uuid.uuid4())
    print(f"\n共享 run_id: {run_id_shared}")
    print(f"对照 run_id: {run_id_control}")

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)

    # ---- 第一次调用（模拟 main） ----
    cb1 = TestCostCallbackHandler()
    r1 = await llm.ainvoke(
        [HumanMessage(content="What is 2+2?")],
        config={"callbacks": [cb1], "run_id": run_id_shared},
    )
    print(f"\nCall 1: in={cb1.total_input_tokens}, out={cb1.total_output_tokens}, content='{r1.content}'")

    await asyncio.sleep(3)

    # ---- 第二次调用（同 run_id，模拟 resume） ----
    cb2 = TestCostCallbackHandler()
    call2_ok = True
    call2_error = None
    try:
        r2 = await llm.ainvoke(
            [HumanMessage(content="What is 3+3?")],
            config={"callbacks": [cb2], "run_id": run_id_shared},
        )
        print(f"Call 2: in={cb2.total_input_tokens}, out={cb2.total_output_tokens}, content='{r2.content}'")
    except Exception as e:
        call2_ok = False
        call2_error = e
        print(f"Call 2 FAILED: {type(e).__name__}: {e}")

    # ---- 对照组（新 run_id） ----
    cb3 = TestCostCallbackHandler()
    r3 = await llm.ainvoke(
        [HumanMessage(content="What is 4+4?")],
        config={"callbacks": [cb3], "run_id": run_id_control},
    )
    print(f"Control: in={cb3.total_input_tokens}, out={cb3.total_output_tokens}, content='{r3.content}'")

    # ---- 等待 LangSmith 上传 ----
    print("\n⏳ 等待 LangSmith 上传 trace...")
    await asyncio.sleep(8)

    # ---- 查询 LangSmith ----
    try:
        from langsmith import Client
        client = Client()
    except Exception as e:
        print(f"⚠️  无法创建 LangSmith client: {e}")
        print("   跳过 LangSmith 查询（仅验证 callback 行为）")
        return

    # 查共享 run_id
    try:
        run_main = client.read_run(run_id=run_id_shared)
        print(f"\nLangSmith read_run({run_id_shared}):")
        print(f"  name: {run_main.name}")
        print(f"  status: {run_main.status}")
        print(f"  total_cost: {run_main.total_cost}")
        print(f"  total_tokens: {run_main.total_tokens}")
        print(f"  prompt_tokens: {run_main.prompt_tokens}")
        print(f"  completion_tokens: {run_main.completion_tokens}")
    except Exception as e:
        print(f"\nLangSmith read_run({run_id_shared}) FAILED: {e}")
        run_main = None

    # 查对照
    try:
        run_ctrl = client.read_run(run_id=run_id_control)
        print(f"\nLangSmith read_run({run_id_control}) [对照]:")
        print(f"  total_cost: {run_ctrl.total_cost}")
        print(f"  total_tokens: {run_ctrl.total_tokens}")
    except Exception as e:
        print(f"\nLangSmith read_run({run_id_control}) [对照] FAILED: {e}")
        run_ctrl = None

    # ---- 分析 ----
    print(f"\n{'='*60}")
    print(f"分析:")
    print(f"  Call 1 callback: in={cb1.total_input_tokens}, out={cb1.total_output_tokens}")
    if call2_ok:
        print(f"  Call 2 callback: in={cb2.total_input_tokens}, out={cb2.total_output_tokens}")
        expected_total = cb1.total_input_tokens + cb1.total_output_tokens + cb2.total_input_tokens + cb2.total_output_tokens
        print(f"  预期合计 tokens: {expected_total}")
    else:
        print(f"  Call 2 失败: {call2_error}")
        print(f"  → 同 run_id 不能重复使用，resume 必须用新 run_id！")

    if run_main:
        print(f"  LangSmith tokens: {run_main.total_tokens}")
        if call2_ok:
            combined_cb = cb1.total_input_tokens + cb1.total_output_tokens + cb2.total_input_tokens + cb2.total_output_tokens
            if run_main.total_tokens and abs(run_main.total_tokens - combined_cb) < 5:
                print(f"  → LangSmith 合并了两次调用的 tokens ✅")
            elif run_main.total_tokens and abs(run_main.total_tokens - (cb1.total_input_tokens + cb1.total_output_tokens)) < 5:
                print(f"  → LangSmith 只记录了第一次调用 ⚠️  resume 成本丢失！")
            else:
                print(f"  → LangSmith tokens 与 callback 不一致，需人工分析")

    print(f"\n⚠️  关键结论（请根据输出判断）:")
    if not call2_ok:
        print(f"  1. 同一 run_id 不能重复用于 LLM 调用")
        print(f"  2. resume 必须使用新的 run_id，billing 需要聚合所有 run_id 的成本")
    else:
        print(f"  1. 同一 run_id 可以重复用于 LLM 调用")
        print(f"  2. 需要验证 LangSmith 是否合并了两次调用")


# ==============================================================================
# Test 8: callback 自算成本 vs LangSmith 成本
# ==============================================================================

@pytest.mark.asyncio
async def test_callback_cost_vs_langsmith():
    """
    验证：callback 自算成本 和 LangSmith 成本的差异。
    使用 ToolService.calculate_cost() 计算 callback 侧成本。
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    from app.services.tool_service import ToolService, CREDITS_PER_DOLLAR
    from app.models.tool_enums import LLMModel

    run_id = str(uuid.uuid4())
    cb = TestCostCallbackHandler()
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)

    result = await llm.ainvoke(
        [HumanMessage(content="Write a short poem about the moon, 4 lines.")],
        config={"callbacks": [cb], "run_id": run_id},
    )

    print(f"\n--- Cost Comparison ---")
    print(cb.summary())

    # callback 侧
    cb_cost = None
    if cb.llm_end_records and cb.llm_end_records[0].has_usage_metadata:
        r = cb.llm_end_records[0]
        cb_cost = ToolService.calculate_cost(
            cost_type=LLMModel.GPT_4_1_MINI,
            prompt_tokens=r.input_tokens,
            completion_tokens=r.output_tokens,
        )
        cb_credits = int(cb_cost * CREDITS_PER_DOLLAR)
        print(f"\nCallback 侧: in={r.input_tokens}, out={r.output_tokens}, cost=${cb_cost:.8f}, credits={cb_credits}")
    else:
        print(f"\n⚠️  callback 无 usage_metadata")

    # LangSmith 侧
    print(f"\n⏳ 等待 LangSmith...")
    await asyncio.sleep(8)

    ls_cost = None
    try:
        from langsmith import Client
        client = Client()
        run = client.read_run(run_id=run_id)
        ls_cost = float(run.total_cost) if run.total_cost else None
        print(f"LangSmith 侧: total_cost=${ls_cost}, total_tokens={run.total_tokens}, "
              f"prompt={run.prompt_tokens}, completion={run.completion_tokens}")
    except Exception as e:
        print(f"LangSmith 查询失败: {e}")

    # 对比
    if cb_cost is not None and ls_cost is not None and ls_cost > 0:
        diff = abs(cb_cost - ls_cost)
        pct = diff / ls_cost * 100
        print(f"\n=== 对比 ===")
        print(f"  Callback:  ${cb_cost:.8f}")
        print(f"  LangSmith: ${ls_cost:.8f}")
        print(f"  差额: ${diff:.8f} ({pct:.1f}%)")
        if pct < 5:
            print(f"  ✅ 偏差 <5%，可用 callback 自算代替 LangSmith")
        elif pct < 20:
            print(f"  ⚠️  偏差 5-20%，检查定价表是否过期")
        else:
            print(f"  ❌ 偏差 >20%，定价表需要更新")
    else:
        print(f"\n⚠️  无法对比 (cb_cost={cb_cost}, ls_cost={ls_cost})")


# ==============================================================================
# Test 9: LangGraph astream + resume（模拟完整中断→恢复流程）
# ==============================================================================

@pytest.mark.asyncio
async def test_langgraph_interrupt_resume_run_id():
    """
    验证：LangGraph interrupt → resume 时，两次 astream 使用同一 run_id，
    LangSmith 记录的是一条还是两条 trace？cost 如何计算？

    这是最接近真实场景的测试。
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, AIMessage
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt, Command
    from typing import TypedDict, Annotated
    import operator

    class State(TypedDict):
        messages: Annotated[list, operator.add]
        step: str

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)

    async def step1(state: State) -> State:
        """第一步：调 LLM，然后 interrupt"""
        result = await llm.ainvoke(state["messages"])
        # 中断，等待用户确认
        user_response = interrupt("请确认是否继续？")
        return {"messages": [result], "step": f"step1_done_{user_response}"}

    async def step2(state: State) -> State:
        """第二步：resume 后调 LLM"""
        msgs = [HumanMessage(content=f"Continue. Previous: {state['step']}. Say 'done'.")]
        result = await llm.ainvoke(msgs)
        return {"messages": [result], "step": "step2_done"}

    builder = StateGraph(State)
    builder.add_node("step1", step1)
    builder.add_node("step2", step2)
    builder.add_edge(START, "step1")
    builder.add_edge("step1", "step2")
    builder.add_edge("step2", END)
    graph = builder.compile(checkpointer=MemorySaver())

    run_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    config = {
        "configurable": {"thread_id": thread_id},
        "run_id": run_id,
    }

    # ---- 第一次调用（会 interrupt） ----
    cb1 = TestCostCallbackHandler()
    config1 = {**config, "callbacks": [cb1]}
    interrupted = False

    try:
        async for event in graph.astream(
            {"messages": [HumanMessage(content="What is 2+2?")], "step": "init"},
            config=config1,
            stream_mode="updates",
        ):
            if isinstance(event, tuple) and len(event) == 2:
                ns, data = event
                if "__interrupt" in str(data):
                    interrupted = True
            elif isinstance(event, dict) and "__interrupt" in str(event):
                interrupted = True
    except Exception as e:
        # LangGraph interrupt 可能抛出异常
        if "interrupt" in str(e).lower():
            interrupted = True
        else:
            raise

    print(f"\n--- Phase 1 (main, should interrupt) ---")
    print(f"  Interrupted: {interrupted}")
    print(cb1.summary())

    # ---- 第二次调用（resume，使用同一 run_id） ----
    await asyncio.sleep(2)

    cb2 = TestCostCallbackHandler()
    config2 = {**config, "callbacks": [cb2]}

    print(f"\n--- Phase 2 (resume, same run_id={run_id}) ---")
    try:
        async for event in graph.astream(
            Command(resume="yes_continue"),
            config=config2,
            stream_mode="updates",
        ):
            pass
    except Exception as e:
        print(f"  Resume error: {type(e).__name__}: {e}")

    print(cb2.summary())

    # ---- 汇总 ----
    total_cb_in = cb1.total_input_tokens + cb2.total_input_tokens
    total_cb_out = cb1.total_output_tokens + cb2.total_output_tokens
    print(f"\n--- 汇总 ---")
    print(f"  Phase 1: on_llm_end={len(cb1.llm_end_records)}, in={cb1.total_input_tokens}, out={cb1.total_output_tokens}")
    print(f"  Phase 2: on_llm_end={len(cb2.llm_end_records)}, in={cb2.total_input_tokens}, out={cb2.total_output_tokens}")
    print(f"  合计: in={total_cb_in}, out={total_cb_out}")

    # ---- 查 LangSmith ----
    await asyncio.sleep(8)
    try:
        from langsmith import Client
        client = Client()
        run = client.read_run(run_id=run_id)
        print(f"\n  LangSmith run({run_id}):")
        print(f"    total_cost: {run.total_cost}")
        print(f"    total_tokens: {run.total_tokens}")
        print(f"    status: {run.status}")

        if run.total_tokens:
            if abs(run.total_tokens - (total_cb_in + total_cb_out)) < 10:
                print(f"    ✅ LangSmith tokens ≈ callback 合计 → 同 run_id 包含了所有调用")
            elif abs(run.total_tokens - (cb1.total_input_tokens + cb1.total_output_tokens)) < 10:
                print(f"    ⚠️  LangSmith tokens ≈ Phase 1 → 只记录了第一次，resume 成本丢失!")
            else:
                print(f"    ❓ LangSmith tokens 与 callback 不一致，需要人工分析")
    except Exception as e:
        print(f"\n  LangSmith 查询失败: {e}")

    print(f"\n📝 无论 LangSmith 结果如何，callback 侧的 token 计数是可靠的。")
    print(f"   建议：每次 astream 都用独立 callback，最后汇总 tokens 计算成本。")
