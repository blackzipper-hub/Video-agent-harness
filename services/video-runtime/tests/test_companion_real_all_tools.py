"""
真实 E2E 测试：不 mock，全部 10 个 companion tool，真实 LLM + DB + tool 调用。

conda env: cuti-video-local
用法: ENVIRONMENT=local conda run -n cuti-video-local python tests/test_companion_real_all_tools.py

测试 thread: 87516541-4690-49b4-ad80-ac03623ce224
"""
import asyncio
import json
import logging
import os
import sys
import time
import uuid

os.environ["ENVIRONMENT"] = "local"
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env.local"), override=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_real_all_tools")

THREAD_ID = "87516541-4690-49b4-ad80-ac03623ce224"
PASS_COUNT = 0
FAIL_COUNT = 0


def check(condition: bool, label: str, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        logger.info(f"✅ PASS: {label} {detail}")
    else:
        FAIL_COUNT += 1
        logger.error(f"❌ FAIL: {label} {detail}")


async def run_test():
    from app.models.database import init_asyncpg_pool, get_asyncpg_pool
    await init_asyncpg_pool()
    pool = get_asyncpg_pool()

    # 确定 run_id
    async with pool.acquire() as conn:
        run_row = await conn.fetchrow(
            "SELECT run_id FROM conversation_runs WHERE thread_id = $1 ORDER BY created_at LIMIT 1",
            THREAD_ID,
        )
    run_id = run_row["run_id"] if run_row else str(uuid.uuid4())
    user_id = "admin"

    logger.info(f"=== 测试开始 thread={THREAD_ID} run_id={run_id} ===")

    from langchain_openai import ChatOpenAI
    from app.services.agent.video_edit.tools import get_companion_tools

    tools = get_companion_tools(run_id=run_id, user_id=user_id, thread_id=THREAD_ID)
    tool_names = [t.name for t in tools]
    logger.info(f"共 {len(tools)} 个 tools: {tool_names}")
    check(len(tools) == 10, "tool 数量 == 10", str(len(tools)))

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, request_timeout=120)
    llm_with_tools = llm.bind_tools(tools)
    tool_map = {t.name: t for t in tools}

    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

    system_prompt = """你是视频生成 Companion Agent，测试用途。
你有 10 个工具。每次我给你指令时，请调用对应的工具。只调用一个工具。
当前项目 thread_id: {thread_id}""".format(thread_id=THREAD_ID)

    messages = [SystemMessage(content=system_prompt)]
    tested_tools = set()

    # ---- helper ----
    async def do_turn(user_msg: str, expected_tool: str | None = None, timeout: int = 120, max_rounds: int = 3) -> dict:
        """执行一轮对话（含多步 tool 调用），返回 {'ai': AIMessage, 'tool_result': str | None}"""
        messages.append(HumanMessage(content=user_msg))
        logger.info(f"\n{'='*60}")
        logger.info(f"USER: {user_msg}")

        first_tool_result = None
        first_checked = False

        for _round in range(max_rounds):
            t0 = time.time()
            ai_msg = await asyncio.wait_for(llm_with_tools.ainvoke(messages), timeout=timeout)
            elapsed = time.time() - t0
            messages.append(ai_msg)

            logger.info(f"AI round={_round} ({elapsed:.1f}s): content={str(ai_msg.content)[:200]}")

            if not ai_msg.tool_calls:
                break

            # 处理所有 tool calls
            for tc in ai_msg.tool_calls:
                tool_name = tc["name"]
                logger.info(f"🔧 Tool call: {tool_name}({json.dumps(tc['args'], ensure_ascii=False)[:300]})")
                tested_tools.add(tool_name)

                if expected_tool and not first_checked:
                    check(tool_name == expected_tool, f"调用了 {expected_tool}", f"实际: {tool_name}")
                    first_checked = True

                tool_obj = tool_map.get(tool_name)
                if tool_obj:
                    try:
                        result = await asyncio.wait_for(tool_obj.ainvoke(tc["args"]), timeout=300)
                    except asyncio.TimeoutError:
                        result = f"⏰ tool {tool_name} 超时(300s)"
                    except Exception as e:
                        result = f"❌ tool {tool_name} 异常: {e}"
                else:
                    result = f"未知 tool: {tool_name}"

                logger.info(f"📋 Tool result: {str(result)[:500]}")
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

                if first_tool_result is None:
                    first_tool_result = result

        if expected_tool and not first_checked:
            check(False, f"期望调用 {expected_tool}", "LLM 未调用任何 tool")

        return {"ai": ai_msg, "tool_result": first_tool_result}

    # =========================================================
    # 1. get_project_status
    # =========================================================
    r = await do_turn("查看项目当前进度", "get_project_status")
    # 只要有"✅"说明有完成的阶段，就不是全部"未开始"
    check(r["tool_result"] and "✅" in r["tool_result"], "project_status 有已完成的阶段", str(r["tool_result"])[:100])

    # =========================================================
    # 2. get_artifact_detail — characters
    # =========================================================
    r = await do_turn("列出所有角色的详细信息", "get_artifact_detail")
    check(r["tool_result"] and "648c5ed5" in r["tool_result"], "artifact_detail 包含角色 UUID", "")

    # =========================================================
    # 3. get_artifact_detail — keyframes (shot_1)
    # =========================================================
    r = await do_turn("查看 shot_1 的关键帧详情", "get_artifact_detail")
    check(r["tool_result"] and ("shot" in str(r["tool_result"]).lower() or "关键帧" in str(r["tool_result"])), "artifact_detail 包含 keyframe shot", "")

    # =========================================================
    # 4. modify_outline — 修改标题
    # =========================================================
    async with pool.acquire() as conn:
        old_title = await conn.fetchval(
            "SELECT title FROM video_story_outline WHERE thread_id = $1 ORDER BY created_at DESC LIMIT 1",
            THREAD_ID,
        )
    new_title = f"全真测试_{int(time.time()) % 10000}"
    r = await do_turn(
        f"调用 modify_outline，field 填 title，instruction 填 '{new_title}'（注意 instruction 的值就是新标题本身，不要加额外文字）",
        "modify_outline",
    )
    check(r["tool_result"] and "✅" in str(r["tool_result"]), "modify_outline 成功", "")

    async with pool.acquire() as conn:
        db_title = await conn.fetchval(
            "SELECT title FROM video_story_outline WHERE thread_id = $1 ORDER BY created_at DESC LIMIT 1",
            THREAD_ID,
        )
    check(db_title == new_title, "DB title 已更新", f"expect={new_title} actual={db_title}")

    # =========================================================
    # 5. update_music_prompt
    # =========================================================
    new_music = f"轻快钢琴曲_test_{int(time.time()) % 10000}"
    r = await do_turn(f"把第一首音乐的提示词改成'{new_music}'", "update_music_prompt")
    check(r["tool_result"] and "✅" in str(r["tool_result"]), "update_music 成功", "")

    async with pool.acquire() as conn:
        mg_uuid = await conn.fetchval(
            "SELECT uuid FROM video_music_generations WHERE thread_id = $1 LIMIT 1", THREAD_ID,
        )
        db_prompt = await conn.fetchval(
            "SELECT music_prompt FROM video_music_generation_versions WHERE music_generation_id = $1 ORDER BY version_number DESC LIMIT 1",
            mg_uuid,
        )
    check(db_prompt == new_music, "DB music_prompt 已更新", f"expect={new_music} actual={db_prompt}")

    # =========================================================
    # 6. select_version — keyframe
    # =========================================================
    async with pool.acquire() as conn:
        kf_row = await conn.fetchrow(
            "SELECT uuid, keyframe_id FROM video_keyframe_versions WHERE thread_id = $1 AND shot_number = 1 ORDER BY version_number LIMIT 1",
            THREAD_ID,
        )
    if kf_row:
        kf_id = kf_row["keyframe_id"]
        kf_ver_uuid = kf_row["uuid"]
        r = await do_turn(
            f"把关键帧 {kf_id} 切换到版本 {kf_ver_uuid}",
            "select_version",
        )
        check(r["tool_result"] and "✅" in str(r["tool_result"]), "select_version keyframe 成功", "")
    else:
        logger.warning("⚠️ 跳过 select_version — 无 KF 版本")
        check(False, "select_version keyframe", "无数据")

    # =========================================================
    # 7. regenerate_keyframes (真实提交 — SQS + GPU)
    # =========================================================
    async with pool.acquire() as conn:
        kfv_count_before = await conn.fetchval(
            "SELECT count(*) FROM video_keyframe_versions WHERE thread_id = $1",
            THREAD_ID,
        )
    r = await do_turn(
        "重新生成 shot_1 的关键帧，要求画面更明亮",
        "regenerate_keyframes",
    )
    result_str = str(r["tool_result"])
    regen_kf_ok = "✅" in result_str or "已提交" in result_str or "submitted" in result_str.lower()
    check(regen_kf_ok, "regenerate_keyframes 提交", result_str[:100])

    # DB 验证: keyframe versions 增加了
    async with pool.acquire() as conn:
        kfv_count_after = await conn.fetchval(
            "SELECT count(*) FROM video_keyframe_versions WHERE thread_id = $1", THREAD_ID,
        )
        latest_kfv = await conn.fetchrow(
            "SELECT uuid, t2i_prompt, success, keyframe_url, shot_number FROM video_keyframe_versions WHERE thread_id = $1 ORDER BY created_at DESC LIMIT 1",
            THREAD_ID,
        )
    check(kfv_count_after > kfv_count_before, "KF versions 数量增加", f"{kfv_count_before} -> {kfv_count_after}")
    if latest_kfv:
        logger.info(f"最新 KF version: shot_{latest_kfv['shot_number']} ok={latest_kfv['success']} url={str(latest_kfv['keyframe_url'])[:80]}")
        logger.info(f"  t2i_prompt: {str(latest_kfv['t2i_prompt'])[:200]}")
        check(latest_kfv["success"] is True, "最新 KF version 成功", f"success={latest_kfv['success']}")
        check(bool(latest_kfv["keyframe_url"]), "最新 KF version 有 URL", str(latest_kfv["keyframe_url"])[:80])

    # =========================================================
    # 8. regenerate_videos (真实提交 — SQS + GPU)
    # =========================================================
    async with pool.acquire() as conn:
        vgv_count_before = await conn.fetchval(
            "SELECT count(*) FROM video_generation_versions vv JOIN video_generations vg ON vg.uuid = vv.video_generation_id WHERE vg.thread_id = $1",
            THREAD_ID,
        )
    r = await do_turn(
        "重新生成 shot_2 的视频，要求镜头运动更流畅",
        "regenerate_videos",
    )
    result_str = str(r["tool_result"])
    regen_vid_ok = "✅" in result_str or "已提交" in result_str or "submitted" in result_str.lower()
    check(regen_vid_ok, "regenerate_videos 提交", result_str[:100])

    # DB 验证: video generation versions 增加了
    async with pool.acquire() as conn:
        vgv_count_after = await conn.fetchval(
            "SELECT count(*) FROM video_generation_versions vv JOIN video_generations vg ON vg.uuid = vv.video_generation_id WHERE vg.thread_id = $1",
            THREAD_ID,
        )
        latest_vgv = await conn.fetchrow(
            """SELECT vv.uuid, vv.motion_prompt, vv.success, vv.video_url, vv.generation_mode, vv.audio_url, vg.shot_number
               FROM video_generation_versions vv
               JOIN video_generations vg ON vg.uuid = vv.video_generation_id
               WHERE vg.thread_id = $1
               ORDER BY vv.created_at DESC LIMIT 1""",
            THREAD_ID,
        )
    check(vgv_count_after > vgv_count_before, "VID versions 数量增加", f"{vgv_count_before} -> {vgv_count_after}")
    if latest_vgv:
        logger.info(f"最新 VID version: shot_{latest_vgv['shot_number']} ok={latest_vgv['success']} mode={latest_vgv['generation_mode']}")
        logger.info(f"  video_url: {str(latest_vgv['video_url'])[:80]}")
        logger.info(f"  audio_url: {str(latest_vgv['audio_url'])[:80]}")
        logger.info(f"  motion_prompt: {str(latest_vgv['motion_prompt'])[:200]}")
        check(latest_vgv["success"] is True, "最新 VID version 成功", f"success={latest_vgv['success']}")

    # =========================================================
    # 9. regenerate_characters (真实提交 — SQS + GPU)
    # =========================================================
    # 记录 character version 数量
    async with pool.acquire() as conn:
        char_ver_before = await conn.fetchval(
            "SELECT count(*) FROM video_character_generation_versions WHERE video_character_id = '648c5ed5-c6be-47e1-b6e9-ef379bd7e4a6'",
        )

    r = await do_turn(
        "重新生成 '小确幸女孩' (uuid=648c5ed5-c6be-47e1-b6e9-ef379bd7e4a6) 的角色形象，穿绿色裙子",
        "regenerate_characters",
    )
    result_str = str(r["tool_result"])
    regen_char_ok = "✅" in result_str or "已提交" in result_str or "submitted" in result_str.lower()
    check(regen_char_ok, "regenerate_characters 提交", result_str[:100])

    # DB 验证: character versions 增加
    async with pool.acquire() as conn:
        char_ver_after = await conn.fetchval(
            "SELECT count(*) FROM video_character_generation_versions WHERE video_character_id = '648c5ed5-c6be-47e1-b6e9-ef379bd7e4a6'",
        )
        latest_cv = await conn.fetchrow(
            "SELECT uuid, version_number, success, character_image_url FROM video_character_generation_versions WHERE video_character_id = '648c5ed5-c6be-47e1-b6e9-ef379bd7e4a6' ORDER BY version_number DESC LIMIT 1",
        )
    check(char_ver_after > char_ver_before, "Character versions 数量增加", f"{char_ver_before} -> {char_ver_after}")
    if latest_cv:
        logger.info(f"最新 character version: v{latest_cv['version_number']} ok={latest_cv['success']} url={str(latest_cv['character_image_url'])[:80]}")
        check(latest_cv["success"] is True, "最新 character version 成功", f"success={latest_cv['success']}")

    # =========================================================
    # 10. continue_pipeline
    # =========================================================
    # 先插入一条 un-continued interrupt 消息，让 continue_pipeline 能找到
    async with pool.acquire() as conn:
        conv = await conn.fetchrow(
            "SELECT id FROM conversations WHERE thread_id = $1 LIMIT 1", THREAD_ID,
        )
        if conv:
            next_seq = await conn.fetchval(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM conversation_messages WHERE conversation_id = $1",
                conv["id"],
            )
            event_data_json = json.dumps({"run_id": run_id, "gate": "after_character"})
            await conn.execute(
                """INSERT INTO conversation_messages (uuid, conversation_id, run_id, role, content, event_type, event_data, sequence, created_at)
                   VALUES ($1, $2, $3, 'system', 'interrupt', 'interrupt', $4, $5, NOW())""",
                str(uuid.uuid4()), conv["id"], run_id, event_data_json, next_seq,
            )
            logger.info("已插入测试用 interrupt 消息")

    r = await do_turn("继续生成，通过 after_character 门控", "continue_pipeline")
    result_str = str(r["tool_result"])
    if "resume_submitted" in result_str:
        check(True, "continue_pipeline 提交 resume", result_str[:100])
    elif "未找到" in result_str:
        check(False, "continue_pipeline", "未找到 interrupt — 插入可能失败")
    else:
        check("失败" not in result_str and "error" not in result_str.lower(), "continue_pipeline", result_str[:100])

    # =========================================================
    # 11. reassemble_video (可能耗时较长)
    # =========================================================
    r = await do_turn("重新合成最终视频", "reassemble_video")
    result_str = str(r["tool_result"])
    logger.info(f"reassemble_video result: {result_str[:300]}")
    reassemble_ok = "✅" in result_str or "已提交" in result_str or "视频" in result_str
    check(reassemble_ok, "reassemble_video 执行", result_str[:100])

    # =========================================================
    # DB 验证：conversation_run + conversation_messages
    # =========================================================
    logger.info(f"\n{'='*60}")
    logger.info("=== DB 验证 ===")
    async with pool.acquire() as conn:
        # 看所有 runs
        runs = await conn.fetch(
            "SELECT run_id, run_type, status, billing_status, cost FROM conversation_runs WHERE thread_id = $1 ORDER BY created_at",
            THREAD_ID,
        )
        logger.info(f"conversation_runs ({len(runs)}):")
        for r2 in runs:
            logger.info(f"  run_id={r2['run_id'][:12]} type={r2['run_type']} status={r2['status']} billing={r2['billing_status']} cost={r2['cost']}")

    # =========================================================
    # 总结
    # =========================================================
    untested = set(tool_names) - tested_tools
    if untested:
        logger.warning(f"⚠️ 未测试的 tool: {untested}")
        for ut in untested:
            check(False, f"tool {ut} 被测试", "未被 LLM 调用")

    logger.info(f"\n{'='*60}")
    logger.info(f"=== 测试完成 ===")
    logger.info(f"✅ PASS: {PASS_COUNT}")
    logger.info(f"❌ FAIL: {FAIL_COUNT}")
    logger.info(f"已测试 tools: {sorted(tested_tools)}")
    logger.info(f"未测试 tools: {sorted(untested)}")

    return FAIL_COUNT == 0


if __name__ == "__main__":
    success = asyncio.run(run_test())
    sys.exit(0 if success else 1)
