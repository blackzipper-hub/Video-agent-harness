"""
完整流程 E2E 测试：新建任务 → pipeline interrupt → companion tools → continue_pipeline → 完成

conda env: cuti-video-local
用法: ENVIRONMENT=local /home/songsong/miniconda3/envs/cuti-video-local/bin/python -u tests/test_full_flow_e2e.py 2>&1 | tee /tmp/full_flow_e2e.log

严格 30s 超时：任何单步操作超过 30s 就跳过。
"""
import asyncio
import json
import logging
import os
import sys
import time
import uuid as _uuid

os.environ["ENVIRONMENT"] = "local"
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env.local"), override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("full_flow_e2e")

PASS_COUNT = 0
FAIL_COUNT = 0
TIMEOUT_SEC = 30


def check(condition: bool, label: str, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        logger.info(f"✅ PASS: {label} {detail}")
    else:
        FAIL_COUNT += 1
        logger.error(f"❌ FAIL: {label} {detail}")


async def wait_with_timeout(coro, label: str, timeout: int = TIMEOUT_SEC):
    """运行协程，超时则返回 None 并记录。"""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"⏰ TIMEOUT({timeout}s): {label}，跳过")
        return None


async def run_test():
    from app.models.database import init_asyncpg_pool, get_asyncpg_pool
    await init_asyncpg_pool()
    pool = get_asyncpg_pool()

    # ================================================================
    # Phase 1: 创建新任务
    # ================================================================
    logger.info("=" * 60)
    logger.info("Phase 1: 创建新 VideoAgent 任务")
    logger.info("=" * 60)

    thread_id = str(_uuid.uuid4())
    user_id = "admin"
    user_input = "做一个10秒的短视频，一只小猫在公园玩耍"

    from app.crud.conversation import async_create_conversation
    conversation = await async_create_conversation(
        user_id=user_id,
        thread_id=thread_id,
        title=user_input[:50],
    )
    check(conversation is not None, "创建 conversation", f"thread_id={thread_id}")

    from app.services.task_enqueue_service import enqueue_video_task
    run_id = await enqueue_video_task(
        conversation_id=conversation.id,
        conversation_uuid=conversation.uuid,
        thread_id=thread_id,
        user_id=user_id,
        user_input=user_input,
        user_option={"full_auto": False},
        user_input_files=None,
        agent_type="video",
        full_auto=False,
    )
    check(run_id is not None, "入队成功", f"run_id={run_id}")
    logger.info(f"🆔 thread_id={thread_id}")
    logger.info(f"🆔 run_id={run_id}")

    # ================================================================
    # Phase 2: 等待 Worker 处理 → 第一个 interrupt (after_character)
    # ================================================================
    logger.info("=" * 60)
    logger.info("Phase 2: 等待 pipeline 到达 interrupt 点")
    logger.info("=" * 60)

    max_wait = 300  # 最多 5 分钟
    poll_interval = 5
    elapsed = 0
    interrupt_found = False

    while elapsed < max_wait:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM conversation_runs WHERE run_id = $1",
                run_id,
            )
            interrupt_msg = await conn.fetchrow("""
                SELECT cm.id, cm.event_data
                FROM conversation_messages cm
                JOIN conversations c ON c.id = cm.conversation_id
                WHERE c.thread_id = $1 AND cm.event_type = 'interrupt'
                  AND cm.event_data::text NOT LIKE '%"continued": true%'
                ORDER BY cm.sequence DESC LIMIT 1
            """, thread_id)

        status = row["status"] if row else "not_found"
        logger.info(f"⏳ [{elapsed}s] run status={status}")

        if interrupt_msg:
            interrupt_found = True
            event_data = interrupt_msg["event_data"]
            if isinstance(event_data, str):
                event_data = json.loads(event_data)
            gate = event_data.get("interrupt_data", {}).get("step", "") or event_data.get("gate", "")
            logger.info(f"🚦 找到 interrupt! msg_id={interrupt_msg['id']} gate={gate}")
            break

        if status in ("completed", "failed", "error"):
            logger.warning(f"任务已结束（{status}），未找到 interrupt")
            break

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    check(interrupt_found, "pipeline 到达 interrupt 点")

    if not interrupt_found:
        logger.error("无法继续测试，pipeline 未到达 interrupt")
        _summary()
        return FAIL_COUNT == 0

    # ================================================================
    # Phase 3: Companion Agent — 测试 tools
    # ================================================================
    logger.info("=" * 60)
    logger.info("Phase 3: Companion Agent 工具测试")
    logger.info("=" * 60)

    from app.services.agent.video_edit.tools import get_companion_tools
    tools = get_companion_tools(run_id=run_id, user_id=user_id, thread_id=thread_id)
    tool_map = {t.name: t for t in tools}

    # 3a. get_project_status
    logger.info("--- 3a: get_project_status ---")
    status_result = await wait_with_timeout(
        tool_map["get_project_status"].ainvoke({}),
        "get_project_status",
    )
    if status_result:
        logger.info(f"Status result:\n{str(status_result)[:500]}")
        check("角色" in str(status_result) or "character" in str(status_result).lower(),
              "get_project_status 包含角色信息")
    else:
        check(False, "get_project_status 返回结果")

    # 3b. get_artifact_detail — 查角色
    logger.info("--- 3b: get_artifact_detail (characters) ---")
    detail_result = await wait_with_timeout(
        tool_map["get_artifact_detail"].ainvoke({"artifact_type": "characters"}),
        "get_artifact_detail characters",
    )
    if detail_result:
        logger.info(f"Characters detail:\n{str(detail_result)[:500]}")
        check(len(str(detail_result)) > 20, "get_artifact_detail 返回角色数据")
    else:
        check(False, "get_artifact_detail characters 返回结果")

    # 3c. 提取角色 UUID 用于 regenerate
    char_uuid = None
    if detail_result:
        import re
        uuids = re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', str(detail_result))
        if uuids:
            char_uuid = uuids[0]
            logger.info(f"提取到角色 UUID: {char_uuid}")

    # 3d. regenerate_characters (instruction 模式)
    if char_uuid:
        logger.info("--- 3d: regenerate_characters (instruction 模式) ---")
        regen_result = await wait_with_timeout(
            tool_map["regenerate_characters"].ainvoke({
                "character_uuids": [char_uuid],
                "instruction": "让毛色更深一些",
                "mode": "instruction",
            }),
            "regenerate_characters",
            timeout=60,
        )
        if regen_result:
            logger.info(f"Regenerate result: {str(regen_result)[:300]}")
            check("✅" in str(regen_result), "regenerate_characters 成功")

            # 验证 DB
            async with pool.acquire() as conn:
                latest_cv = await conn.fetchrow(
                    "SELECT t2i_prompt, success FROM video_character_generation_versions "
                    "WHERE video_character_id = $1 ORDER BY version_number DESC LIMIT 1",
                    char_uuid,
                )
            if latest_cv:
                prompt = latest_cv["t2i_prompt"] or ""
                logger.info(f"DB 新版本 t2i_prompt ({len(prompt)} chars): {prompt[:200]}")
                check(len(prompt) > 30, "DB t2i_prompt 不是纯短指令", f"len={len(prompt)}")
            else:
                check(False, "DB 角色新版本存在")
        else:
            check(False, "regenerate_characters 完成")
    else:
        logger.warning("未提取到角色 UUID，跳过 regenerate")

    # 3e. get_artifact_detail — 关键帧
    logger.info("--- 3e: get_artifact_detail (keyframes) ---")
    kf_detail = await wait_with_timeout(
        tool_map["get_artifact_detail"].ainvoke({"artifact_type": "keyframes"}),
        "get_artifact_detail keyframes",
    )
    if kf_detail:
        kf_str = str(kf_detail)
        logger.info(f"Keyframes detail:\n{kf_str[:500]}")
        has_kf_data = "shot" in kf_str.lower() or "关键帧" in kf_str
        check(has_kf_data or "暂无" in kf_str, "get_artifact_detail keyframes 返回")
    else:
        check(False, "get_artifact_detail keyframes 返回结果")

    # ================================================================
    # Phase 4: continue_pipeline — 恢复管线
    # ================================================================
    logger.info("=" * 60)
    logger.info("Phase 4: continue_pipeline — 恢复管线")
    logger.info("=" * 60)

    continue_result = await wait_with_timeout(
        tool_map["continue_pipeline"].ainvoke({"gate": "after_character"}),
        "continue_pipeline",
    )
    if continue_result:
        logger.info(f"Continue result: {str(continue_result)[:300]}")
        check("resume_submitted" in str(continue_result), "continue_pipeline 提交成功")

        result_data = {}
        try:
            result_data = json.loads(str(continue_result))
        except Exception:
            pass
        new_run_id = result_data.get("new_run_id", "")
        if new_run_id:
            logger.info(f"新 run_id: {new_run_id}")

            # 验证 DB 中 conversation_run 记录
            async with pool.acquire() as conn:
                new_run = await conn.fetchrow(
                    "SELECT run_type, status FROM conversation_runs WHERE run_id = $1",
                    new_run_id,
                )
            if new_run:
                check(new_run["run_type"] == "resume", "新 run 是 resume 类型", f"type={new_run['run_type']}")
            else:
                check(False, "新 run DB 记录存在")
    else:
        check(False, "continue_pipeline 完成")

    # ================================================================
    # Phase 5: 等待恢复后的 pipeline 处理
    # ================================================================
    logger.info("=" * 60)
    logger.info("Phase 5: 等待恢复后的 pipeline 直到下一个 interrupt 或完成")
    logger.info("=" * 60)

    if new_run_id:
        elapsed = 0
        next_state = None
        while elapsed < max_wait:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT status FROM conversation_runs WHERE run_id = $1",
                    new_run_id,
                )
                next_interrupt = await conn.fetchrow("""
                    SELECT cm.id, cm.event_data
                    FROM conversation_messages cm
                    JOIN conversations c ON c.id = cm.conversation_id
                    WHERE c.thread_id = $1 AND cm.event_type = 'interrupt'
                      AND cm.event_data::text NOT LIKE '%"continued": true%'
                    ORDER BY cm.sequence DESC LIMIT 1
                """, thread_id)

            status = row["status"] if row else "not_found"
            logger.info(f"⏳ [{elapsed}s] resume run status={status}")

            if next_interrupt:
                event_data = next_interrupt["event_data"]
                if isinstance(event_data, str):
                    event_data = json.loads(event_data)
                gate2 = event_data.get("interrupt_data", {}).get("step", "") or event_data.get("gate", "")
                logger.info(f"🚦 新 interrupt! gate={gate2}")
                next_state = "interrupted"
                break

            if status in ("completed", "failed", "error"):
                next_state = status
                break

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        check(next_state is not None, "resume 后 pipeline 有进展", f"state={next_state}")

        # 如果到了 after_keyframe_reflection，再 continue 一次
        if next_state == "interrupted":
            logger.info("--- 再次 continue_pipeline ---")
            continue2 = await wait_with_timeout(
                tool_map["continue_pipeline"].ainvoke({"gate": "after_keyframe_reflection"}),
                "continue_pipeline #2",
            )
            if continue2:
                logger.info(f"Continue #2 result: {str(continue2)[:300]}")
                check("resume_submitted" in str(continue2), "第二次 continue 成功")

                # 等待第三个 interrupt 或完成
                result_data2 = {}
                try:
                    result_data2 = json.loads(str(continue2))
                except Exception:
                    pass
                new_run_id_2 = result_data2.get("new_run_id", "")

                if new_run_id_2:
                    elapsed = 0
                    while elapsed < max_wait:
                        async with pool.acquire() as conn:
                            row = await conn.fetchrow(
                                "SELECT status FROM conversation_runs WHERE run_id = $1",
                                new_run_id_2,
                            )
                            next_interrupt2 = await conn.fetchrow("""
                                SELECT cm.id, cm.event_data
                                FROM conversation_messages cm
                                JOIN conversations c ON c.id = cm.conversation_id
                                WHERE c.thread_id = $1 AND cm.event_type = 'interrupt'
                                  AND cm.event_data::text NOT LIKE '%"continued": true%'
                                ORDER BY cm.sequence DESC LIMIT 1
                            """, thread_id)

                        status = row["status"] if row else "not_found"
                        logger.info(f"⏳ [{elapsed}s] resume #2 run status={status}")

                        if next_interrupt2:
                            event_data2 = next_interrupt2["event_data"]
                            if isinstance(event_data2, str):
                                event_data2 = json.loads(event_data2)
                            gate3 = event_data2.get("interrupt_data", {}).get("step", "") or event_data2.get("gate", "")
                            logger.info(f"🚦 第三个 interrupt! gate={gate3}")
                            break

                        if status in ("completed", "failed", "error"):
                            logger.info(f"Pipeline 结束: {status}")
                            break

                        await asyncio.sleep(poll_interval)
                        elapsed += poll_interval

                    # 第三次 continue (after_shots)
                    async with pool.acquire() as conn:
                        final_interrupt = await conn.fetchrow("""
                            SELECT cm.id, cm.event_data
                            FROM conversation_messages cm
                            JOIN conversations c ON c.id = cm.conversation_id
                            WHERE c.thread_id = $1 AND cm.event_type = 'interrupt'
                              AND cm.event_data::text NOT LIKE '%"continued": true%'
                            ORDER BY cm.sequence DESC LIMIT 1
                        """, thread_id)

                    if final_interrupt:
                        logger.info("--- 第三次 continue_pipeline (after_shots) ---")
                        continue3 = await wait_with_timeout(
                            tool_map["continue_pipeline"].ainvoke({"gate": "after_shots"}),
                            "continue_pipeline #3",
                        )
                        if continue3:
                            logger.info(f"Continue #3 result: {str(continue3)[:300]}")
                            check("resume_submitted" in str(continue3), "第三次 continue 成功")

    # ================================================================
    # Phase 6: 最终验证 — 检查整体数据完整性
    # ================================================================
    logger.info("=" * 60)
    logger.info("Phase 6: 最终验证")
    logger.info("=" * 60)

    async with pool.acquire() as conn:
        outline = await conn.fetchrow(
            "SELECT id, title FROM video_story_outline WHERE thread_id = $1 LIMIT 1",
            thread_id,
        )
        char_count = await conn.fetchval(
            "SELECT COUNT(*) FROM video_characters WHERE thread_id = $1",
            thread_id,
        )
        kf_count = await conn.fetchval(
            "SELECT COUNT(*) FROM video_keyframes WHERE thread_id = $1",
            thread_id,
        )
        vid_count = await conn.fetchval(
            "SELECT COUNT(*) FROM video_generations WHERE thread_id = $1",
            thread_id,
        )
        run_count = await conn.fetchval(
            "SELECT COUNT(*) FROM conversation_runs WHERE thread_id = $1",
            thread_id,
        )

    check(outline is not None, "大纲已生成", f"title={outline['title'][:50] if outline else 'None'}")
    check(char_count > 0, f"角色已生成: {char_count} 个")
    logger.info(f"📊 最终统计: outline={'✓' if outline else '✗'}, characters={char_count}, keyframes={kf_count}, videos={vid_count}, runs={run_count}")

    _summary()
    return FAIL_COUNT == 0


def _summary():
    logger.info(f"\n{'=' * 60}")
    logger.info(f"✅ PASS: {PASS_COUNT}")
    logger.info(f"❌ FAIL: {FAIL_COUNT}")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    success = asyncio.run(run_test())
    sys.exit(0 if success else 1)
