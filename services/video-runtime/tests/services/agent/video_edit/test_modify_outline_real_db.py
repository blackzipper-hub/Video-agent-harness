"""modify_outline 真实 DB + LLM 集成测试。

默认使用 thread_admin_72cdc01e-...；会先备份并在结束时尽量恢复关键字段。

运行：
  cd Cuti-VideoAgent
  conda run -n cuti-video-local pytest \\
    tests/services/agent/video_edit/test_modify_outline_real_db.py -v -s -m integration
"""
from __future__ import annotations

import json
import logging

import pytest

logger = logging.getLogger(__name__)

THREAD_ID = "thread_admin_72cdc01e-03d9-4e0c-be92-f1f26e422d4f"
RUN_ID = "2e2a2b03-de32-4687-b786-12524c0209d1"
USER_ID = "admin"


async def _load_chapters(pool, outline_uuid: str):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            'SELECT uuid, "order", title, description FROM video_chapters '
            "WHERE story_outline_id=$1 ORDER BY \"order\"",
            outline_uuid,
        )
        return [dict(r) for r in rows]


async def _restore_chapters(pool, chapters_backup: list):
    async with pool.acquire() as conn:
        for ch in chapters_backup:
            await conn.execute(
                "UPDATE video_chapters SET title=$1, description=$2, updated_at=now() WHERE uuid=$3",
                ch["title"],
                ch["description"],
                ch["uuid"],
            )


@pytest.fixture
async def db_pool():
    from app.models import database as db_mod

    if db_mod._asyncpg_pool is not None:
        try:
            await db_mod._asyncpg_pool.close()
        except Exception:
            pass
        db_mod._asyncpg_pool = None
    await db_mod.init_asyncpg_pool()
    yield db_mod.get_asyncpg_pool()
    if db_mod._asyncpg_pool is not None:
        try:
            await db_mod._asyncpg_pool.close()
        except Exception:
            pass
        db_mod._asyncpg_pool = None


async def _load_outline_and_analysis(pool, thread_id: str):
    async with pool.acquire() as conn:
        outline = await conn.fetchrow(
            "SELECT uuid, title, theme, description, key_message, style_guide, analysis_id "
            "FROM video_story_outline WHERE thread_id=$1 ORDER BY updated_at DESC LIMIT 1",
            thread_id,
        )
        assert outline, f"outline missing for {thread_id}"
        analysis = None
        if outline["analysis_id"]:
            analysis = await conn.fetchrow(
                "SELECT uuid, style_preferences FROM video_analysis WHERE uuid=$1",
                outline["analysis_id"],
            )
        return outline, analysis


@pytest.mark.integration
@pytest.mark.asyncio
async def test_modify_outline_style_real_db_and_llm(db_pool):
    from app.services.agent.video_edit.tools.modify_outline import ModifyOutlineTool

    before_o, before_a = await _load_outline_and_analysis(db_pool, THREAD_ID)
    backup = {
        "outline_uuid": before_o["uuid"],
        "title": before_o["title"],
        "theme": before_o["theme"],
        "description": before_o["description"],
        "key_message": before_o["key_message"],
        "style_guide": before_o["style_guide"],
        "analysis_uuid": before_a["uuid"] if before_a else None,
        "style_preferences": before_a["style_preferences"] if before_a else None,
    }
    logger.info("BEFORE style_guide=%s", (backup["style_guide"] or "")[:120])
    logger.info("BEFORE style_preferences=%s", backup["style_preferences"])
    logger.info("BEFORE description=%s", (backup["description"] or "")[:120])

    tool = ModifyOutlineTool(run_id="real-test", user_id="admin", thread_id=THREAD_ID)
    try:
        result = await tool._arun(
            instruction="故事改成科技风格",
            fields=["style", "description"],
        )
        logger.info("TOOL RESULT:\n%s", result)
        assert "✅" in result
        assert "style" in result

        after_o, after_a = await _load_outline_and_analysis(db_pool, THREAD_ID)
        logger.info("AFTER style_guide=%s", (after_o["style_guide"] or "")[:160])
        logger.info("AFTER style_preferences=%s", after_a["style_preferences"] if after_a else None)
        logger.info("AFTER description=%s", (after_o["description"] or "")[:160])

        assert after_o["style_guide"] != backup["style_guide"]
        assert after_o["description"] != backup["description"]
        assert after_o["title"] == backup["title"]
        assert after_o["theme"] == backup["theme"]
        assert after_o["key_message"] == backup["key_message"]

        assert after_a is not None
        prefs = after_a["style_preferences"]
        if isinstance(prefs, str):
            prefs = json.loads(prefs)
        assert isinstance(prefs, list) and prefs
        assert prefs != json.loads(backup["style_preferences"]) if isinstance(backup["style_preferences"], str) else prefs != backup["style_preferences"]
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE video_story_outline SET title=$1, theme=$2, description=$3, key_message=$4, "
                "style_guide=$5, updated_at=now() WHERE uuid=$6",
                backup["title"],
                backup["theme"],
                backup["description"],
                backup["key_message"],
                backup["style_guide"],
                backup["outline_uuid"],
            )
            if backup["analysis_uuid"] is not None:
                await conn.execute(
                    "UPDATE video_analysis SET style_preferences=$1, updated_at=now() WHERE uuid=$2",
                    backup["style_preferences"],
                    backup["analysis_uuid"],
                )
        logger.info("restored outline/analysis backup")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_modify_outline_horror_style_real_db(db_pool):
    """LLM 选择 fields=['style','description'] 时应更新风格标签、style_guide 与 description。"""
    from app.services.agent.video_edit.tools.modify_outline import ModifyOutlineTool

    before_o, before_a = await _load_outline_and_analysis(db_pool, THREAD_ID)
    backup = {
        "outline_uuid": before_o["uuid"],
        "title": before_o["title"],
        "theme": before_o["theme"],
        "description": before_o["description"],
        "key_message": before_o["key_message"],
        "style_guide": before_o["style_guide"],
        "analysis_uuid": before_a["uuid"] if before_a else None,
        "style_preferences": before_a["style_preferences"] if before_a else None,
    }
    logger.info("HORROR BEFORE style_preferences=%s", backup["style_preferences"])
    logger.info("HORROR BEFORE style_guide=%s", (backup["style_guide"] or "")[:120])
    logger.info("HORROR BEFORE description=%s", (backup["description"] or "")[:160])

    tool = ModifyOutlineTool(run_id="real-horror", user_id="admin", thread_id=THREAD_ID)
    try:
        result = await tool._arun(
            instruction="故事改成恐怖风格",
            fields=["style", "description"],
        )
        logger.info("HORROR TOOL RESULT:\n%s", result)
        assert "✅" in result
        assert "style" in result

        after_o, after_a = await _load_outline_and_analysis(db_pool, THREAD_ID)
        logger.info("HORROR AFTER style_preferences=%s", after_a["style_preferences"] if after_a else None)
        logger.info("HORROR AFTER style_guide=%s", (after_o["style_guide"] or "")[:160])
        logger.info("HORROR AFTER description=%s", (after_o["description"] or "")[:200])

        assert after_o["style_guide"] != backup["style_guide"]
        assert after_a is not None
        prefs = after_a["style_preferences"]
        if isinstance(prefs, str):
            prefs = json.loads(prefs)
        old_prefs = backup["style_preferences"]
        if isinstance(old_prefs, str):
            old_prefs = json.loads(old_prefs)
        assert prefs != old_prefs
        assert "真实摄影" not in json.dumps(prefs, ensure_ascii=False)
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE video_story_outline SET title=$1, theme=$2, description=$3, key_message=$4, "
                "style_guide=$5, updated_at=now() WHERE uuid=$6",
                backup["title"], backup["theme"], backup["description"],
                backup["key_message"], backup["style_guide"], backup["outline_uuid"],
            )
            if backup["analysis_uuid"]:
                await conn.execute(
                    "UPDATE video_analysis SET style_preferences=$1, updated_at=now() WHERE uuid=$2",
                    backup["style_preferences"], backup["analysis_uuid"],
                )
        logger.info("restored horror test backup")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_modify_outline_title_only_real_db_and_llm(db_pool):
    from app.services.agent.video_edit.tools.modify_outline import ModifyOutlineTool

    before_o, _ = await _load_outline_and_analysis(db_pool, THREAD_ID)
    old_title = before_o["title"]
    old_style = before_o["style_guide"]
    old_desc = before_o["description"]
    new_title_hint = "科技青春：未来校园之光"

    tool = ModifyOutlineTool(run_id="real-test-title", user_id="admin", thread_id=THREAD_ID)
    try:
        result = await tool._arun(instruction=f"把大纲标题改成{new_title_hint}", fields=["title"])
        logger.info("TITLE RESULT:\n%s", result)
        assert "✅" in result
        assert "title" in result

        after_o, _ = await _load_outline_and_analysis(db_pool, THREAD_ID)
        assert after_o["title"] != old_title
        assert after_o["style_guide"] == old_style
        assert after_o["description"] == old_desc
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE video_story_outline SET title=$1, updated_at=now() WHERE uuid=$2",
                old_title,
                before_o["uuid"],
            )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_modify_outline_apocalypse_style_syncs_chapters_real_db_and_llm(db_pool):
    """真实 LLM：style+description 应更新 style_preferences/style_guide/description 并联动章节。"""
    from app.services.agent.video_edit.tools.modify_outline import ModifyOutlineTool

    before_o, before_a = await _load_outline_and_analysis(db_pool, THREAD_ID)
    chapters_before = await _load_chapters(db_pool, before_o["uuid"])
    backup = {
        "outline_uuid": before_o["uuid"],
        "title": before_o["title"],
        "theme": before_o["theme"],
        "description": before_o["description"],
        "key_message": before_o["key_message"],
        "style_guide": before_o["style_guide"],
        "analysis_uuid": before_a["uuid"] if before_a else None,
        "style_preferences": before_a["style_preferences"] if before_a else None,
        "chapters": chapters_before,
    }
    logger.info("APOC BEFORE style_guide=%s", (backup["style_guide"] or "")[:100])
    logger.info("APOC BEFORE ch0=%s", (chapters_before[0]["description"] or "")[:80] if chapters_before else "none")

    tool = ModifyOutlineTool(run_id="real-apoc", user_id=USER_ID, thread_id=THREAD_ID)
    try:
        result = await tool._arun(
            instruction="故事改成末日风格，整体基调压抑但保留希望",
            fields=["style", "description"],
        )
        logger.info("APOC TOOL RESULT:\n%s", result)
        assert "✅" in result
        assert "style" in result
        assert "章节已同步" in result or "chapters" in result

        after_o, after_a = await _load_outline_and_analysis(db_pool, THREAD_ID)
        chapters_after = await _load_chapters(db_pool, before_o["uuid"])

        assert after_o["style_guide"] != backup["style_guide"]
        assert after_o["description"] != backup["description"]
        assert chapters_after
        assert chapters_after[0]["description"] != chapters_before[0]["description"]
        assert "明媚" not in chapters_after[0]["description"] or "末日" in chapters_after[0]["description"] or "废墟" in chapters_after[0]["description"]

        prefs = after_a["style_preferences"] if after_a else None
        if isinstance(prefs, str):
            prefs = json.loads(prefs)
        assert isinstance(prefs, list) and prefs
        joined = json.dumps(prefs, ensure_ascii=False)
        assert "真实摄影" not in joined
        logger.info("APOC AFTER style_preferences=%s", prefs)
        logger.info("APOC AFTER ch0=%s", (chapters_after[0]["description"] or "")[:120])
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE video_story_outline SET title=$1, theme=$2, description=$3, key_message=$4, "
                "style_guide=$5, updated_at=now() WHERE uuid=$6",
                backup["title"], backup["theme"], backup["description"],
                backup["key_message"], backup["style_guide"], backup["outline_uuid"],
            )
            if backup["analysis_uuid"]:
                await conn.execute(
                    "UPDATE video_analysis SET style_preferences=$1, updated_at=now() WHERE uuid=$2",
                    backup["style_preferences"], backup["analysis_uuid"],
                )
        await _restore_chapters(db_pool, backup["chapters"])
        logger.info("restored apocalypse test backup")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_companion_llm_chooses_fields_for_style_change(db_pool):
    """真实 Companion LLM：用户说改风格时，应调用 modify_outline 且 fields 含 style（非仅 description）。"""
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_openai import ChatOpenAI

    from app.services.agent.video_edit.agent import create_video_companion_agent
    from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

    before_o, before_a = await _load_outline_and_analysis(db_pool, THREAD_ID)
    chapters_before = await _load_chapters(db_pool, before_o["uuid"])
    backup = {
        "outline_uuid": before_o["uuid"],
        "title": before_o["title"],
        "theme": before_o["theme"],
        "description": before_o["description"],
        "key_message": before_o["key_message"],
        "style_guide": before_o["style_guide"],
        "analysis_uuid": before_a["uuid"] if before_a else None,
        "style_preferences": before_a["style_preferences"] if before_a else None,
        "chapters": chapters_before,
    }

    snap = await build_snapshot_from_db(RUN_ID, USER_ID, thread_id=THREAD_ID)
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    agent = create_video_companion_agent(
        llm=llm,
        run_id=RUN_ID,
        user_id=USER_ID,
        thread_id=THREAD_ID,
        snapshot=snap,
        enable_summarization=False,
    )

    modify_calls = []
    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="故事改成末日风格")]},
        )
        for m in result["messages"]:
            if isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    if tc.get("name") == "modify_outline":
                        modify_calls.append(tc.get("args") or {})

        logger.info("COMPANION modify_outline calls: %s", modify_calls)
        assert modify_calls, "Companion 应调用 modify_outline"
        fields_used = modify_calls[0].get("fields") or []
        logger.info("COMPANION fields chosen by LLM: %s", fields_used)
        assert "style" in fields_used, (
            f"LLM 应选 fields 含 style，实际: {fields_used}；"
            "若只有 description 则前端风格标签不会更新"
        )
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE video_story_outline SET title=$1, theme=$2, description=$3, key_message=$4, "
                "style_guide=$5, updated_at=now() WHERE uuid=$6",
                backup["title"], backup["theme"], backup["description"],
                backup["key_message"], backup["style_guide"], backup["outline_uuid"],
            )
            if backup["analysis_uuid"]:
                await conn.execute(
                    "UPDATE video_analysis SET style_preferences=$1, updated_at=now() WHERE uuid=$2",
                    backup["style_preferences"], backup["analysis_uuid"],
                )
        await _restore_chapters(db_pool, backup["chapters"])
        logger.info("restored companion LLM test backup")
