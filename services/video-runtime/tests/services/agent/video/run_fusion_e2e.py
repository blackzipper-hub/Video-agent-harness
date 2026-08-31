"""
端到端融合图生成 + 关键帧 regenerate 测试

针对 thread_id = full-803c8251-eff-eff_0 的真实数据：
1. 初始化 asyncpg pool
2. 加载 scenes / characters / character_images
3. 调用 generate_fusion_images_for_scenes 实际生成融合图并写入 DB
4. 验证 DB 中融合图记录
5. 测试 get_character_ref_images 是否正确使用融合图

Usage:
    conda run -n cuti-video-local python tests/services/agent/video/run_fusion_e2e.py
"""
import asyncio
import os
import sys
import logging
import json
from dotenv import load_dotenv

load_dotenv(".env.development")
load_dotenv(".env.local", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("fusion_e2e")

THREAD_ID = "full-803c8251-eff-eff_0"
RUN_ID = "787bf76b-927f-47a1-9948-b06fd5a41a14"
USER_ID = "admin"
CONVERSATION_ID = "3751"

# 预先导入以解决循环依赖
import app.services.agent.video.keyframe_generation_service  # noqa: F401


async def main():
    # ==================== Step 0: 初始化 ====================
    logger.info("=" * 60)
    logger.info("Step 0: 初始化 asyncpg pool & 加载环境")
    logger.info("=" * 60)

    from app.models.database import init_asyncpg_pool, close_asyncpg_pool, get_asyncpg_pool
    await init_asyncpg_pool()

    import asyncpg
    DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/storybook_dev")

    # ==================== Step 1: 从 DB 加载数据 ====================
    logger.info("=" * 60)
    logger.info("Step 1: 从 DB 加载 scenes / characters 数据")
    logger.info("=" * 60)

    from app.services.agent.utils.database_utils import (
        get_scenes_from_db, get_characters_from_db, get_story_outline_from_db
    )
    from app.crud.video.video_character import get_character_multi_view_images_batch
    from app.models.video_state import CharacterImageInfo, StoryboardScene, CharacterProfile

    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        scene_rows = await conn.fetch(
            "SELECT uuid FROM video_scenes WHERE run_id = $1 ORDER BY scene_number",
            RUN_ID,
        )
        scene_uuids = [r["uuid"] for r in scene_rows]

        char_rows = await conn.fetch(
            "SELECT uuid FROM video_characters WHERE thread_id = $1 AND user_id = $2 ORDER BY created_at",
            THREAD_ID, USER_ID,
        )
        character_uuids = [r["uuid"] for r in char_rows]

        outline_row = await conn.fetchrow(
            "SELECT uuid FROM video_story_outline WHERE run_id = $1", RUN_ID
        )
        story_outline_uuid = outline_row["uuid"] if outline_row else None
    await pool.close()

    logger.info(f"  场景数: {len(scene_uuids)}")
    logger.info(f"  角色数: {len(character_uuids)}")
    logger.info(f"  大纲UUID: {story_outline_uuid}")

    scenes_data = await get_scenes_from_db(scene_uuids)
    characters_data = await get_characters_from_db(character_uuids)
    multiview_by_char = await get_character_multi_view_images_batch(character_uuids, USER_ID)

    character_images = {}
    for character in characters_data:
        char_info = CharacterImageInfo(
            character_id=character.id,
            main_image_url=character.character_image_url or None,
        )
        version = multiview_by_char.get(character.id)
        if version and getattr(version, "multi_view_image_url", None):
            char_info.multiview_url = version.multi_view_image_url
        character_images[character.id] = char_info

    story_outline = None
    if story_outline_uuid:
        story_outline = await get_story_outline_from_db(story_outline_uuid)

    logger.info(f"  加载完成: {len(scenes_data)} scenes, {len(characters_data)} characters, {len(character_images)} images")

    for cid, ci in character_images.items():
        char_name = next((c.name for c in characters_data if c.id == cid), "?")
        char_type = next((getattr(c.type, 'value', str(c.type)) for c in characters_data if c.id == cid), "?")
        logger.info(f"    {cid[:8]} {char_name:20s} type={char_type:10s} main={ci.main_image_url is not None} mv={ci.multiview_url is not None}")

    # ==================== Step 2: 分析融合图需求 ====================
    logger.info("=" * 60)
    logger.info("Step 2: 分析融合图需求 (decide_fusion_combinations)")
    logger.info("=" * 60)

    from app.services.agent.video.character_fusion_service import (
        decide_fusion_combinations_for_scenes, get_model_limit, split_characters_by_model_limit
    )

    model_limit = get_model_limit(None)
    logger.info(f"  Model limit: {model_limit}")

    main_combos, mv_combos = decide_fusion_combinations_for_scenes(scenes_data, model_limit)
    logger.info(f"  需要生成 {len(main_combos)} 个主图融合图")
    logger.info(f"  需要生成 {len(mv_combos)} 个 multiview 融合图")

    for key, char_ids in main_combos.items():
        names = [next((c.name for c in characters_data if c.id == cid), cid[:8]) for cid in char_ids]
        logger.info(f"    MAIN: {names}")

    over_limit_scenes = [s for s in scenes_data if len(s.character_ids) > model_limit]
    logger.info(f"  超限场景数: {len(over_limit_scenes)} / {len(scenes_data)}")

    # ==================== Step 3: 实际生成融合图并写入 DB ====================
    logger.info("=" * 60)
    logger.info("Step 3: 开始实际生成融合图 (generate_fusion_images_for_scenes)")
    logger.info("=" * 60)

    from app.services.agent.video.character_fusion_service import generate_fusion_images_for_scenes

    state = {
        "user_id": USER_ID,
        "conversation_id": CONVERSATION_ID,
        "thread_id": THREAD_ID,
        "run_id": RUN_ID,
    }

    saved_count, failed_count, all_messages = await generate_fusion_images_for_scenes(
        scenes_data=scenes_data,
        characters_data=characters_data,
        character_images=character_images,
        story_outline=story_outline,
        user_option=None,
        state=state,
        llm=None,
    )

    logger.info(f"  融合图生成完成: 成功={saved_count}, 失败={failed_count}, messages={len(all_messages)}")

    # ==================== Step 4: 验证 DB 中的融合图 ====================
    logger.info("=" * 60)
    logger.info("Step 4: 验证 DB 中的融合图记录")
    logger.info("=" * 60)

    pool2 = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool2.acquire() as conn:
        fusions = await conn.fetch(
            "SELECT uuid, fusion_key, image_type, character_ids, fusion_image_url "
            "FROM video_character_fusion_images WHERE thread_id = $1 ORDER BY created_at",
            THREAD_ID,
        )
        logger.info(f"  DB 中融合图总数: {len(fusions)}")
        for f in fusions:
            url = f["fusion_image_url"] or ""
            has_url = bool(url)
            cids = f["character_ids"] or []
            names = []
            for cid in cids:
                n = next((c.name for c in characters_data if c.id == cid), cid[:8])
                names.append(n)
            logger.info(f"    type={f['image_type']:10s} chars={names} has_url={has_url}")
            if has_url:
                logger.info(f"      url={url[:100]}...")
    await pool2.close()

    # ==================== Step 5: 测试 get_character_ref_images 使用融合图 ====================
    logger.info("=" * 60)
    logger.info("Step 5: 测试 get_character_ref_images 使用融合图")
    logger.info("=" * 60)

    from app.services.agent.video.keyframe_generation_service import (
        get_character_ref_images, _build_character_images_dict
    )
    from app.crud.video.video_character import get_character_fusion_images_by_character_ids
    from unittest.mock import MagicMock

    char_images_full = await _build_character_images_dict(
        {c.id for c in characters_data}, USER_ID
    )

    all_char_ids = list({cid for s in scenes_data for cid in s.character_ids})
    pre_fetched_fusions = await get_character_fusion_images_by_character_ids(character_ids=all_char_ids)
    logger.info(f"  预取融合图: {len(pre_fetched_fusions)} 条")

    pool3 = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool3.acquire() as conn:
        shots = await conn.fetch(
            "SELECT uuid, shot_number, character_ids FROM video_detailed_shots "
            "WHERE run_id = $1 ORDER BY shot_number",
            RUN_ID,
        )

    for s in shots:
        cids = s["character_ids"] if isinstance(s["character_ids"], list) else json.loads(s["character_ids"])
        if len(cids) <= model_limit:
            continue

        shot = MagicMock()
        shot.shot_number = s["shot_number"]
        shot.character_ids = cids

        result = await get_character_ref_images(
            shot, char_images_full, model_limit=model_limit, user_id=USER_ID,
            pre_fetched_fusions=pre_fetched_fusions,
        )

        names = [next((c.name for c in characters_data if c.id == cid), cid[:8]) for cid in cids]
        fusion_count = sum(1 for url in result if "fusion" in url.lower() or url in [
            getattr(f, "fusion_image_url", "") for f in pre_fetched_fusions if getattr(f, "fusion_image_url", None)
        ])

        logger.info(f"  Shot {s['shot_number']:02d}: {len(cids)} chars → {len(result)} ref images  chars={names}")
        for i, url in enumerate(result):
            logger.info(f"    [{i}] {url[:80]}...")

    await pool3.close()

    # ==================== 清理 ====================
    await close_asyncpg_pool()
    logger.info("=" * 60)
    logger.info("全部完成！可以去 admin 页面查看融合图了")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
