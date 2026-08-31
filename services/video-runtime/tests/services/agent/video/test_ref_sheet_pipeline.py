"""
测试 Reference Sheet 在 pipeline 和 regenerate 两条路径下的行为

1. Pipeline 路径: get_character_ref_images → _get_per_shot_ref_image_urls → generate_batch_keyframe_prompts
2. Regenerate 路径: resolve_keyframe_prompt_reference_images → execute (模拟 custom_prompt)

conda run -n cuti-video-local python tests/services/agent/video/test_ref_sheet_pipeline.py
"""
import asyncio
import os
import json
import logging
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv
load_dotenv(".env.development")
load_dotenv(".env.local", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("ref_sheet_test")

THREAD_ID = "full-803c8251-eff-eff_0"
RUN_ID = "787bf76b-927f-47a1-9948-b06fd5a41a14"
USER_ID = "admin"

import app.services.agent.video.keyframe_generation_service  # noqa: F401


async def main():
    from app.models.database import init_asyncpg_pool, close_asyncpg_pool
    await init_asyncpg_pool()
    import asyncpg

    from app.services.agent.video.keyframe_generation_service import (
        get_character_ref_images,
        _build_character_images_dict,
        resolve_keyframe_prompt_reference_images,
        KeyframePromptResult,
        REFERENCE_SHEET_URL_TAG,
        process_ref_urls_with_sheet_detection,
        get_model_limit,
    )
    from app.services.agent.utils.database_utils import get_characters_from_db

    DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/storybook_dev")
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)

    async with pool.acquire() as conn:
        char_rows = await conn.fetch(
            "SELECT uuid FROM video_characters WHERE thread_id = $1 AND user_id = $2",
            THREAD_ID, USER_ID,
        )
        character_uuids = [r["uuid"] for r in char_rows]

        shots = await conn.fetch(
            "SELECT uuid, shot_number, character_ids FROM video_detailed_shots "
            "WHERE run_id = $1 ORDER BY shot_number", RUN_ID,
        )

    char_images = await _build_character_images_dict(set(character_uuids), USER_ID)
    characters_data = await get_characters_from_db(character_uuids)
    char_name_map = {c.id: c.name for c in characters_data}
    model_limit = get_model_limit(None)

    results = []

    logger.info("=" * 70)
    logger.info(f"Model limit: {model_limit}")
    logger.info(f"Total shots: {len(shots)}, Total characters: {len(character_uuids)}")
    logger.info("=" * 70)

    # ============================================================
    # TEST 1: Pipeline 路径 — get_character_ref_images per shot
    # ============================================================
    logger.info("\n" + "=" * 70)
    logger.info("TEST 1: Pipeline 路径 — get_character_ref_images")
    logger.info("=" * 70)

    from unittest.mock import MagicMock

    for s in shots:
        cids = s["character_ids"] if isinstance(s["character_ids"], list) else json.loads(s["character_ids"])
        shot_num = s["shot_number"]
        char_count = len(cids)
        is_over = char_count > model_limit
        char_names = [char_name_map.get(cid, cid[:8]) for cid in cids]

        mock_shot = MagicMock()
        mock_shot.shot_number = shot_num
        mock_shot.character_ids = cids

        ref_urls = await get_character_ref_images(
            shot=mock_shot,
            character_images=char_images,
            db=None,
            user_id=USER_ID,
            model_limit=model_limit,
        )

        # 检测是否有 sheet
        clean_urls, has_sheet, _ = process_ref_urls_with_sheet_detection(ref_urls)
        individual_count = len(clean_urls) - 1 if has_sheet else len(clean_urls)
        sheet_count = len(cids) - individual_count if has_sheet else 0

        status = "SHEET" if has_sheet else ("OK" if char_count <= model_limit else "TRUNCATED")

        result = {
            "shot": shot_num,
            "char_count": char_count,
            "char_names": char_names,
            "over_limit": is_over,
            "ref_urls_count": len(clean_urls),
            "has_sheet": has_sheet,
            "individual_count": individual_count,
            "sheet_overflow_count": sheet_count,
            "status": status,
        }
        results.append(result)

        if is_over:
            logger.info(
                f"  Shot {shot_num}: {char_count} chars [{', '.join(char_names)}] → "
                f"{individual_count} individual + {'1 sheet (' + str(sheet_count) + ' overflow)' if has_sheet else 'NO SHEET'} "
                f"= {len(clean_urls)} total URLs [{status}]"
            )
        else:
            logger.info(f"  Shot {shot_num}: {char_count} chars → {len(clean_urls)} URLs [OK]")

    over_limit_shots = [r for r in results if r["over_limit"]]
    sheet_shots = [r for r in results if r["has_sheet"]]

    logger.info(f"\n  Summary: {len(over_limit_shots)}/{len(results)} shots over limit, {len(sheet_shots)} using Reference Sheet")

    # ============================================================
    # TEST 2: Regenerate 路径 — resolve_keyframe_prompt_reference_images
    # ============================================================
    logger.info("\n" + "=" * 70)
    logger.info("TEST 2: Regenerate 路径 — resolve_keyframe_prompt_reference_images")
    logger.info("=" * 70)

    regen_results = []
    for s in shots:
        cids = s["character_ids"] if isinstance(s["character_ids"], list) else json.loads(s["character_ids"])
        shot_num = s["shot_number"]
        char_count = len(cids)
        if char_count <= model_limit:
            continue

        mock_shot = MagicMock()
        mock_shot.shot_number = shot_num
        mock_shot.character_ids = cids

        custom_prompt = f"A cinematic scene with all {char_count} characters present. Warm lighting, 16:9."
        single_prompt = KeyframePromptResult(
            shot_number=shot_num,
            t2i_prompt=custom_prompt,
            frame_index=0,
        )

        resolved = await resolve_keyframe_prompt_reference_images(
            shots_batch=[mock_shot],
            character_images=char_images,
            prompts=[single_prompt],
            user_option=None,
            user_id=USER_ID,
        )

        p = resolved[0]
        has_prefix = p.final_prompt and "REFERENCE SHEET" in p.final_prompt
        final_urls = p.all_ref_urls

        regen_result = {
            "shot": shot_num,
            "char_count": char_count,
            "final_urls_count": len(final_urls),
            "has_sheet_prefix": has_prefix,
            "original_prompt_len": len(custom_prompt),
            "final_prompt_len": len(p.final_prompt) if p.final_prompt else 0,
        }
        regen_results.append(regen_result)

        logger.info(
            f"  Shot {shot_num}: {char_count} chars → "
            f"final_urls={len(final_urls)}, has_sheet_prefix={has_prefix}, "
            f"prompt_grew={regen_result['final_prompt_len'] - regen_result['original_prompt_len']} chars"
        )

    # ============================================================
    # Summary
    # ============================================================
    logger.info("\n" + "=" * 70)
    logger.info("FINAL SUMMARY")
    logger.info("=" * 70)

    total_shots = len(results)
    over_shots = len(over_limit_shots)
    sheet_ok = len(sheet_shots)

    logger.info(f"  Total shots: {total_shots}")
    logger.info(f"  Over limit: {over_shots}")
    logger.info(f"  Sheet generated: {sheet_ok}")
    logger.info(f"  Sheet failed: {over_shots - sheet_ok}")

    for r in results:
        if r["over_limit"]:
            logger.info(
                f"    Shot {r['shot']:>2}: {r['char_count']} chars → "
                f"{r['individual_count']} individual + {r['sheet_overflow_count']} in sheet → "
                f"{r['ref_urls_count']} total [{r['status']}]"
            )

    logger.info(f"\n  Regenerate path tested: {len(regen_results)} shots")
    for r in regen_results:
        logger.info(
            f"    Shot {r['shot']:>2}: {r['char_count']} chars → "
            f"{r['final_urls_count']} URLs, prefix={'YES' if r['has_sheet_prefix'] else 'NO'}"
        )

    all_ok = all(r["has_sheet"] for r in results if r["over_limit"])
    regen_ok = all(r["has_sheet_prefix"] for r in regen_results)
    logger.info(f"\n  Pipeline path: {'ALL OK' if all_ok else 'SOME FAILED'}")
    logger.info(f"  Regenerate path: {'ALL OK' if regen_ok else 'SOME FAILED'}")

    await pool.close()
    await close_asyncpg_pool()

    return results, regen_results


if __name__ == "__main__":
    asyncio.run(main())
