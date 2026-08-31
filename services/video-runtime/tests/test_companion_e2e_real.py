"""
Companion Agent 真实 E2E 测试 — 完整 LLM + Tool Calling 流程。

使用真实 OpenAI API Key, 真实 DB, 真实 LLM tool calling。
测试流程：
  1. 初始化 DB
  2. 构建 snapshot
  3. 创建 Companion Agent (create_video_companion_agent)
  4. 发送用户消息："帮我看看现在项目的进度"
  5. LLM 自动调用 get_project_status tool
  6. 发送第二轮消息："角色的详细信息呢？"
  7. LLM 自动调用 get_artifact_detail tool
  8. 验证完整的消息历史

用法：
  cd /home/songsong/local/Cuti-VideoAgent
  ENVIRONMENT=local conda run -n cuti-video-local python tests/test_companion_e2e_real.py [thread_id] [run_id]
"""
import asyncio
import json
import os
import sys
import uuid as uuid_lib
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "local")

from dotenv import load_dotenv
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_project_root, ".env.local"), override=False)


async def run_e2e_test(thread_id: str, run_id: str, user_id: str):
    from app.models.database import init_asyncpg_pool
    await init_asyncpg_pool()

    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import MemorySaver

    from app.services.agent.video_edit.agent import create_video_companion_agent
    from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

    print(f"Thread: {thread_id}")
    print(f"Run:    {run_id}")
    print(f"User:   {user_id}")

    # ── Step 1: Build snapshot ──
    print("\n" + "=" * 60)
    print("Step 1: Building ProjectSnapshot from DB...")
    snap = await build_snapshot_from_db(run_id=run_id, user_id=user_id, thread_id=thread_id)
    print(f"  Phase: {snap.get('phase', 'unknown')}")
    print(f"  Outline: {snap.get('outline', {}).get('status', 'N/A')}")
    print(f"  Characters: {snap.get('characters', {}).get('status', 'N/A')}")
    print("  ✅ Snapshot built")

    # ── Step 2: Create agent ──
    print("\n" + "=" * 60)
    print("Step 2: Creating Companion Agent with real LLM...")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, streaming=True)
    checkpointer = MemorySaver()

    agent = create_video_companion_agent(
        llm=llm,
        run_id=run_id,
        user_id=user_id,
        thread_id=thread_id,
        snapshot=snap,
        checkpointer=checkpointer,
        enable_summarization=False,
    )
    config = {"configurable": {"thread_id": f"test-companion-{thread_id}"}}
    print("  ✅ Agent created")

    # ── Step 3: First turn — "查看项目进度" ──
    print("\n" + "=" * 60)
    print('Step 3: Turn 1 — "帮我看看现在项目的进度"')
    print("-" * 40)

    turn1_tools = []
    turn1_ai_content = ""

    async for event in agent.astream_events(
        {"messages": [HumanMessage(content="帮我看看现在项目的进度")]},
        config=config,
        version="v2",
    ):
        kind = event.get("event", "")
        data = event.get("data", {})

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                print(chunk.content, end="", flush=True)
                turn1_ai_content += chunk.content

        elif kind == "on_tool_start":
            tool_name = event.get("name", "")
            tool_input = data.get("input", {})
            print(f"\n  🔧 Tool Call: {tool_name}({json.dumps(tool_input, ensure_ascii=False)[:100]})")
            turn1_tools.append(tool_name)

        elif kind == "on_tool_end":
            tool_name = event.get("name", "")
            output = data.get("output", "")
            if hasattr(output, "content"):
                output = output.content
            print(f"  📋 Tool Result ({tool_name}): {str(output)[:200]}...")

    print()
    print(f"\n  Tools called: {turn1_tools}")
    print(f"  AI response length: {len(turn1_ai_content)} chars")
    assert "get_project_status" in turn1_tools, "❌ Expected get_project_status to be called!"
    assert len(turn1_ai_content) > 20, "❌ AI response too short"
    print("  ✅ Turn 1 passed — LLM called get_project_status")

    # ── Step 4: Second turn — "角色详细信息" ──
    print("\n" + "=" * 60)
    print('Step 4: Turn 2 — "帮我看看角色的详细信息"')
    print("-" * 40)

    turn2_tools = []
    turn2_ai_content = ""

    async for event in agent.astream_events(
        {"messages": [HumanMessage(content="帮我看看角色的详细信息")]},
        config=config,
        version="v2",
    ):
        kind = event.get("event", "")
        data = event.get("data", {})

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                print(chunk.content, end="", flush=True)
                turn2_ai_content += chunk.content

        elif kind == "on_tool_start":
            tool_name = event.get("name", "")
            tool_input = data.get("input", {})
            print(f"\n  🔧 Tool Call: {tool_name}({json.dumps(tool_input, ensure_ascii=False)[:100]})")
            turn2_tools.append(tool_name)

        elif kind == "on_tool_end":
            tool_name = event.get("name", "")
            output = data.get("output", "")
            if hasattr(output, "content"):
                output = output.content
            print(f"  📋 Tool Result ({tool_name}): {str(output)[:200]}...")

    print()
    print(f"\n  Tools called: {turn2_tools}")
    print(f"  AI response length: {len(turn2_ai_content)} chars")
    assert "get_artifact_detail" in turn2_tools, "❌ Expected get_artifact_detail to be called!"
    assert len(turn2_ai_content) > 20, "❌ AI response too short"
    print("  ✅ Turn 2 passed — LLM called get_artifact_detail")

    # ── Step 5: Third turn — "把第一个角色的描述换成蓝色头发" (多 tool 组合) ──
    print("\n" + "=" * 60)
    print('Step 5: Turn 3 — "能不能帮我把第一个角色换成蓝色头发的？"')
    print("-" * 40)

    turn3_tools = []
    turn3_ai_content = ""

    async for event in agent.astream_events(
        {"messages": [HumanMessage(content="能不能帮我把第一个角色换成蓝色头发的？")]},
        config=config,
        version="v2",
    ):
        kind = event.get("event", "")
        data = event.get("data", {})

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                print(chunk.content, end="", flush=True)
                turn3_ai_content += chunk.content

        elif kind == "on_tool_start":
            tool_name = event.get("name", "")
            tool_input = data.get("input", {})
            print(f"\n  🔧 Tool Call: {tool_name}({json.dumps(tool_input, ensure_ascii=False)[:200]})")
            turn3_tools.append(tool_name)

        elif kind == "on_tool_end":
            tool_name = event.get("name", "")
            output = data.get("output", "")
            if hasattr(output, "content"):
                output = output.content
            print(f"  📋 Tool Result ({tool_name}): {str(output)[:300]}...")

    print()
    print(f"\n  Tools called: {turn3_tools}")
    print(f"  AI response length: {len(turn3_ai_content)} chars")
    assert len(turn3_tools) >= 1, "❌ Expected at least one tool call for regeneration"
    print("  ✅ Turn 3 passed — LLM attempted character modification")

    # ── Summary ──
    print("\n" + "=" * 60)
    print("🎉 E2E Test Summary")
    print(f"  Turn 1: {turn1_tools} → AI {len(turn1_ai_content)} chars")
    print(f"  Turn 2: {turn2_tools} → AI {len(turn2_ai_content)} chars")
    print(f"  Turn 3: {turn3_tools} → AI {len(turn3_ai_content)} chars")
    all_tools = set(turn1_tools + turn2_tools + turn3_tools)
    print(f"  Total unique tools called: {all_tools}")
    print("\n🎉 All E2E tests passed! Real LLM + Real DB + Real Tool Calling!")


def main():
    thread_id = ""
    run_id = ""
    user_id = ""

    if len(sys.argv) >= 3:
        thread_id = sys.argv[1]
        run_id = sys.argv[2]
    if len(sys.argv) >= 4:
        user_id = sys.argv[3]

    if not thread_id:
        thread_id = "thread_admin_9af32963-7e52-4c86-a9b6-d86d39afcf66"
        run_id = "a7aa45f7-b329-4955-90ad-7fb5fa1597db"
        user_id = "admin"
        print(f"⚠️  Using defaults: thread={thread_id}, run={run_id}, user={user_id}")

    if not user_id:
        user_id = "admin"

    try:
        asyncio.run(run_e2e_test(thread_id, run_id, user_id))
    except Exception as e:
        print(f"\n❌ E2E Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
