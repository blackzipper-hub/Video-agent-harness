"""
测试三个改动:
1. Reference Sheet URL 不存入 KeyframeVersion.reference_image_urls（不展示给前端）
2. Reference Sheet PIL 拼接使用 CJK 字体（中文标签不显示方块）
3. IMAGE_CHARACTER_CONSISTENCY_CHECK / VIDEO_CONSISTENCY_CHECK 的 model 改为 gemini-2.5-flash

conda run -n cuti-video-local python -u tests/services/agent/video/test_three_fixes.py
"""
import asyncio
import os
import sys
import logging
import tempfile

from dotenv import load_dotenv
load_dotenv(".env.development")
load_dotenv(".env.local", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("test_three_fixes")

import app.services.agent.video.keyframe_generation_service  # noqa: F401

THREAD_ID = "08ac64a8-6451-4f0b-a4aa-b74dbecc4e4b"
RUN_ID_QUERY = f"SELECT run_id FROM video_detailed_shots WHERE thread_id = '{THREAD_ID}' LIMIT 1"
USER_ID = "admin"
TEST_SHOTS = [4, 8, 12, 14, 18]

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        logger.info(f"  ✅ PASS: {name}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        logger.error(f"  ❌ FAIL: {name}" + (f" — {detail}" if detail else ""))


# ========================================================
# TEST 1: Reference Sheet URL 不存入前端可见的 reference_image_urls
# ========================================================
async def test_sheet_url_not_in_display_refs():
    logger.info("\n" + "=" * 70)
    logger.info("TEST 1: Reference Sheet URL 不展示给前端")
    logger.info("=" * 70)

    from app.services.agent.video.keyframe_generation_service import (
        _build_character_images_dict,
        generate_batch_keyframe_prompts,
        resolve_keyframe_prompt_reference_images,
        execute_batch_keyframe_generation,
        REFERENCE_SHEET_URL_TAG,
    )
    from app.models.database import init_asyncpg_pool
    await init_asyncpg_pool()

    import asyncpg
    DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/storybook_dev")
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        run_row = await conn.fetchrow(
            "SELECT run_id FROM video_detailed_shots WHERE thread_id = $1 LIMIT 1", THREAD_ID,
        )
        if not run_row:
            logger.error(f"No shots for thread_id={THREAD_ID}")
            return
        run_id = run_row["run_id"]
        rows = await conn.fetch(
            "SELECT * FROM video_detailed_shots WHERE run_id = $1 ORDER BY shot_number", run_id,
        )
    await pool.close()

    from app.crud.video.video_story import _row_to_shot
    from app.models.video_state import DetailedShot
    all_shots_db = [_row_to_shot(dict(r)) for r in rows if r]
    all_shots_db = [s for s in all_shots_db if s is not None]
    shots = []
    for s in all_shots_db:
        if s.shot_number in TEST_SHOTS:
            shots.append(DetailedShot(
                uuid=s.uuid, scene_id=s.scene_id,
                storyboard_detail_id=s.storyboard_detail_id,
                shot_number=s.shot_number, duration=s.duration,
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
            ))
    shots.sort(key=lambda s: s.shot_number)
    logger.info(f"Loaded {len(shots)} shots: {[s.shot_number for s in shots]}")

    all_char_ids = set()
    for s in shots:
        all_char_ids.update(s.character_ids)
    char_images = await _build_character_images_dict(all_char_ids, USER_ID)

    over_limit_shots = [s for s in shots if len(s.character_ids) > 4]
    under_limit_shots = [s for s in shots if len(s.character_ids) <= 4]
    test_shots = (over_limit_shots[:2] if over_limit_shots else []) + (under_limit_shots[:1] if under_limit_shots else [])
    if not test_shots:
        logger.warning("No suitable test shots found")
        return

    logger.info(f"Testing with shots: {[(s.shot_number, len(s.character_ids)) for s in test_shots]}")

    gen_messages, prompts = await generate_batch_keyframe_prompts(
        shots_batch=test_shots, character_images=char_images,
        user_option=None, user_input="", generate_first_frame=True,
        generate_last_frame=False, user_id=USER_ID,
    )

    resolved = await resolve_keyframe_prompt_reference_images(
        shots_batch=test_shots, character_images=char_images,
        prompts=prompts, user_option=None, user_id=USER_ID,
    )

    for p in resolved:
        n_chars = len(next((s.character_ids for s in test_shots if s.shot_number == p.shot_number), []))
        logger.info(
            f"  Shot {p.shot_number}: chars={n_chars}, has_sheet={p.has_reference_sheet}, "
            f"sheet_index={p.sheet_index}, "
            f"ref_images={len(p.ref_images)}, all_ref_urls={len(p.all_ref_urls)}"
        )
        if n_chars > 4:
            check(
                f"Shot {p.shot_number} has_reference_sheet=True (chars={n_chars}>4)",
                p.has_reference_sheet,
            )
            check(
                f"Shot {p.shot_number} sheet_index is not None",
                p.sheet_index is not None,
                f"sheet_index={p.sheet_index}",
            )

    logger.info("Executing keyframe generation...")
    exec_messages, kf_versions = await execute_batch_keyframe_generation(
        shots_batch=test_shots, character_images=char_images,
        prompts=resolved, user_option=None, shared_semaphore=None,
        user_input="", user_id=USER_ID,
    )

    for kv in kf_versions:
        shot = next((s for s in test_shots if s.shot_number == kv.shot_number), None)
        prompt = next((p for p in resolved if p.shot_number == kv.shot_number), None)
        if not shot or not prompt:
            continue
        n_chars = len(shot.character_ids)
        stored_urls = kv.reference_image_urls or []
        model_urls = prompt.all_ref_urls
        display_urls = prompt.display_ref_urls

        logger.info(
            f"  Shot {kv.shot_number}: success={kv.success}, "
            f"model_urls={len(model_urls)}, "
            f"stored_urls(for frontend)={len(stored_urls)}, "
            f"url={kv.keyframe_url[:80] if kv.keyframe_url else 'NONE'}..."
        )

        if prompt.has_reference_sheet and prompt.sheet_index is not None:
            check(
                f"Shot {kv.shot_number}: stored refs < model refs (sheet excluded)",
                len(stored_urls) < len(model_urls),
                f"stored={len(stored_urls)} < model={len(model_urls)}",
            )
            sheet_url = model_urls[prompt.sheet_index]
            check(
                f"Shot {kv.shot_number}: sheet URL (idx={prompt.sheet_index}) NOT in stored refs",
                sheet_url not in stored_urls,
                f"sheet_url={sheet_url[:60]}...",
            )
        else:
            check(
                f"Shot {kv.shot_number}: no sheet → stored refs == model refs",
                len(stored_urls) == len(model_urls),
                f"stored={len(stored_urls)} == model={len(model_urls)}",
            )


# ========================================================
# TEST 2: Reference Sheet PIL CJK 字体
# ========================================================
async def test_reference_sheet_cjk_font():
    logger.info("\n" + "=" * 70)
    logger.info("TEST 2: Reference Sheet PIL CJK 字体（中文标签不显示方块）")
    logger.info("=" * 70)

    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default()
    for _font_path in [
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        try:
            font = ImageFont.truetype(_font_path, 16)
            logger.info(f"  Loaded font: {_font_path}")
            break
        except Exception:
            continue

    canvas = Image.new("RGB", (400, 100), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    test_labels = ["女主角", "男主角", "珍珠耳环", "Tokyo夜景"]
    x = 10
    for label in test_labels:
        draw.text((x, 40), label, fill=(40, 40, 40), font=font)
        x += 100

    out_path = tempfile.mktemp(suffix=".png")
    canvas.save(out_path)
    logger.info(f"  Saved test image to: {out_path}")

    canvas_reload = Image.open(out_path)
    pixels = list(canvas_reload.getdata())
    non_white = sum(1 for p in pixels if p != (255, 255, 255))
    check(
        "CJK labels render non-trivially (not all white)",
        non_white > 50,
        f"non_white_pixels={non_white}",
    )

    font_name = getattr(font, "path", "default")
    check(
        "Font is NOT DejaVuSans (which lacks CJK)",
        "DejaVu" not in str(font_name),
        f"font={font_name}",
    )
    os.unlink(out_path)


# ========================================================
# TEST 3: 3.1 pro → 2.5 flash
# ========================================================
async def test_consistency_model_changed():
    logger.info("\n" + "=" * 70)
    logger.info("TEST 3: IMAGE_CHARACTER_CONSISTENCY_CHECK / VIDEO_CONSISTENCY_CHECK → gemini-2.5-flash")
    logger.info("=" * 70)

    from prompts.prompt_config import PromptName, PROMPTS_CONFIG

    img_cfg = PROMPTS_CONFIG.get(PromptName.IMAGE_CHARACTER_CONSISTENCY_CHECK, {})
    vid_cfg = PROMPTS_CONFIG.get(PromptName.VIDEO_CONSISTENCY_CHECK, {})

    img_model = img_cfg.get("model_config", {}).get("model", "UNKNOWN")
    vid_model = vid_cfg.get("model_config", {}).get("model", "UNKNOWN")

    check(
        "IMAGE_CHARACTER_CONSISTENCY_CHECK model = gemini-2.5-flash",
        img_model == "gemini-2.5-flash",
        f"actual={img_model}",
    )
    check(
        "VIDEO_CONSISTENCY_CHECK model = gemini-2.5-flash",
        vid_model == "gemini-2.5-flash",
        f"actual={vid_model}",
    )


async def main():
    logger.info("开始测试三个改动...\n")

    await test_consistency_model_changed()
    await test_reference_sheet_cjk_font()
    await test_sheet_url_not_in_display_refs()

    from app.models.database import close_asyncpg_pool
    await close_asyncpg_pool()

    logger.info("\n" + "=" * 70)
    logger.info(f"SUMMARY: {passed} passed, {failed} failed")
    logger.info("=" * 70)
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
