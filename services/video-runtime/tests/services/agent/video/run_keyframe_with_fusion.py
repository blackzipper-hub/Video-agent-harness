"""
测试关键帧使用融合图的完整流程：
1. Pipeline 路径：keyframe_generation_node 中 batch 生成 → get_character_ref_images 使用融合图
2. Regenerate 路径：_regenerate_keyframe_version 中 resolve + execute

conda run -n cuti-video-local python tests/services/agent/video/run_keyframe_with_fusion.py
"""
import asyncio
import os
import sys
import json
import logging
from dotenv import load_dotenv
from unittest.mock import MagicMock

load_dotenv(".env.development")
load_dotenv(".env.local", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("keyframe_fusion_test")

THREAD_ID = "full-803c8251-eff-eff_0"
RUN_ID = "787bf76b-927f-47a1-9948-b06fd5a41a14"
USER_ID = "admin"

import app.services.agent.video.keyframe_generation_service  # noqa: F401


async def main():
    from app.models.database import init_asyncpg_pool, close_asyncpg_pool
    await init_asyncpg_pool()
    import asyncpg

    DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/storybook_dev")
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)

    # ==================== 加载数据 ====================
    logger.info("=" * 60)
    logger.info("加载 shots / characters / fusions 数据")
    logger.info("=" * 60)

    from app.services.agent.video.keyframe_generation_service import (
        get_character_ref_images, _build_character_images_dict
    )
    from app.crud.video.video_character import get_character_fusion_images_by_character_ids
    from app.services.agent.video.character_fusion_service import get_model_limit

    async with pool.acquire() as conn:
        char_rows = await conn.fetch(
            "SELECT uuid FROM video_characters WHERE thread_id = $1 AND user_id = $2",
            THREAD_ID, USER_ID,
        )
        character_uuids = [r["uuid"] for r in char_rows]

        shots = await conn.fetch(
            "SELECT uuid, shot_number, character_ids, scene_id FROM video_detailed_shots "
            "WHERE run_id = $1 ORDER BY shot_number",
            RUN_ID,
        )

    char_images = await _build_character_images_dict(set(character_uuids), USER_ID)
    all_char_ids = list({cid for s in shots for cid in (s["character_ids"] if isinstance(s["character_ids"], list) else json.loads(s["character_ids"]))})
    pre_fetched_fusions = await get_character_fusion_images_by_character_ids(character_ids=all_char_ids)

    model_limit = get_model_limit(None)
    logger.info(f"角色数: {len(character_uuids)}, 镜头数: {len(shots)}, 融合图数: {len(pre_fetched_fusions)}, model_limit: {model_limit}")

    # ==================== Test 1: Pipeline 路径 ====================
    logger.info("=" * 60)
    logger.info("Test 1: Pipeline 路径 - get_character_ref_images 对所有镜头")
    logger.info("=" * 60)

    over_limit_count = 0
    fusion_used_count = 0
    fallback_count = 0

    for s in shots:
        cids = s["character_ids"] if isinstance(s["character_ids"], list) else json.loads(s["character_ids"])
        shot = MagicMock()
        shot.shot_number = s["shot_number"]
        shot.character_ids = cids

        ref_images = await get_character_ref_images(
            shot, char_images, model_limit=model_limit, user_id=USER_ID,
            pre_fetched_fusions=pre_fetched_fusions,
        )

        is_over = len(cids) > model_limit
        used_fusion = len(ref_images) < len(cids) if is_over else False

        if is_over:
            over_limit_count += 1
            if used_fusion:
                fusion_used_count += 1
                logger.info(f"  Shot {s['shot_number']:02d}: {len(cids)} chars → {len(ref_images)} refs ✅ 融合图")
            else:
                fallback_count += 1
                logger.info(f"  Shot {s['shot_number']:02d}: {len(cids)} chars → {len(ref_images)} refs ⚠️ 降级(无融合图)")

    logger.info(f"\n📊 Pipeline 统计:")
    logger.info(f"  总镜头: {len(shots)}")
    logger.info(f"  超限镜头: {over_limit_count}")
    logger.info(f"  使用融合图: {fusion_used_count}")
    logger.info(f"  降级到单独图: {fallback_count}")

    # ==================== Test 2: Regenerate 路径 ====================
    logger.info("=" * 60)
    logger.info("Test 2: Regenerate 路径 - 模拟 resolve_keyframe_prompt_reference_images")
    logger.info("=" * 60)

    from app.services.agent.video.keyframe_generation_service import (
        resolve_keyframe_prompt_reference_images, KeyframePromptResult
    )
    from app.services.agent.utils.database_utils import get_scenes_from_db

    for s in shots:
        cids = s["character_ids"] if isinstance(s["character_ids"], list) else json.loads(s["character_ids"])
        if len(cids) <= model_limit:
            continue

        shot = MagicMock()
        shot.shot_number = s["shot_number"]
        shot.character_ids = cids
        shot.character_names = None

        prompt = KeyframePromptResult(
            shot_number=s["shot_number"],
            t2i_prompt="Test regenerate prompt with fusion images",
            frame_index=0,
        )

        resolved = await resolve_keyframe_prompt_reference_images(
            shots_batch=[shot],
            character_images=char_images,
            prompts=[prompt],
            user_option=None,
            user_id=USER_ID,
        )

        logger.info(f"  Shot {s['shot_number']:02d}: regenerate resolve → {len(resolved)} prompts")
        for rp in resolved:
            ref_count = len(rp.ref_images)
            logger.info(f"    ref_images={ref_count}, within_limit={ref_count <= model_limit}")

    # ==================== Test 3: 验证重复生成行为 ====================
    logger.info("=" * 60)
    logger.info("Test 3: 验证融合图复用 — 是否有 skip existing 逻辑？")
    logger.info("=" * 60)

    from app.services.agent.video.character_fusion_service import (
        generate_fusion_images_for_scenes, decide_fusion_combinations_for_scenes
    )
    from app.services.agent.utils.database_utils import get_scenes_from_db

    async with pool.acquire() as conn:
        scene_rows = await conn.fetch(
            "SELECT uuid FROM video_scenes WHERE run_id = $1 ORDER BY scene_number", RUN_ID
        )
        scene_uuids = [r["uuid"] for r in scene_rows]
    scenes_data = await get_scenes_from_db(scene_uuids)

    main_combos, mv_combos = decide_fusion_combinations_for_scenes(scenes_data, model_limit)
    logger.info(f"  需要生成: main={len(main_combos)}, multiview={len(mv_combos)}")

    async with pool.acquire() as conn:
        existing = await conn.fetch(
            "SELECT fusion_key, success, fusion_image_url FROM video_character_fusion_images "
            "WHERE thread_id = $1 AND user_id = $2",
            THREAD_ID, USER_ID,
        )
    existing_keys = {r["fusion_key"]: (r["success"], bool(r["fusion_image_url"])) for r in existing}

    skip_count = 0
    regen_count = 0
    for key in list(main_combos.keys()) + list(mv_combos.keys()):
        if key in existing_keys:
            success, has_url = existing_keys[key]
            if success and has_url:
                skip_count += 1
                logger.info(f"  {key[:50]}... → 已有成功融合图 (会被覆盖!)")
            else:
                regen_count += 1
                logger.info(f"  {key[:50]}... → 已有失败记录 (应重生成)")
        else:
            regen_count += 1

    logger.info(f"\n⚠️ 复用分析:")
    logger.info(f"  已有成功融合图: {skip_count} (当前代码会重新生成并覆盖)")
    logger.info(f"  需要重新生成: {regen_count}")
    logger.info(f"  结论: 当前代码 NO skip existing — 每次 character_fusion_node 运行都会重新生成全部")

    await pool.close()
    await close_asyncpg_pool()
    logger.info("=" * 60)
    logger.info("全部测试完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
