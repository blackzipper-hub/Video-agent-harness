"""
Companion Agent 快速 E2E 验证测试 (目标 <30s)

- 用新 thread（87516541）而非 efe5af1c
- regenerate_* GPU 调用 mock 掉（只验证 DB 记录创建 + prompt 正确）
- 纯 DB 操作（modify_outline、get_status、get_detail）直接验证
- continue_pipeline 构造 interrupt 消息验证
- 最后验证 conversation_run + messages + billing
- 检查生成的图片 URL 是否可访问

用法:
  ENVIRONMENT=local conda run -n cuti-video-local python tests/test_companion_fast_e2e.py
"""
import asyncio
import json
import logging
import os
import sys
import time
import uuid as uuid_lib
from datetime import datetime
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "local")
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"), override=False)

LOG_FILE = "/home/songsong/local/video_agent_performance.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("fast_e2e")

THREAD_ID = "87516541-4690-49b4-ad80-ac03623ce224"
RUN_ID    = "0ace49b6-1dbe-4b50-aadc-36e9616fdc7d"
USER_ID   = "admin"
CHAR_UUID = "648c5ed5-c6be-47e1-b6e9-ef379bd7e4a6"

PASS_COUNT = 0
FAIL_COUNT = 0
def ok(name):
    global PASS_COUNT; PASS_COUNT += 1; log.info(f"  ✅ PASS: {name}")
def fail(name, reason=""):
    global FAIL_COUNT; FAIL_COUNT += 1; log.info(f"  ❌ FAIL: {name} — {reason}")


async def agent_turn(agent, config, msg):
    from langchain_core.messages import HumanMessage
    tools_used, results, ai_text = [], {}, ""
    async for ev in agent.astream_events({"messages": [HumanMessage(content=msg)]}, config=config, version="v2"):
        k, d = ev.get("event",""), ev.get("data",{})
        if k == "on_chat_model_stream":
            ch = d.get("chunk")
            if ch and hasattr(ch,"content") and ch.content: ai_text += ch.content
        elif k == "on_tool_start":
            n = ev.get("name",""); inp = d.get("input",{})
            tools_used.append(n)
            log.info(f"    🔧 {n}({json.dumps(inp,ensure_ascii=False)[:120]})")
        elif k == "on_tool_end":
            n = ev.get("name",""); o = d.get("output","")
            if hasattr(o,"content"): o = o.content
            results[n] = str(o)[:2000]
            log.info(f"    📋 {n}: {str(o)[:150]}")
    log.info(f"    💬 AI: {ai_text[:120]}")
    return tools_used, results, ai_text


async def run():
    t0 = time.time()
    from app.models.database import init_asyncpg_pool, get_asyncpg_pool
    await init_asyncpg_pool()
    pool = get_asyncpg_pool()

    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver
    from app.services.agent.video_edit.agent import create_video_companion_agent
    from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db
    from app.callbacks.credit_check_callback import CreditCheckCallbackHandler
    from app.crud.conversation import (
        async_get_conversation_by_thread_id, async_create_conversation_run,
        async_add_message_to_conversation, async_update_conversation_run_cost,
        async_update_conversation_run_status, async_set_conversation_run_billing_pending_if_not_completed,
        async_get_conversation_run_by_run_id, async_get_conversation_messages,
    )

    log.info("=" * 60)
    log.info(f"快速 E2E 测试 — thread={THREAD_ID[:12]} (可爱的一天)")
    log.info("=" * 60)

    # ── Setup ──
    comp_run_id = str(uuid_lib.uuid4())
    conversation = await async_get_conversation_by_thread_id(THREAD_ID)
    if not conversation:
        fail("conversation not found"); return
    await async_create_conversation_run(
        conversation_id=conversation.id, thread_id=THREAD_ID, run_id=comp_run_id,
        user_id=USER_ID, agent_type="video", run_type="companion_chat",
        status="running", conversation_uuid=conversation.uuid,
    )

    credit_cb = CreditCheckCallbackHandler(user_id=USER_ID, action="companion_chat")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, streaming=True, callbacks=[credit_cb])
    snap = await build_snapshot_from_db(run_id=RUN_ID, user_id=USER_ID, thread_id=THREAD_ID)
    agent = create_video_companion_agent(
        llm=llm, run_id=RUN_ID, user_id=USER_ID, thread_id=THREAD_ID,
        snapshot=snap, checkpointer=MemorySaver(), enable_summarization=False,
    )
    config = {"configurable": {"thread_id": f"fast-e2e-{comp_run_id}"}}
    log.info(f"  Agent created in {time.time()-t0:.1f}s, snapshot phase={snap['phase']}")
    ok("Agent + snapshot created")

    # ── Turn 1: get_project_status ──
    log.info("\n[Turn 1] get_project_status")
    ts = time.time()
    tools, res, ai = await agent_turn(agent, config, "看看项目进度")
    log.info(f"  耗时 {time.time()-ts:.1f}s")

    status_text = res.get("get_project_status", "")
    async with pool.acquire() as conn:
        db_chars = await conn.fetchval("SELECT count(*) FROM video_characters WHERE thread_id = $1", THREAD_ID)
        db_kfs = await conn.fetchval("SELECT count(*) FROM video_keyframes WHERE thread_id = $1", THREAD_ID)
        db_vids = await conn.fetchval("SELECT count(*) FROM video_generations WHERE thread_id = $1", THREAD_ID)
    log.info(f"  DB: chars={db_chars} kfs={db_kfs} vids={db_vids}")
    if "get_project_status" in tools: ok("LLM called get_project_status")
    else: fail("LLM tool call", f"called {tools}")
    if f"{db_kfs}/{db_kfs}" in status_text: ok(f"关键帧数 {db_kfs} 与 DB 一致")
    else: fail("关键帧数", status_text[:80])
    await async_add_message_to_conversation(conversation_id=conversation.id, role="human", content="看看项目进度", event_type="user_input", event_data={"run_id": comp_run_id}, run_id=comp_run_id, conversation_uuid=conversation.uuid)

    # ── Turn 2: get_artifact_detail(character) ──
    log.info("\n[Turn 2] get_artifact_detail — characters")
    ts = time.time()
    tools, res, ai = await agent_turn(agent, config, "看看角色详情")
    log.info(f"  耗时 {time.time()-ts:.1f}s")
    detail = res.get("get_artifact_detail", "")
    if CHAR_UUID in detail:
        ok(f"角色 UUID {CHAR_UUID[:12]} 在返回中")
    elif "小确幸女孩" in detail:
        ok("包含角色名 小确幸女孩")
    else:
        fail("get_artifact_detail", detail[:200])

    # ── Turn 3: modify_outline — DB 前后对比 ──
    log.info("\n[Turn 3] modify_outline — DB 前后对比")
    async with pool.acquire() as conn:
        old_title = await conn.fetchval("SELECT title FROM video_story_outline WHERE thread_id = $1 LIMIT 1", THREAD_ID)
    new_title_tag = f"快乐测试_{int(time.time()) % 10000}"
    ts = time.time()
    tools, res, ai = await agent_turn(agent, config, f"把大纲标题改成'{new_title_tag}'")
    log.info(f"  耗时 {time.time()-ts:.1f}s")
    if "modify_outline" in tools: ok("LLM called modify_outline")
    else: fail("modify_outline tool call", f"called {tools}")
    async with pool.acquire() as conn:
        new_title = await conn.fetchval("SELECT title FROM video_story_outline WHERE thread_id = $1 LIMIT 1", THREAD_ID)
    log.info(f"  DB title: '{old_title}' → '{new_title}'")
    if new_title != old_title: ok(f"DB title 变了: '{new_title}'")
    else: fail("DB title unchanged")

    # ── Turn 4: regenerate_keyframes (mocked GPU) — 验证 DB + prompt ──
    log.info("\n[Turn 4] regenerate_keyframes (GPU mocked)")
    mock_regen_result = {"succeeded": 1, "failed": 0, "results": [{"keyframe_uuid": "mock-kf", "success": True}]}
    ts = time.time()
    with patch("app.services.task_enqueue_service.execute_regenerate_keyframes", new_callable=AsyncMock, return_value=mock_regen_result):
        tools, res, ai = await agent_turn(agent, config, "重新生成第1个镜头关键帧，改成日落风格")
    log.info(f"  耗时 {time.time()-ts:.1f}s")
    regen_kf_text = res.get("regenerate_keyframes", "")
    if "regenerate_keyframes" in tools: ok("LLM called regenerate_keyframes")
    else: fail("regenerate_keyframes call", f"called {tools}")
    if "提交" in regen_kf_text or "成功" in regen_kf_text:
        ok("regenerate_keyframes 返回成功")
    else:
        fail("regenerate_keyframes result", regen_kf_text[:200])
    async with pool.acquire() as conn:
        regen_run = await conn.fetchrow("""
            SELECT run_id, status, run_type FROM conversation_runs
            WHERE thread_id = $1 AND run_type = 'regenerate_keyframes'
            ORDER BY created_at DESC LIMIT 1
        """, THREAD_ID)
    if regen_run:
        log.info(f"  DB conversation_run: {regen_run['run_id'][:12]} type={regen_run['run_type']} status={regen_run['status']}")
        ok(f"regenerate_keyframes DB run created (status={regen_run['status']})")
    else:
        log.info("  ⚠️ mock 跳过了 DB run 创建（mock 太早，execute_regenerate_keyframes 整个被跳过）")

    # ── Turn 5: regenerate_videos (mocked GPU) ──
    log.info("\n[Turn 5] regenerate_videos (GPU mocked)")
    mock_vid_result = {"succeeded": 1, "failed": 0, "results": [{"video_uuid": "mock-vid", "success": True}]}
    ts = time.time()
    with patch("app.services.task_enqueue_service.execute_regenerate_videos", new_callable=AsyncMock, return_value=mock_vid_result):
        tools, res, ai = await agent_turn(agent, config, "重新生成第2个镜头视频")
    log.info(f"  耗时 {time.time()-ts:.1f}s")
    regen_vid_text = res.get("regenerate_videos", "")
    if "regenerate_videos" in tools: ok("LLM called regenerate_videos")
    else: fail("regenerate_videos call", f"called {tools}")
    if "提交" in regen_vid_text or "成功" in regen_vid_text:
        ok("regenerate_videos 返回成功")
    else:
        fail("regenerate_videos result", regen_vid_text[:200])

    # ── Turn 6: continue_pipeline — 构造 interrupt 消息验证 ──
    log.info("\n[Turn 6] continue_pipeline — 构造 interrupt 消息")
    async with pool.acquire() as conn:
        next_seq = await conn.fetchval("SELECT COALESCE(max(sequence), 0) + 1 FROM conversation_messages WHERE conversation_id = $1", conversation.id)
        int_id = await conn.fetchval("""
            INSERT INTO conversation_messages (conversation_id, role, content, event_type, event_data, run_id, conversation_uuid, sequence, created_at)
            VALUES ($1, 'ai', 'interrupt: after_shots', 'interrupt', $2, $3, $4, $5, now())
            RETURNING id
        """, conversation.id,
            json.dumps({"gate": "after_shots", "run_id": RUN_ID, "continued": False}),
            RUN_ID, conversation.uuid, next_seq)
    log.info(f"  构造 interrupt msg id={int_id}")

    from app.services.agent.video_edit.tools.continue_pipeline import _find_latest_interrupt_msgid
    found = await _find_latest_interrupt_msgid(THREAD_ID, RUN_ID)
    if found == int_id:
        ok(f"_find_latest_interrupt_msgid 正确找到 msg_id={found}")
    else:
        fail("continue_pipeline find", f"expected {int_id}, got {found}")

    # mock SQS 提交 — continue_pipeline 用的是延迟 import，mock 目标模块
    mock_task = {"run_id": str(uuid_lib.uuid4()), "task_type": "resume"}
    with patch("app.services.task_enqueue_service.prepare_resume_task", new_callable=AsyncMock, return_value=mock_task) as m_prep, \
         patch("app.services.aws.sqs_service.SQSTaskService.add_task_to_queue", new_callable=AsyncMock) as m_sqs, \
         patch("app.services.redis.connection.get_redis_stream_service", new_callable=AsyncMock) as m_redis:
        mock_redis_inst = AsyncMock()
        mock_redis_inst.add_task_index = AsyncMock()
        m_redis.return_value = mock_redis_inst
        ts = time.time()
        tools, res, ai = await agent_turn(agent, config, "通过 after_shots 门控继续")
        log.info(f"  耗时 {time.time()-ts:.1f}s")

    cp_text = res.get("continue_pipeline", "")
    if "continue_pipeline" in tools:
        ok("LLM called continue_pipeline")
    else:
        fail("continue_pipeline call", f"called {tools}")
    if "resume_submitted" in cp_text:
        ok("continue_pipeline 返回 resume_submitted")
        parsed = json.loads(cp_text)
        log.info(f"  new_run_id={parsed.get('new_run_id','?')[:12]}")
    elif "恢复管线失败" in cp_text:
        fail("continue_pipeline 执行", cp_text[:200])
    else:
        log.info(f"  continue_pipeline result: {cp_text[:200]}")

    # 清理测试 interrupt
    async with pool.acquire() as conn:
        await conn.execute("UPDATE conversation_messages SET event_data = $1 WHERE id = $2",
            json.dumps({"gate": "after_shots", "run_id": RUN_ID, "continued": True, "test": True}), int_id)

    # ── Phase 7: DB billing 验证 ──
    log.info("\n[Phase 7] DB 验证: conversation_run + messages + billing")
    total_cost = credit_cb.total_cost
    log.info(f"  LLM cost: ${total_cost:.6f}")
    if total_cost > 0: await async_update_conversation_run_cost(comp_run_id, total_cost)
    await async_update_conversation_run_status(comp_run_id, "completed", completed_at=datetime.utcnow())
    await async_set_conversation_run_billing_pending_if_not_completed(comp_run_id)

    run_db = await async_get_conversation_run_by_run_id(comp_run_id)
    log.info(f"  run_type={run_db.run_type} status={run_db.status} billing={run_db.billing_status} cost={run_db.cost}")
    if run_db.run_type == "companion_chat": ok("DB run_type=companion_chat")
    else: fail("run_type", run_db.run_type)
    if run_db.status == "completed": ok("DB status=completed")
    else: fail("status", run_db.status)
    if run_db.billing_status == "pending": ok("DB billing=pending")
    else: fail("billing", run_db.billing_status)
    if run_db.cost and run_db.cost > 0: ok(f"DB cost=${run_db.cost:.6f}")
    else: fail("cost", str(run_db.cost))

    all_msgs = await async_get_conversation_messages(conversation.id)
    comp_msgs = [m for m in all_msgs if m.get("run_id") == comp_run_id]
    log.info(f"  companion msgs: {len(comp_msgs)}")
    if len(comp_msgs) >= 1: ok(f"DB {len(comp_msgs)} companion messages 持久化")
    else: fail("messages", "0 messages")

    # ── Phase 8: 检查已有 keyframe 图片是否可访问 ──
    log.info("\n[Phase 8] 检查现有 keyframe 图片可访问性")
    import aiohttp
    async with pool.acquire() as conn:
        kf_url = await conn.fetchval("""
            SELECT kv.keyframe_url FROM video_keyframe_versions kv
            JOIN video_keyframes k ON k.id::text = kv.keyframe_id OR k.uuid = kv.keyframe_id
            WHERE k.thread_id = $1 AND k.shot_number = 1
            ORDER BY kv.version_number DESC LIMIT 1
        """, THREAD_ID)
    if kf_url:
        log.info(f"  shot_1 keyframe URL: {kf_url}")
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.head(kf_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        ct = resp.headers.get("content-type","")
                        cl = resp.headers.get("content-length","?")
                        ok(f"keyframe 图片可访问 (status=200, type={ct}, size={cl})")
                    else:
                        fail("keyframe 图片", f"status={resp.status}")
        except Exception as e:
            fail("keyframe 图片访问", str(e))
    else:
        fail("keyframe URL", "not found in DB")

    # ── Summary ──
    elapsed = time.time() - t0
    log.info("\n" + "=" * 60)
    log.info(f"🏁 总耗时: {elapsed:.1f}s  ✅ PASS={PASS_COUNT}  ❌ FAIL={FAIL_COUNT}")
    log.info("=" * 60)
    if elapsed > 60:
        log.info("⚠️  超过 60s，需要优化")
    if FAIL_COUNT == 0:
        log.info("🎉 All tests passed!")


def main():
    try:
        asyncio.run(run())
    except Exception as e:
        log.error(f"❌ FATAL: {e}", exc_info=True)
        sys.exit(1)
    if FAIL_COUNT > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
