"""
真实关键帧生成测试 — 实际调用模型，验证 Pipeline 和 Regenerate 两条路径

选择超限 Shot 9 (6 chars) 和一个未超限 Shot 1 (3 chars) 对比。

conda run -n cuti-video-local python tests/services/agent/video/test_real_keyframe_gen.py
"""
import asyncio
import os
import json
import logging
from typing import List, Dict, Optional

from dotenv import load_dotenv
load_dotenv(".env.development")
load_dotenv(".env.local", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("real_keyframe_test")

THREAD_ID = "full-803c8251-eff-eff_0"
RUN_ID = "787bf76b-927f-47a1-9948-b06fd5a41a14"
USER_ID = "admin"
TEST_SHOTS = [1, 2, 5, 9, 11, 12, 13, 15, 22, 30]  # 10 shots: 含超限(2,9,11,12,13,22) + 未超限(1,5,15,30)

import app.services.agent.video.keyframe_generation_service  # noqa: F401


async def load_shots_from_db():
    """从 DB 加载真实 DetailedShot"""
    from app.models.database import init_asyncpg_pool, get_asyncpg_pool
    await init_asyncpg_pool()

    import asyncpg
    DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/storybook_dev")
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM video_detailed_shots WHERE run_id = $1 ORDER BY shot_number", RUN_ID,
        )
    await pool.close()

    from app.crud.video.video_story import _row_to_shot
    all_shots_db = [_row_to_shot(dict(r)) for r in rows if r]
    all_shots_db = [s for s in all_shots_db if s is not None]
    if not all_shots_db:
        raise RuntimeError(f"No shots found for run_id={RUN_ID}")

    from app.models.video_state import DetailedShot
    shots = []
    for s in all_shots_db:
        if s.shot_number in TEST_SHOTS:
            shot = DetailedShot(
                uuid=s.uuid,
                scene_id=s.scene_id,
                storyboard_detail_id=s.storyboard_detail_id,
                shot_number=s.shot_number,
                duration=s.duration,
                shot_type=s.shot_type,
                camera_position=getattr(s, "camera_position", None),
                camera_angle=getattr(s, "camera_angle", None),
                subject_angle=getattr(s, "subject_angle", None),
                subject_pose=getattr(s, "subject_pose", None),
                scene_description=getattr(s, "scene_description", None),
                camera_movement=getattr(s, "camera_movement", None),
                lighting=getattr(s, "lighting", None),
                visual_effects=getattr(s, "visual_effects", None),
                transition=getattr(s, "transition", None),
                dialogue=getattr(s, "dialogue", None),
                sound_effects=getattr(s, "sound_effects", None),
                narration=getattr(s, "narration", None),
                is_bridge=getattr(s, "is_bridge", False),
                character_ids=list(s.character_ids) if s.character_ids else [],
                style_guide=getattr(s, "style_guide", None),
            )
            shots.append(shot)
    shots.sort(key=lambda s: s.shot_number)
    return shots


async def main():
    from app.services.agent.video.keyframe_generation_service import (
        _build_character_images_dict,
        generate_batch_keyframe_prompts,
        resolve_keyframe_prompt_reference_images,
        execute_batch_keyframe_generation,
        KeyframePromptResult,
        process_ref_urls_with_sheet_detection,
    )
    from app.services.agent.utils.database_utils import get_characters_from_db

    shots = await load_shots_from_db()
    logger.info(f"Loaded {len(shots)} shots: {[s.shot_number for s in shots]}")
    for s in shots:
        logger.info(f"  Shot {s.shot_number}: {len(s.character_ids)} chars, desc={s.scene_description[:60] if s.scene_description else 'N/A'}...")

    all_char_ids = set()
    for s in shots:
        all_char_ids.update(s.character_ids)
    char_images = await _build_character_images_dict(all_char_ids, USER_ID)
    logger.info(f"Built character_images for {len(char_images)} characters")

    results = {}

    # ============================================================
    # TEST A: Pipeline 路径 (generate_prompts → resolve → execute)
    # ============================================================
    logger.info("\n" + "=" * 70)
    logger.info("TEST A: PIPELINE 路径 — generate → resolve → execute")
    logger.info("=" * 70)

    prompts_and_msgs = await generate_batch_keyframe_prompts(
        shots_batch=shots,
        character_images=char_images,
        prev_shot=None,
        next_shot=None,
        user_option=None,
        user_input="",
        generate_first_frame=True,
        generate_last_frame=False,
        first_frame_images=None,
        first_frame_prompts=None,
        user_id=USER_ID,
    )
    gen_messages, prompts = prompts_and_msgs
    logger.info(f"Generated {len(prompts)} prompts")
    for p in prompts:
        has_sheet_urls = bool(getattr(p, "shot_reference_image_urls", None) and
                             any("__ref_sheet__" in u for u in p.shot_reference_image_urls))
        logger.info(f"  Shot {p.shot_number}: t2i_prompt={len(p.t2i_prompt)} chars, has_sheet_in_urls={has_sheet_urls}")

    resolved_prompts = await resolve_keyframe_prompt_reference_images(
        shots_batch=shots,
        character_images=char_images,
        prompts=prompts,
        user_option=None,
        user_id=USER_ID,
    )
    for p in resolved_prompts:
        has_prefix = bool(p.final_prompt and "REFERENCE SHEET" in p.final_prompt)
        n_urls = len(p.final_reference_image_urls) if p.final_reference_image_urls else 0
        logger.info(f"  Resolved Shot {p.shot_number}: final_urls={n_urls}, has_prefix={has_prefix}")

    logger.info("Executing keyframe generation (calling model)...")
    exec_messages, keyframe_versions = await execute_batch_keyframe_generation(
        shots_batch=shots,
        character_images=char_images,
        prompts=resolved_prompts,
        user_option=None,
        shared_semaphore=None,
        user_input="",
        first_frame_images=None,
        user_id=USER_ID,
    )

    for kv in keyframe_versions:
        logger.info(
            f"  🎨 Pipeline Shot {kv.shot_number}: "
            f"success={kv.success}, url={kv.keyframe_url or 'NONE'}, "
            f"provider={kv.provider}, error={kv.error_msg or 'none'}"
        )
        results[f"pipeline_shot_{kv.shot_number}"] = {
            "path": "pipeline",
            "shot": kv.shot_number,
            "success": kv.success,
            "keyframe_url": kv.keyframe_url,
            "provider": kv.provider,
            "error": kv.error_msg,
            "t2i_prompt_len": len(kv.t2i_prompt) if kv.t2i_prompt else 0,
            "ref_urls": kv.reference_image_urls,
        }

    # ============================================================
    # TEST B: Regenerate 路径 (custom_prompt → resolve → execute)
    # ============================================================
    logger.info("\n" + "=" * 70)
    logger.info("TEST B: REGENERATE 路径 — custom_prompt → resolve → execute")
    logger.info("=" * 70)

    regen_targets = []
    over_limit = [s for s in shots if len(s.character_ids) > 4]
    under_limit = [s for s in shots if len(s.character_ids) <= 4]
    if over_limit:
        regen_targets.append(over_limit[0])
    if under_limit:
        regen_targets.append(under_limit[0])

    custom_prompt = (
        "A cinematic wide shot of a 1980s Tokyo nightscape. "
        "The male protagonist and female protagonist stand together beside a vintage car, "
        "with the city lights reflected in her pearl earrings. "
        "The bay coastline stretches into the distance. Warm amber streetlights, film grain, 16:9."
    )

    regen_prompts = []
    for rs in regen_targets:
        logger.info(f"Regenerate target: Shot {rs.shot_number} ({len(rs.character_ids)} chars)")
        regen_prompts.append(KeyframePromptResult(
            shot_number=rs.shot_number,
            t2i_prompt=custom_prompt,
            frame_index=0,
        ))

    resolved = await resolve_keyframe_prompt_reference_images(
        shots_batch=regen_targets,
        character_images=char_images,
        prompts=regen_prompts,
        user_option=None,
        user_id=USER_ID,
    )
    for p in resolved:
        has_prefix = bool(p.final_prompt and "REFERENCE SHEET" in p.final_prompt)
        n_urls = len(p.final_reference_image_urls) if p.final_reference_image_urls else 0
        logger.info(f"  Resolved Shot {p.shot_number}: final_urls={n_urls}, has_prefix={has_prefix}")

    logger.info("Executing regenerate keyframes (calling model)...")
    regen_messages, regen_versions = await execute_batch_keyframe_generation(
        shots_batch=regen_targets,
        character_images=char_images,
        prompts=resolved,
        user_option=None,
        shared_semaphore=None,
        user_input=custom_prompt,
        first_frame_images=None,
        user_id=USER_ID,
    )

    for kv in regen_versions:
        logger.info(
            f"  🎨 Regenerate Shot {kv.shot_number}: "
            f"success={kv.success}, url={kv.keyframe_url or 'NONE'}, "
            f"provider={kv.provider}, error={kv.error_msg or 'none'}"
        )
        results[f"regenerate_shot_{kv.shot_number}"] = {
            "path": "regenerate",
            "shot": kv.shot_number,
            "success": kv.success,
            "keyframe_url": kv.keyframe_url,
            "provider": kv.provider,
            "error": kv.error_msg,
            "t2i_prompt_len": len(kv.t2i_prompt) if kv.t2i_prompt else 0,
            "ref_urls": kv.reference_image_urls,
        }

    # ============================================================
    # Summary
    # ============================================================
    logger.info("\n" + "=" * 70)
    logger.info("FINAL RESULTS")
    logger.info("=" * 70)
    for key, r in results.items():
        logger.info(
            f"  [{r['path'].upper():>10}] Shot {r['shot']}: "
            f"{'✅' if r['success'] else '❌'} {r['keyframe_url'] or 'NO URL'}"
        )
        if r.get("ref_urls"):
            logger.info(f"             ref_images: {r['ref_urls']}")

    from app.models.database import close_asyncpg_pool
    await close_asyncpg_pool()

    return results


if __name__ == "__main__":
    r = asyncio.run(main())
    print("\n\n===== JSON RESULTS =====")
    print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
