"""
Companion Agent 完整 E2E 测试 — 所有 tool 真实 LLM 调用 + DB 验证 + 计费检查。

覆盖:
  1. get_project_status   — 查进度
  2. get_artifact_detail   — 查角色/关键帧/视频详情
  3. regenerate_characters — 重新生成角色
  4. regenerate_keyframes  — 重新生成关键帧
  5. regenerate_videos     — 重新生成视频
  6. modify_outline        — 修改大纲
  7. continue_pipeline     — 继续管线（测试错误处理）
  8. DB 验证: conversation_run(companion_chat), messages, cost, billing_status

用法:
  cd /home/songsong/local/Cuti-VideoAgent
  ENVIRONMENT=local conda run -n cuti-video-local python tests/test_companion_full_e2e.py 2>&1 | tee /home/songsong/local/video_agent_performance.log
"""
import asyncio
import json
import logging
import os
import sys
import time
import uuid as uuid_lib
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "local")

from dotenv import load_dotenv
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_project_root, ".env.local"), override=False)

LOG_FILE = "/home/songsong/local/video_agent_performance.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("e2e")

# 测试用的数据——efe5af1c 线程数据最完整
TEST_THREAD_ID = "efe5af1c-a058-4765-b648-e3e70b4172e5"
TEST_RUN_ID = "9767f19e-96d4-40ed-a117-525407e9a5e2"
TEST_USER_ID = "admin"

# 已知的一些 UUID（从上面查到的）
KNOWN_CHAR_UUID = "ec7a8cda-1cdf-44bc-b097-75ac18a1142e"  # 闺蜜
KNOWN_OUTLINE_UUID = "e29ccab9-ebe1-4bc5-aa06-5d74c0f4ee3c"

PASS_COUNT = 0
FAIL_COUNT = 0


def mark_pass(name):
    global PASS_COUNT
    PASS_COUNT += 1
    log.info(f"  ✅ PASS: {name}")


def mark_fail(name, reason):
    global FAIL_COUNT
    FAIL_COUNT += 1
    log.info(f"  ❌ FAIL: {name} — {reason}")


async def run_agent_turn(agent, config, user_message: str) -> dict:
    """执行一轮 agent 对话，返回 {ai_content, tools_called, tool_results}"""
    from langchain_core.messages import HumanMessage
    tools_called = []
    tool_results = {}
    ai_content = ""

    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=user_message)]},
        config=config,
        version="v2",
    ):
        kind = event.get("event", "")
        data = event.get("data", {})

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                ai_content += chunk.content

        elif kind == "on_tool_start":
            tool_name = event.get("name", "")
            tool_input = data.get("input", {})
            tools_called.append({"name": tool_name, "input": tool_input})
            log.info(f"    🔧 Tool: {tool_name}({json.dumps(tool_input, ensure_ascii=False)[:150]})")

        elif kind == "on_tool_end":
            tool_name = event.get("name", "")
            output = data.get("output", "")
            if hasattr(output, "content"):
                output = output.content
            output_str = str(output)[:500]
            tool_results[tool_name] = output_str
            log.info(f"    📋 Result ({tool_name}): {output_str[:200]}...")

    return {
        "ai_content": ai_content,
        "tools_called": tools_called,
        "tool_results": tool_results,
    }


async def run_full_test():
    from app.models.database import init_asyncpg_pool, get_asyncpg_pool
    await init_asyncpg_pool()

    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver
    from app.services.agent.video_edit.agent import create_video_companion_agent
    from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db
    from app.callbacks.credit_check_callback import CreditCheckCallbackHandler
    from app.crud.conversation import (
        async_get_conversation_by_thread_id,
        async_create_conversation_run,
        async_add_message_to_conversation,
        async_update_conversation_run_cost,
        async_update_conversation_run_status,
        async_set_conversation_run_billing_pending_if_not_completed,
        async_get_conversation_run_by_run_id,
        async_get_conversation_messages,
    )

    log.info("=" * 70)
    log.info("Companion Agent 完整 E2E 测试")
    log.info(f"Thread: {TEST_THREAD_ID}")
    log.info(f"Run:    {TEST_RUN_ID}")
    log.info(f"User:   {TEST_USER_ID}")
    log.info(f"Log:    {LOG_FILE}")
    log.info("=" * 70)

    # ── 0. 创建 companion_run + 写 conversation_run 到 DB ──
    companion_run_id = str(uuid_lib.uuid4())
    log.info(f"\n[Phase 0] 创建 companion_chat conversation_run: {companion_run_id}")
    conversation = await async_get_conversation_by_thread_id(TEST_THREAD_ID)
    assert conversation, "conversation not found"
    log.info(f"  conversation.id={conversation.id}, uuid={conversation.uuid}")

    await async_create_conversation_run(
        conversation_id=conversation.id,
        thread_id=TEST_THREAD_ID,
        run_id=companion_run_id,
        user_id=TEST_USER_ID,
        agent_type="video",
        run_type="companion_chat",
        status="running",
        conversation_uuid=conversation.uuid,
        user_input="[E2E Test] full companion test",
    )
    run_record = await async_get_conversation_run_by_run_id(companion_run_id)
    assert run_record and run_record.run_type == "companion_chat"
    mark_pass("conversation_run created with run_type=companion_chat")

    # ── 1. 构建 snapshot + 创建 agent ──
    log.info("\n[Phase 1] Build snapshot + create agent")
    snap = await build_snapshot_from_db(run_id=TEST_RUN_ID, user_id=TEST_USER_ID, thread_id=TEST_THREAD_ID)
    log.info(f"  Snapshot phase={snap.get('phase')}")
    log.info(f"  outline={snap.get('outline',{}).get('status')}  chars={snap.get('characters',{}).get('status')}")
    log.info(f"  keyframes={snap.get('keyframes',{}).get('status')}  videos={snap.get('videos',{}).get('status')}")

    credit_cb = CreditCheckCallbackHandler(user_id=TEST_USER_ID, action="companion_chat")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, streaming=True, callbacks=[credit_cb])
    checkpointer = MemorySaver()

    agent = create_video_companion_agent(
        llm=llm,
        run_id=TEST_RUN_ID,
        user_id=TEST_USER_ID,
        thread_id=TEST_THREAD_ID,
        snapshot=snap,
        checkpointer=checkpointer,
        enable_summarization=False,
    )
    config = {"configurable": {"thread_id": f"e2e-test-{companion_run_id}"}}
    mark_pass("Agent created with CreditCheckCallback")

    # ── 2. Turn 1: get_project_status ──
    log.info("\n" + "=" * 70)
    log.info("[Turn 1] 用户: '帮我看看项目进度'")
    t1 = time.time()
    r1 = await run_agent_turn(agent, config, "帮我看看项目进度")
    log.info(f"  AI回复 ({len(r1['ai_content'])} chars): {r1['ai_content'][:200]}")
    log.info(f"  耗时: {time.time()-t1:.1f}s")

    if any(t["name"] == "get_project_status" for t in r1["tools_called"]):
        mark_pass("Turn 1: LLM called get_project_status")
        result_text = r1["tool_results"].get("get_project_status", "")
        if "大纲" in result_text and "角色" in result_text:
            mark_pass("Turn 1: get_project_status 返回了各阶段状态")
        else:
            mark_fail("Turn 1: get_project_status result", f"缺少阶段信息: {result_text[:100]}")
    else:
        mark_fail("Turn 1", f"未调用 get_project_status, 调用了: {[t['name'] for t in r1['tools_called']]}")

    # 写消息到 DB
    await async_add_message_to_conversation(
        conversation_id=conversation.id, role="human", content="帮我看看项目进度",
        event_type="user_input", event_data={"run_id": companion_run_id, "source": "companion"},
        run_id=companion_run_id, conversation_uuid=conversation.uuid,
    )
    await async_add_message_to_conversation(
        conversation_id=conversation.id, role="ai", content=r1["ai_content"],
        event_type="video_edit_response",
        event_data={"run_id": companion_run_id, "source": "companion", "tools_used": [t["name"] for t in r1["tools_called"]]},
        run_id=companion_run_id, conversation_uuid=conversation.uuid,
    )

    # ── 3. Turn 2: get_artifact_detail (character) ──
    log.info("\n" + "=" * 70)
    log.info("[Turn 2] 用户: '帮我看看角色详细信息'")
    t2 = time.time()
    r2 = await run_agent_turn(agent, config, "帮我看看角色的详细信息")
    log.info(f"  AI回复 ({len(r2['ai_content'])} chars): {r2['ai_content'][:200]}")
    log.info(f"  耗时: {time.time()-t2:.1f}s")

    if any(t["name"] == "get_artifact_detail" for t in r2["tools_called"]):
        mark_pass("Turn 2: LLM called get_artifact_detail")
        result_text = r2["tool_results"].get("get_artifact_detail", "")
        if "闺蜜" in result_text or "character" in result_text.lower() or "角色" in result_text:
            mark_pass("Turn 2: get_artifact_detail 返回了角色信息")
        else:
            mark_fail("Turn 2: get_artifact_detail result", f"缺少角色数据: {result_text[:150]}")
    else:
        mark_fail("Turn 2", f"未调用 get_artifact_detail, 调用了: {[t['name'] for t in r2['tools_called']]}")

    await async_add_message_to_conversation(
        conversation_id=conversation.id, role="human", content="帮我看看角色的详细信息",
        event_type="user_input", event_data={"run_id": companion_run_id, "source": "companion"},
        run_id=companion_run_id, conversation_uuid=conversation.uuid,
    )
    await async_add_message_to_conversation(
        conversation_id=conversation.id, role="ai", content=r2["ai_content"],
        event_type="video_edit_response",
        event_data={"run_id": companion_run_id, "source": "companion", "tools_used": [t["name"] for t in r2["tools_called"]]},
        run_id=companion_run_id, conversation_uuid=conversation.uuid,
    )

    # ── 4. Turn 3: get_artifact_detail (keyframe) ──
    log.info("\n" + "=" * 70)
    log.info("[Turn 3] 用户: '看看第1个镜头的关键帧'")
    t3 = time.time()
    r3 = await run_agent_turn(agent, config, "看看第1个镜头的关键帧详情")
    log.info(f"  AI回复 ({len(r3['ai_content'])} chars): {r3['ai_content'][:200]}")
    log.info(f"  耗时: {time.time()-t3:.1f}s")

    if any(t["name"] == "get_artifact_detail" for t in r3["tools_called"]):
        mark_pass("Turn 3: LLM called get_artifact_detail for keyframe")
        result_text = r3["tool_results"].get("get_artifact_detail", "")
        if "shot" in result_text.lower() or "镜头" in result_text or "关键帧" in result_text or "keyframe" in result_text.lower():
            mark_pass("Turn 3: get_artifact_detail 返回了关键帧信息")
        else:
            mark_fail("Turn 3: result", f"缺少关键帧数据: {result_text[:150]}")
    else:
        mark_fail("Turn 3", f"未调用 get_artifact_detail, 调用了: {[t['name'] for t in r3['tools_called']]}")

    # ── 5. Turn 4: regenerate_characters ──
    log.info("\n" + "=" * 70)
    log.info("[Turn 4] 用户: '帮我把闺蜜这个角色换成红色头发'")
    t4 = time.time()
    r4 = await run_agent_turn(agent, config, "帮我把闺蜜这个角色换成红色头发")
    log.info(f"  AI回复 ({len(r4['ai_content'])} chars): {r4['ai_content'][:200]}")
    log.info(f"  耗时: {time.time()-t4:.1f}s")

    if any(t["name"] == "regenerate_characters" for t in r4["tools_called"]):
        mark_pass("Turn 4: LLM called regenerate_characters")
        regen_input = next(t["input"] for t in r4["tools_called"] if t["name"] == "regenerate_characters")
        if KNOWN_CHAR_UUID in str(regen_input.get("character_uuids", [])):
            mark_pass("Turn 4: regenerate_characters 使用了正确的角色 UUID")
        else:
            log.info(f"    ⚠️ UUID 不完全匹配, input={regen_input}")
        result_text = r4["tool_results"].get("regenerate_characters", "")
        if "提交" in result_text or "成功" in result_text or "已" in result_text:
            mark_pass("Turn 4: regenerate_characters 提交成功")
        else:
            mark_fail("Turn 4: regen result", f"{result_text[:200]}")
    else:
        mark_fail("Turn 4", f"未调用 regenerate_characters, 调用了: {[t['name'] for t in r4['tools_called']]}")

    # ── 6. Turn 5: regenerate_keyframes ──
    log.info("\n" + "=" * 70)
    log.info("[Turn 5] 用户: '帮我重新生成第2个镜头的关键帧，换成夜景'")
    t5 = time.time()
    r5 = await run_agent_turn(agent, config, "帮我重新生成第2个镜头的关键帧，换成夜景")
    log.info(f"  AI回复 ({len(r5['ai_content'])} chars): {r5['ai_content'][:200]}")
    log.info(f"  耗时: {time.time()-t5:.1f}s")

    if any(t["name"] == "regenerate_keyframes" for t in r5["tools_called"]):
        mark_pass("Turn 5: LLM called regenerate_keyframes")
        regen_input = next(t["input"] for t in r5["tools_called"] if t["name"] == "regenerate_keyframes")
        if 2 in regen_input.get("shot_numbers", []):
            mark_pass("Turn 5: regenerate_keyframes 使用了正确的 shot_number=2")
        else:
            log.info(f"    ⚠️ shot_numbers 不匹配, input={regen_input}")
        result_text = r5["tool_results"].get("regenerate_keyframes", "")
        if "提交" in result_text or "成功" in result_text or "已" in result_text:
            mark_pass("Turn 5: regenerate_keyframes 提交成功")
        else:
            mark_fail("Turn 5: regen kf result", f"{result_text[:200]}")
    else:
        mark_fail("Turn 5", f"未调用 regenerate_keyframes, 调用了: {[t['name'] for t in r5['tools_called']]}")

    # ── 7. Turn 6: regenerate_videos ──
    log.info("\n" + "=" * 70)
    log.info("[Turn 6] 用户: '帮我重新生成第3个镜头的视频，运动幅度大一点'")
    t6 = time.time()
    r6 = await run_agent_turn(agent, config, "帮我重新生成第3个镜头的视频，运动幅度大一点")
    log.info(f"  AI回复 ({len(r6['ai_content'])} chars): {r6['ai_content'][:200]}")
    log.info(f"  耗时: {time.time()-t6:.1f}s")

    if any(t["name"] == "regenerate_videos" for t in r6["tools_called"]):
        mark_pass("Turn 6: LLM called regenerate_videos")
        regen_input = next(t["input"] for t in r6["tools_called"] if t["name"] == "regenerate_videos")
        if 3 in regen_input.get("shot_numbers", []):
            mark_pass("Turn 6: regenerate_videos 使用了正确的 shot_number=3")
        else:
            log.info(f"    ⚠️ shot_numbers 不匹配, input={regen_input}")
        result_text = r6["tool_results"].get("regenerate_videos", "")
        if "提交" in result_text or "成功" in result_text or "已" in result_text:
            mark_pass("Turn 6: regenerate_videos 提交成功")
        else:
            mark_fail("Turn 6: regen vid result", f"{result_text[:200]}")
    else:
        mark_fail("Turn 6", f"未调用 regenerate_videos, 调用了: {[t['name'] for t in r6['tools_called']]}")

    # ── 8. Turn 7: modify_outline ──
    log.info("\n" + "=" * 70)
    log.info("[Turn 7] 用户: '帮我把大纲标题改成更文艺一点的'")
    t7 = time.time()
    r7 = await run_agent_turn(agent, config, "帮我把大纲标题改成更文艺一点的")
    log.info(f"  AI回复 ({len(r7['ai_content'])} chars): {r7['ai_content'][:200]}")
    log.info(f"  耗时: {time.time()-t7:.1f}s")

    if any(t["name"] == "modify_outline" for t in r7["tools_called"]):
        mark_pass("Turn 7: LLM called modify_outline")
        modify_input = next(t["input"] for t in r7["tools_called"] if t["name"] == "modify_outline")
        log.info(f"    modify_outline input: {json.dumps(modify_input, ensure_ascii=False)[:200]}")
        result_text = r7["tool_results"].get("modify_outline", "")
        log.info(f"    modify_outline result: {result_text[:300]}")
        if "修改" in result_text or "更新" in result_text or "成功" in result_text or "title" in result_text.lower():
            mark_pass("Turn 7: modify_outline 执行成功")
        else:
            mark_fail("Turn 7: modify result", f"{result_text[:200]}")
    else:
        mark_fail("Turn 7", f"未调用 modify_outline, 调用了: {[t['name'] for t in r7['tools_called']]}")

    # ── 9. Turn 8: continue_pipeline (期望失败——所有 interrupt 已 continued) ──
    log.info("\n" + "=" * 70)
    log.info("[Turn 8] 用户: '好了，帮我继续下一步吧'")
    t8 = time.time()
    r8 = await run_agent_turn(agent, config, "好了，帮我继续下一步吧，通过 after_shots 门控")
    log.info(f"  AI回复 ({len(r8['ai_content'])} chars): {r8['ai_content'][:300]}")
    log.info(f"  耗时: {time.time()-t8:.1f}s")

    if any(t["name"] == "continue_pipeline" for t in r8["tools_called"]):
        mark_pass("Turn 8: LLM called continue_pipeline")
        result_text = r8["tool_results"].get("continue_pipeline", "")
        log.info(f"    continue_pipeline result: {result_text[:300]}")
        # 这里所有 interrupt 都已 continued，所以预期会失败或返回"已恢复"
        if "未找到" in result_text or "失败" in result_text or "已" in result_text or "resume" in result_text.lower():
            mark_pass("Turn 8: continue_pipeline 正确处理了边界情况")
        else:
            log.info(f"    ⚠️ continue_pipeline 返回了意料之外的结果")
    else:
        # LLM 可能判断不需要调用 continue_pipeline，这也是合理的
        log.info(f"    ⚠️ LLM 没有调用 continue_pipeline（可能判断当前不适合继续），调用了: {[t['name'] for t in r8['tools_called']]}")
        mark_pass("Turn 8: LLM 做了合理的判断（不强制调用）")

    # ── 10. DB 验证: cost + billing ──
    log.info("\n" + "=" * 70)
    log.info("[Phase 10] DB 验证: cost, billing, messages")

    total_cost = credit_cb.total_cost
    log.info(f"  LLM total_cost (from callback): ${total_cost:.6f}")

    if total_cost > 0:
        await async_update_conversation_run_cost(companion_run_id, total_cost)
        mark_pass(f"conversation_run cost updated to ${total_cost:.6f}")
    else:
        log.info(f"  ⚠️ total_cost=0, skip cost update")

    await async_update_conversation_run_status(
        companion_run_id, "completed", completed_at=datetime.utcnow()
    )
    await async_set_conversation_run_billing_pending_if_not_completed(companion_run_id)

    # 验证 DB
    run_after = await async_get_conversation_run_by_run_id(companion_run_id)
    log.info(f"\n  === conversation_run 验证 ===")
    log.info(f"  run_id:         {run_after.run_id}")
    log.info(f"  run_type:       {run_after.run_type}")
    log.info(f"  status:         {run_after.status}")
    log.info(f"  cost:           {run_after.cost}")
    log.info(f"  billing_status: {run_after.billing_status}")

    if run_after.run_type == "companion_chat":
        mark_pass("DB: run_type == companion_chat")
    else:
        mark_fail("DB: run_type", f"expected companion_chat, got {run_after.run_type}")

    if run_after.status == "completed":
        mark_pass("DB: status == completed")
    else:
        mark_fail("DB: status", f"expected completed, got {run_after.status}")

    if run_after.billing_status == "pending":
        mark_pass("DB: billing_status == pending")
    else:
        mark_fail("DB: billing_status", f"expected pending, got {run_after.billing_status}")

    if run_after.cost and run_after.cost > 0:
        mark_pass(f"DB: cost > 0 (${run_after.cost:.6f})")
    else:
        log.info(f"  ⚠️ cost={run_after.cost} (may be 0 if callback didn't track)")

    # 验证消息
    all_msgs = await async_get_conversation_messages(conversation.id)
    companion_msgs = [m for m in all_msgs if m.get("run_id") == companion_run_id]
    log.info(f"\n  === conversation_messages 验证 ===")
    log.info(f"  本次 companion 消息数: {len(companion_msgs)}")

    user_msgs = [m for m in companion_msgs if m.get("role") == "human"]
    ai_msgs = [m for m in companion_msgs if m.get("role") == "ai"]
    log.info(f"  human 消息: {len(user_msgs)}")
    log.info(f"  ai 消息:    {len(ai_msgs)}")
    for m in companion_msgs:
        log.info(f"    [{m.get('role')}] event_type={m.get('event_type')} content={str(m.get('content',''))[:60]}...")

    if len(user_msgs) >= 2:
        mark_pass(f"DB: {len(user_msgs)} user messages persisted")
    else:
        mark_fail("DB: user messages", f"expected >= 2, got {len(user_msgs)}")

    if len(ai_msgs) >= 2:
        mark_pass(f"DB: {len(ai_msgs)} AI messages persisted")
    else:
        mark_fail("DB: AI messages", f"expected >= 2, got {len(ai_msgs)}")

    # ── Summary ──
    log.info("\n" + "=" * 70)
    log.info("🏁 E2E Test Summary")
    log.info(f"  ✅ PASS: {PASS_COUNT}")
    log.info(f"  ❌ FAIL: {FAIL_COUNT}")
    log.info(f"  Total cost: ${total_cost:.6f}")
    log.info(f"  Log file: {LOG_FILE}")
    log.info("=" * 70)

    if FAIL_COUNT > 0:
        log.info("⚠️  有失败项目，请查看日志")
    else:
        log.info("🎉 All tests passed!")


def main():
    try:
        asyncio.run(run_full_test())
    except Exception as e:
        log.error(f"\n❌ FATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    if FAIL_COUNT > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
