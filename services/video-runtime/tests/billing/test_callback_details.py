"""
计费验证测试 — 第二轮：字段详情 + 模型区分 + LangSmith resume 深入
=================================================================
验证:
  1. on_llm_end 中如何区分模型（GPT vs Gemini）
  2. stream_usage / usage_metadata / token_usage 各字段差异
  3. ainvoke vs astream 能否区分
  4. LangSmith resume 同 run_id — 更深入查 list_runs / child_runs
  5. on_tool_end 中如何拿到 tool name 和参数

运行:
    conda run -n cuti-video-local pytest tests/billing/test_callback_details.py -v -s
"""
import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import pytest

logger = logging.getLogger(__name__)


# ==================== 详细记录 callback handler ====================

class DetailedCallbackHandler:
    """记录 on_llm_end / on_tool_end 的完整原始数据"""

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
        self.records: List[Dict[str, Any]] = []

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.records.append({
            "event": "chat_model_start",
            "run_id": str(run_id),
            "serialized_id": serialized.get("id", []) if serialized else None,
            "serialized_name": serialized.get("name", "") if serialized else None,
            "kwargs_keys": list(kwargs.keys()),
        })

    async def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        pass

    async def on_llm_new_token(self, token, *, run_id, **kwargs):
        pass

    async def on_llm_end(self, response, *, run_id, **kwargs):
        record = {
            "event": "llm_end",
            "run_id": str(run_id),
        }
        try:
            gen = response.generations[0][0]
            msg = gen.message

            # 1. response_metadata — 原始字段全部记录
            rm = getattr(msg, "response_metadata", None)
            record["response_metadata"] = dict(rm) if rm else None

            # 2. usage_metadata — 原始字段全部记录
            um = getattr(msg, "usage_metadata", None)
            if isinstance(um, dict):
                record["usage_metadata"] = um
            elif um is not None:
                # 可能是 UsageMetadata 对象，转 dict
                record["usage_metadata"] = {
                    k: getattr(um, k, None)
                    for k in ["input_tokens", "output_tokens", "total_tokens",
                              "input_token_details", "output_token_details"]
                }
            else:
                record["usage_metadata"] = None

            # 3. 消息类型
            record["message_type"] = type(msg).__name__

            # 4. 尝试提取模型名称（多种路径）
            record["model_name"] = rm.get("model_name") if rm else None
            record["model_provider"] = rm.get("model_provider") if rm else None
            record["token_usage_raw"] = rm.get("token_usage") if rm else None
            record["model_paths"] = {
                "response_metadata.model_name": rm.get("model_name") if rm else None,
                "response_metadata.model": rm.get("model") if rm else None,
                "response_metadata.model_provider": rm.get("model_provider") if rm else None,
            }
        except Exception as e:
            record["error"] = str(e)

        self.records.append(record)

    async def on_llm_error(self, error, *, run_id, **kwargs):
        pass

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        self.records.append({
            "event": "tool_start",
            "run_id": str(run_id),
            "serialized": serialized,
            "input_str": str(input_str)[:200] if input_str else None,
            "kwargs_keys": list(kwargs.keys()),
        })

    async def on_tool_end(self, output, *, run_id, **kwargs):
        record = {
            "event": "tool_end",
            "run_id": str(run_id),
            "output_type": type(output).__name__,
            "kwargs_keys": list(kwargs.keys()),
        }
        # 尝试获取 tool name（从 kwargs 或 output）
        if "name" in kwargs:
            record["tool_name_from_kwargs"] = kwargs["name"]
        if hasattr(output, "tool_call_id"):
            record["tool_call_id"] = output.tool_call_id
        if hasattr(output, "name"):
            record["tool_name_from_output"] = output.name
        if hasattr(output, "content"):
            record["output_content"] = str(output.content)[:200]

        self.records.append(record)

    async def on_tool_error(self, error, *, run_id, **kwargs):
        pass

    async def on_chain_start(self, serialized, inputs, *, run_id, **kwargs):
        pass

    async def on_chain_end(self, outputs, *, run_id, **kwargs):
        pass

    async def on_chain_error(self, error, *, run_id, **kwargs):
        pass

    def dump(self) -> str:
        return json.dumps(self.records, indent=2, default=str, ensure_ascii=False)

    def llm_end_records(self):
        return [r for r in self.records if r["event"] == "llm_end"]

    def tool_end_records(self):
        return [r for r in self.records if r["event"] == "tool_end"]


# ==============================================================================
# Test 1: on_llm_end 模型区分 — GPT vs Gemini
# ==============================================================================

@pytest.mark.asyncio
async def test_model_identification_in_on_llm_end():
    """
    验证：on_llm_end 中如何区分是 GPT 还是 Gemini 调用。
    打印 response_metadata 和 usage_metadata 的完整字段。
    """
    from langchain_openai import ChatOpenAI
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage

    # --- OpenAI ---
    cb_openai = DetailedCallbackHandler()
    llm_openai = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)
    await llm_openai.ainvoke(
        [HumanMessage(content="Say 'hi'.")],
        config={"callbacks": [cb_openai]},
    )

    # --- Gemini ---
    cb_gemini = DetailedCallbackHandler()
    llm_gemini = ChatGoogleGenerativeAI(model="gemini-2.5-flash", timeout=60)
    await llm_gemini.ainvoke(
        [HumanMessage(content="Say 'hi'.")],
        config={"callbacks": [cb_gemini]},
    )

    print("\n" + "="*80)
    print("OpenAI on_llm_end 完整数据:")
    print("="*80)
    for r in cb_openai.llm_end_records():
        print(json.dumps(r, indent=2, default=str, ensure_ascii=False))

    print("\n" + "="*80)
    print("Gemini on_llm_end 完整数据:")
    print("="*80)
    for r in cb_gemini.llm_end_records():
        print(json.dumps(r, indent=2, default=str, ensure_ascii=False))

    # 验证模型区分
    openai_r = cb_openai.llm_end_records()[0]
    gemini_r = cb_gemini.llm_end_records()[0]

    print("\n" + "="*80)
    print("模型区分方法:")
    print("="*80)
    print(f"OpenAI model_name: {openai_r['model_paths']['response_metadata.model_name']}")
    print(f"OpenAI model_provider: {openai_r['model_paths']['response_metadata.model_provider']}")
    print(f"Gemini model_name: {gemini_r['model_paths']['response_metadata.model_name']}")
    print(f"Gemini model: {gemini_r['model_paths']['response_metadata.model']}")
    print(f"Gemini model_provider: {gemini_r['model_paths']['response_metadata.model_provider']}")

    # 验证
    assert openai_r['model_paths']['response_metadata.model_name'] is not None, "OpenAI 应有 model_name"
    print("\n✅ 可通过 response_metadata.model_name 或 response_metadata.model 区分模型")


# ==============================================================================
# Test 2: stream_usage 字段详情 — ainvoke vs astream
# ==============================================================================

@pytest.mark.asyncio
async def test_stream_usage_field_details():
    """
    验证：ainvoke vs astream(stream_usage=True) 返回的 usage 字段有什么不同？
    重点看：
    - usage_metadata 的结构
    - response_metadata.token_usage 的结构
    - 是否有 input_token_details / output_token_details
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage

    prompt = [HumanMessage(content="Count 1 to 3.")]

    # --- ainvoke ---
    cb_invoke = DetailedCallbackHandler()
    llm_invoke = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
    r_invoke = await llm_invoke.ainvoke(prompt, config={"callbacks": [cb_invoke]})

    # --- astream (stream_usage=True) ---
    cb_stream = DetailedCallbackHandler()
    llm_stream = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)
    chunks = []
    async for chunk in llm_stream.astream(prompt, config={"callbacks": [cb_stream]}):
        if hasattr(chunk, "content") and chunk.content:
            chunks.append(chunk.content)

    # --- astream (stream_usage=False) ---
    cb_no_stream = DetailedCallbackHandler()
    llm_no_stream = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=False)
    async for chunk in llm_no_stream.astream(prompt, config={"callbacks": [cb_no_stream]}):
        pass

    print("\n" + "="*80)
    print("ainvoke — on_llm_end 字段:")
    print("="*80)
    for r in cb_invoke.llm_end_records():
        print(f"  usage_metadata: {json.dumps(r['usage_metadata'], default=str)}")
        print(f"  token_usage_raw: {json.dumps(r.get('token_usage_raw'), default=str)}")
        print(f"  response_metadata 全部: {json.dumps(r['response_metadata'], default=str)}")

    print("\n" + "="*80)
    print("astream (stream_usage=True) — on_llm_end 字段:")
    print("="*80)
    for r in cb_stream.llm_end_records():
        print(f"  usage_metadata: {json.dumps(r['usage_metadata'], default=str)}")
        print(f"  token_usage_raw: {json.dumps(r.get('token_usage_raw'), default=str)}")
        print(f"  response_metadata 全部: {json.dumps(r['response_metadata'], default=str)}")

    print("\n" + "="*80)
    print("astream (stream_usage=False) — on_llm_end 字段:")
    print("="*80)
    for r in cb_no_stream.llm_end_records():
        print(f"  usage_metadata: {json.dumps(r['usage_metadata'], default=str)}")
        print(f"  token_usage_raw: {json.dumps(r.get('token_usage_raw'), default=str)}")
        print(f"  response_metadata 全部: {json.dumps(r['response_metadata'], default=str)}")

    # 对比 ainvoke message 上的字段
    print("\n" + "="*80)
    print("ainvoke — AIMessage 上直接拿的字段:")
    print("="*80)
    print(f"  r_invoke.usage_metadata: {r_invoke.usage_metadata}")
    print(f"  r_invoke.response_metadata: {json.dumps(r_invoke.response_metadata, default=str)}")

    # 验证 ainvoke 和 astream(True) 结构是否一致
    invoke_um = cb_invoke.llm_end_records()[0]["usage_metadata"]
    stream_um = cb_stream.llm_end_records()[0]["usage_metadata"]
    print(f"\n📝 ainvoke usage_metadata keys: {list(invoke_um.keys()) if invoke_um else 'None'}")
    print(f"📝 astream usage_metadata keys: {list(stream_um.keys()) if stream_um else 'None'}")

    if invoke_um and stream_um:
        common_keys = set(invoke_um.keys()) & set(stream_um.keys())
        diff_keys_invoke = set(invoke_um.keys()) - set(stream_um.keys())
        diff_keys_stream = set(stream_um.keys()) - set(invoke_um.keys())
        print(f"📝 共同 keys: {common_keys}")
        print(f"📝 ainvoke 独有 keys: {diff_keys_invoke}")
        print(f"📝 astream 独有 keys: {diff_keys_stream}")


# ==============================================================================
# Test 3: Gemini usage_metadata 字段详情
# ==============================================================================

@pytest.mark.asyncio
async def test_gemini_usage_field_details():
    """Gemini 的 usage_metadata 和 response_metadata 完整字段"""
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage

    # --- ainvoke ---
    cb_invoke = DetailedCallbackHandler()
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", timeout=60)
    r = await llm.ainvoke(
        [HumanMessage(content="Count 1 to 3.")],
        config={"callbacks": [cb_invoke]},
    )

    # --- astream ---
    cb_stream = DetailedCallbackHandler()
    async for chunk in llm.astream(
        [HumanMessage(content="Count 1 to 3.")],
        config={"callbacks": [cb_stream]},
    ):
        pass

    print("\n" + "="*80)
    print("Gemini ainvoke — on_llm_end 字段:")
    print("="*80)
    for rec in cb_invoke.llm_end_records():
        print(f"  usage_metadata: {json.dumps(rec['usage_metadata'], default=str)}")
        print(f"  response_metadata keys: {list(rec['response_metadata'].keys()) if rec['response_metadata'] else 'None'}")

    print(f"\nGemini AIMessage.usage_metadata: {r.usage_metadata}")
    print(f"Gemini AIMessage.response_metadata: {json.dumps(r.response_metadata, default=str)}")

    print("\n" + "="*80)
    print("Gemini astream — on_llm_end 字段:")
    print("="*80)
    for rec in cb_stream.llm_end_records():
        print(f"  usage_metadata: {json.dumps(rec['usage_metadata'], default=str)}")


# ==============================================================================
# Test 4: LangSmith resume run_id — 深入查 list_runs
# ==============================================================================

@pytest.mark.asyncio
async def test_langsmith_resume_run_id_deep():
    """
    深入验证：同 run_id 两次调用后，LangSmith 里到底有几条 run？
    用 list_runs 按 project + filter 查，看是不是有 child run 或者第二条 run。
    也检查 LangGraph interrupt/resume 场景。
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, AIMessage
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt, Command
    from typing import TypedDict, Annotated
    import operator

    # ---- 场景 A：直接 LLM 调用，同 run_id ----
    run_id_a = str(uuid.uuid4())
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)

    await llm.ainvoke(
        [HumanMessage(content="What is 1+1?")],
        config={"run_id": run_id_a},
    )
    await asyncio.sleep(1)
    await llm.ainvoke(
        [HumanMessage(content="What is 2+2?")],
        config={"run_id": run_id_a},
    )
    print(f"\n场景 A: 直接 LLM 两次调用, run_id={run_id_a}")

    # ---- 场景 B：LangGraph interrupt/resume，同 run_id ----
    class State(TypedDict):
        messages: Annotated[list, operator.add]
        step: str

    async def step1(state: State) -> State:
        result = await llm.ainvoke(state["messages"])
        user_response = interrupt("confirm?")
        return {"messages": [result], "step": f"done_{user_response}"}

    async def step2(state: State) -> State:
        result = await llm.ainvoke([HumanMessage(content="Say 'resumed'.")])
        return {"messages": [result], "step": "step2_done"}

    builder = StateGraph(State)
    builder.add_node("s1", step1)
    builder.add_node("s2", step2)
    builder.add_edge(START, "s1")
    builder.add_edge("s1", "s2")
    builder.add_edge("s2", END)
    graph = builder.compile(checkpointer=MemorySaver())

    run_id_b = str(uuid.uuid4())
    thread_id_b = str(uuid.uuid4())
    config_b = {"configurable": {"thread_id": thread_id_b}, "run_id": run_id_b}

    # Phase 1
    async for _ in graph.astream(
        {"messages": [HumanMessage(content="Hello")], "step": ""},
        config=config_b, stream_mode="updates",
    ):
        pass

    await asyncio.sleep(1)

    # Phase 2 (resume)
    async for _ in graph.astream(
        Command(resume="yes"),
        config=config_b, stream_mode="updates",
    ):
        pass
    print(f"场景 B: LangGraph interrupt/resume, run_id={run_id_b}")

    # ---- 等 LangSmith ----
    print("\n⏳ 等待 LangSmith 上传（15s）...")
    await asyncio.sleep(15)

    from langsmith import Client
    client = Client()

    # ---- 查场景 A ----
    print(f"\n{'='*60}")
    print(f"场景 A: 直接 LLM 两次调用 (run_id={run_id_a})")
    print(f"{'='*60}")

    try:
        run_a = client.read_run(run_id=run_id_a)
        print(f"  read_run: name={run_a.name}, status={run_a.status}")
        print(f"  total_cost={run_a.total_cost}, total_tokens={run_a.total_tokens}")
        print(f"  prompt_tokens={run_a.prompt_tokens}, completion_tokens={run_a.completion_tokens}")
        print(f"  child_run_ids={run_a.child_run_ids}")
    except Exception as e:
        print(f"  read_run FAILED: {e}")

    # 查 child runs
    try:
        children = list(client.list_runs(
            run_ids=[run_id_a],
            is_root=False,
        ))
        print(f"  child runs (is_root=False): {len(children)}")
        for c in children[:5]:
            print(f"    - {c.id}: name={c.name}, tokens={c.total_tokens}, cost={c.total_cost}")
    except Exception as e:
        print(f"  list child runs failed: {e}")

    # 按 project 查所有 runs（最近的）
    try:
        project_name = run_a.session_name if hasattr(run_a, 'session_name') else None
        if project_name:
            recent_runs = list(client.list_runs(
                project_name=project_name,
                filter=f'eq(name, "ChatOpenAI")',
                limit=10,
            ))
            print(f"\n  同 project 最近 ChatOpenAI runs (共 {len(recent_runs)}):")
            for rr in recent_runs:
                match = "← MATCH" if str(rr.id) == run_id_a else ""
                print(f"    - {rr.id}: tokens={rr.total_tokens}, cost={rr.total_cost} {match}")
    except Exception as e:
        print(f"  list recent runs failed: {e}")

    # ---- 查场景 B ----
    print(f"\n{'='*60}")
    print(f"场景 B: LangGraph interrupt/resume (run_id={run_id_b})")
    print(f"{'='*60}")

    try:
        run_b = client.read_run(run_id=run_id_b)
        print(f"  read_run: name={run_b.name}, status={run_b.status}")
        print(f"  total_cost={run_b.total_cost}, total_tokens={run_b.total_tokens}")
        print(f"  child_run_ids={run_b.child_run_ids}")
    except Exception as e:
        print(f"  read_run FAILED: {e}")

    # 查 child runs
    try:
        children_b = list(client.list_runs(
            parent_run_id=run_id_b,
        ))
        print(f"  child runs: {len(children_b)}")
        total_child_tokens = 0
        for c in children_b[:10]:
            total_child_tokens += (c.total_tokens or 0)
            print(f"    - {c.id}: name={c.name}, tokens={c.total_tokens}, cost={c.total_cost}")
        print(f"  child runs total tokens: {total_child_tokens}")
    except Exception as e:
        print(f"  list child runs failed: {e}")

    # 用 trace_id 查（LangGraph 可能用不同的 trace）
    try:
        trace_runs = list(client.list_runs(
            trace_id=run_id_b,
        ))
        print(f"\n  trace_id={run_id_b} 下的所有 runs: {len(trace_runs)}")
        for tr in trace_runs[:10]:
            is_root = "ROOT" if tr.parent_run_id is None else f"child of {tr.parent_run_id}"
            print(f"    - {tr.id}: name={tr.name}, tokens={tr.total_tokens}, {is_root}")
    except Exception as e:
        print(f"  list trace runs failed: {e}")

    # 看有没有第二个 root run（resume 可能创建新的 root）
    try:
        # 按 metadata 中的 thread_id 查
        thread_runs = list(client.list_runs(
            project_name=run_b.session_name if hasattr(run_b, 'session_name') else "default",
            filter=f'has(metadata, {{"thread_id": "{thread_id_b}"}})',
            limit=10,
        ))
        print(f"\n  同 thread_id 的所有 runs: {len(thread_runs)}")
        for tr in thread_runs[:10]:
            is_root = "ROOT" if tr.parent_run_id is None else "child"
            print(f"    - {tr.id}: name={tr.name}, tokens={tr.total_tokens}, {is_root}")
    except Exception as e:
        print(f"  查 thread_id runs failed: {e}")


# ==============================================================================
# Test 5: on_tool_end — tool name 和参数
# ==============================================================================

@pytest.mark.asyncio
async def test_on_tool_end_details():
    """
    验证：on_tool_end 能拿到什么数据（tool name、参数、输出）。
    用一个简单的自定义 tool 测试。
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool
    from langgraph.prebuilt import create_react_agent

    @tool
    def get_weather(city: str) -> str:
        """Get the weather for a city."""
        return f"The weather in {city} is sunny, 25°C."

    @tool
    def calculate(expression: str) -> str:
        """Calculate a math expression."""
        try:
            return str(eval(expression))
        except:
            return "Error"

    cb = DetailedCallbackHandler()
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, stream_usage=True)

    agent = create_react_agent(llm, [get_weather, calculate])

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="What's the weather in Tokyo? Also calculate 42 * 17.")]},
        config={"callbacks": [cb]},
    )

    print("\n" + "="*80)
    print("on_tool_end 记录:")
    print("="*80)
    for r in cb.tool_end_records():
        print(json.dumps(r, indent=2, default=str, ensure_ascii=False))

    print("\n" + "="*80)
    print("on_llm_end 记录（agent 内部 LLM 调用）:")
    print("="*80)
    for r in cb.llm_end_records():
        um = r.get("usage_metadata", {})
        model = r.get("model_paths", {}).get("response_metadata.model_name", "?")
        in_t = um.get("input_tokens", 0) if um else 0
        out_t = um.get("output_tokens", 0) if um else 0
        print(f"  model={model}, in={in_t}, out={out_t}")

    print("\n" + "="*80)
    print("tool_start 记录（有 serialized info）:")
    print("="*80)
    for r in cb.records:
        if r["event"] == "tool_start":
            print(json.dumps(r, indent=2, default=str, ensure_ascii=False))

    # 验证
    tool_ends = cb.tool_end_records()
    assert len(tool_ends) >= 1, "至少应有 1 个 tool_end"
    print(f"\n✅ on_tool_end 共 {len(tool_ends)} 次")
    for te in tool_ends:
        name = te.get("tool_name_from_output") or te.get("tool_name_from_kwargs", "unknown")
        print(f"   tool: {name}")
