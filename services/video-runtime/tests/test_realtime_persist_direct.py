"""直接代码调用测试 companion 消息实时持久化。
不走 HTTP，直接调用 companion agent 并验证 DB 中的消息。
"""
import asyncio
import json
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

THREAD_ID = "a3a96f37-b7bf-48d8-8729-1e3627b1c10b"


async def count_ai_messages_for_run(pool, run_id: str) -> dict:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT event_type, content FROM conversation_messages "
            "WHERE run_id = $1 AND role = 'ai' ORDER BY sequence",
            run_id,
        )
    result = {}
    for r in rows:
        et = r["event_type"]
        result.setdefault(et, [])
        result[et].append(r["content"][:80])
    return result


async def main():
    from app.models.database import init_asyncpg_pool, get_asyncpg_pool
    await init_asyncpg_pool()
    pool = get_asyncpg_pool()

    from app.crud.conversation import (
        async_get_conversation_by_thread_id,
        async_create_conversation_run,
        async_add_message_to_conversation,
        async_update_conversation_run_status,
    )
    from app.services.agent.video_edit.agent import create_video_companion_agent
    from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db
    from app.callbacks.credit_check_callback import CreditCheckCallbackHandler
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import MemorySaver
    from datetime import datetime

    conversation = await async_get_conversation_by_thread_id(THREAD_ID)
    if not conversation:
        logger.error("找不到对话")
        return

    user_id = "admin"
    run_id = "cb4366b7-06d7-451e-b794-da157fb0618e"
    companion_run_id = str(uuid.uuid4())
    message = "查看项目状态"

    logger.info(f"companion_run_id: {companion_run_id}")

    await async_create_conversation_run(
        conversation_id=conversation.id,
        thread_id=THREAD_ID,
        run_id=companion_run_id,
        user_id=user_id,
        agent_type="video",
        run_type="companion_chat",
        status="running",
        conversation_uuid=conversation.uuid,
        user_input=message,
    )

    await async_add_message_to_conversation(
        conversation_id=conversation.id,
        role="human",
        content=message,
        event_type="user_input",
        event_data={"run_id": companion_run_id, "source": "companion"},
        run_id=companion_run_id,
        conversation_uuid=conversation.uuid,
    )

    snap = await build_snapshot_from_db(run_id, user_id, THREAD_ID)
    credit_callback = CreditCheckCallbackHandler(user_id=user_id, action="companion_chat")

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, streaming=True, callbacks=[credit_callback])
    checkpointer = MemorySaver()

    agent = create_video_companion_agent(
        llm=llm,
        run_id=run_id,
        user_id=user_id,
        thread_id=THREAD_ID,
        snapshot=snap,
        checkpointer=checkpointer,
        enable_summarization=False,
    )

    config = {"configurable": {"thread_id": f"companion-{companion_run_id}"}}

    final_content = ""
    tool_records = []

    try:
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=message)]},
            config=config,
            version="v2",
        ):
            kind = event.get("event", "")
            data = event.get("data", {})

            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    final_content += chunk.content

            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                tool_input = data.get("input", {})
                tool_records.append({"tool": tool_name, "args": tool_input, "persisted": False})
                logger.info(f"🔧 tool_call: {tool_name}")

                # 实时持久化 tool_call
                await async_add_message_to_conversation(
                    conversation_id=conversation.id,
                    role="ai",
                    content=f"🔧 调用工具: {tool_name}",
                    event_type="video_edit_tool_call",
                    event_data={"run_id": companion_run_id, "source": "companion", "tool": tool_name, "args": tool_input},
                    run_id=companion_run_id,
                    conversation_uuid=conversation.uuid,
                )
                tool_records[-1]["persisted"] = True

                # 验证：立即查 DB
                db_check = await count_ai_messages_for_run(pool, companion_run_id)
                tc_count = len(db_check.get("video_edit_tool_call", []))
                logger.info(f"✅ DB 验证 (tool_call 后): video_edit_tool_call={tc_count}")

            elif kind == "on_tool_end":
                tool_name = event.get("name", "")
                output = data.get("output", "")
                if hasattr(output, "content"):
                    output = output.content
                output_str = str(output)[:2000]
                if tool_records and tool_records[-1]["tool"] == tool_name:
                    tool_records[-1]["result"] = output_str
                logger.info(f"📋 tool_result: {tool_name} -> {output_str[:80]}")

                # 实时持久化 tool_result
                await async_add_message_to_conversation(
                    conversation_id=conversation.id,
                    role="ai",
                    content=f"📋 {tool_name} 结果:\n{output_str[:500]}",
                    event_type="video_edit_tool_result",
                    event_data={"run_id": companion_run_id, "source": "companion", "tool": tool_name},
                    run_id=companion_run_id,
                    conversation_uuid=conversation.uuid,
                )

                # 验证
                db_check = await count_ai_messages_for_run(pool, companion_run_id)
                tr_count = len(db_check.get("video_edit_tool_result", []))
                logger.info(f"✅ DB 验证 (tool_result 后): video_edit_tool_result={tr_count}")

    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)

    # 最终：存 final_content + 更新 run status
    if final_content:
        await async_add_message_to_conversation(
            conversation_id=conversation.id,
            role="ai",
            content=final_content,
            event_type="video_edit_response",
            event_data={
                "run_id": companion_run_id,
                "source": "companion",
                "tools_used": [t["tool"] for t in tool_records],
            },
            run_id=companion_run_id,
            conversation_uuid=conversation.uuid,
        )

    await async_update_conversation_run_status(
        companion_run_id, "completed", completed_at=datetime.utcnow()
    )

    # 最终 DB 检查
    logger.info("\n=== 最终 DB 检查 ===")
    db_final = await count_ai_messages_for_run(pool, companion_run_id)
    for et, msgs in db_final.items():
        logger.info(f"  {et}: {len(msgs)} 条")
        for m in msgs:
            logger.info(f"    -> {m}")

    total = sum(len(v) for v in db_final.values())
    logger.info(f"\n总计: {total} 条 AI 消息 (run_id={companion_run_id})")

    if db_final.get("video_edit_tool_call") and db_final.get("video_edit_tool_result"):
        logger.info("✅ 实时持久化验证通过！tool_call 和 tool_result 都已在流式过程中写入 DB")
    else:
        logger.error("❌ 实时持久化验证失败")


if __name__ == "__main__":
    asyncio.run(main())
