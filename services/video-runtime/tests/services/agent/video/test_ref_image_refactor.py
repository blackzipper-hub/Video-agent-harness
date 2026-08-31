"""
RefImageInfo / KeyframePromptResult 重构后的全面测试。
覆盖场景：
  1. RefImageInfo / RefImageRole 基础行为
  2. KeyframePromptResult computed properties (has_reference_sheet, sheet_index, all_ref_urls, display_ref_urls)
  3. Pipeline 首帧 — 角色数 ≤ model_limit（无 sheet）
  4. Pipeline 首帧 — 角色数 > model_limit（有 sheet）
  5. Pipeline 尾帧 — 角色数 > model_limit + 首帧（sheet + first_frame 共存）
  6. Regenerate（无 ref_images → resolve 填充）
  7. Regenerate 角色数 > model_limit（resolve 填充 sheet）
  8. Eval fix 保留 ref_images
  9. Execute display_ref_urls 排除 sheet
  10. Sheet 不在 [-1] 位置（尾帧场景）的正确处理

conda run -n cuti-video-local python -u tests/services/agent/video/test_ref_image_refactor.py
"""
import asyncio
import os
import sys
import logging

from dotenv import load_dotenv
load_dotenv(".env.development")
load_dotenv(".env.local", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("test_ref_image_refactor")

import app.services.agent.video.keyframe_generation_service  # noqa: F401

THREAD_ID = "08ac64a8-6451-4f0b-a4aa-b74dbecc4e4b"
USER_ID = "admin"

passed = 0
failed = 0

def check(desc, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        logger.info(f"  ✅ PASS: {desc}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        logger.error(f"  ❌ FAIL: {desc}" + (f" — {detail}" if detail else ""))


# ====================== TEST 1: 基础 class 行为 ======================
async def test_basic_class_behavior():
    logger.info("\n" + "=" * 70)
    logger.info("TEST 1: RefImageInfo / KeyframePromptResult 基础行为")
    logger.info("=" * 70)

    from app.services.agent.video.keyframe_generation_service import (
        RefImageInfo, RefImageRole, KeyframePromptResult,
    )

    # 1a: RefImageInfo 构建
    img_char = RefImageInfo(url="https://ex.com/a.webp", role=RefImageRole.CHARACTER, label="男主角")
    img_sheet = RefImageInfo(url="https://ex.com/sheet.webp", role=RefImageRole.SHEET, label="Reference Sheet")
    img_ff = RefImageInfo(url="https://ex.com/ff.webp", role=RefImageRole.FIRST_FRAME, label="首帧")
    check("RefImageInfo CHARACTER", img_char.role == RefImageRole.CHARACTER)
    check("RefImageInfo SHEET", img_sheet.role == RefImageRole.SHEET)
    check("RefImageInfo FIRST_FRAME", img_ff.role == RefImageRole.FIRST_FRAME)

    # 1b: 无图 — 空 ref_images
    p_empty = KeyframePromptResult(shot_number=1, t2i_prompt="test")
    check("Empty ref_images", p_empty.ref_images == [])
    check("Empty has_reference_sheet", p_empty.has_reference_sheet is False)
    check("Empty sheet_index", p_empty.sheet_index is None)
    check("Empty all_ref_urls", p_empty.all_ref_urls == [])
    check("Empty display_ref_urls", p_empty.display_ref_urls == [])

    # 1c: 纯角色图（无 sheet）
    p_chars = KeyframePromptResult(
        shot_number=2, t2i_prompt="test",
        ref_images=[img_char, RefImageInfo(url="https://ex.com/b.webp", role=RefImageRole.CHARACTER)]
    )
    check("Chars-only has_reference_sheet=False", p_chars.has_reference_sheet is False)
    check("Chars-only sheet_index=None", p_chars.sheet_index is None)
    check("Chars-only all_ref_urls", len(p_chars.all_ref_urls) == 2)
    check("Chars-only display_ref_urls == all_ref_urls", p_chars.display_ref_urls == p_chars.all_ref_urls)

    # 1d: 角色图 + sheet
    p_sheet = KeyframePromptResult(
        shot_number=3, t2i_prompt="test",
        ref_images=[img_char, RefImageInfo(url="https://ex.com/c.webp", role=RefImageRole.CHARACTER), img_sheet]
    )
    check("With-sheet has_reference_sheet=True", p_sheet.has_reference_sheet is True)
    check("With-sheet sheet_index=2", p_sheet.sheet_index == 2)
    check("With-sheet all_ref_urls=3", len(p_sheet.all_ref_urls) == 3)
    check("With-sheet display_ref_urls=2 (excludes sheet)", len(p_sheet.display_ref_urls) == 2)
    check("Sheet URL not in display", img_sheet.url not in p_sheet.display_ref_urls)

    # 1e: 角色图 + sheet + 首帧（尾帧场景）— sheet 不在 [-1]
    p_tail = KeyframePromptResult(
        shot_number=4, t2i_prompt="test",
        ref_images=[img_char, img_sheet, img_ff]
    )
    check("Tail-frame has_reference_sheet=True", p_tail.has_reference_sheet is True)
    check("Tail-frame sheet_index=1 (not [-1])", p_tail.sheet_index == 1)
    check("Tail-frame all_ref_urls=3", len(p_tail.all_ref_urls) == 3)
    check("Tail-frame display_ref_urls=2 (char + ff, no sheet)", len(p_tail.display_ref_urls) == 2)
    check("Tail-frame first_frame in display", img_ff.url in p_tail.display_ref_urls)
    check("Tail-frame sheet not in display", img_sheet.url not in p_tail.display_ref_urls)


# ====================== TEST 2: Pipeline 首帧 ======================
async def test_pipeline_first_frame():
    logger.info("\n" + "=" * 70)
    logger.info("TEST 2: Pipeline 首帧（generate → resolve → execute 属性验证）")
    logger.info("=" * 70)

    from app.services.agent.video.keyframe_generation_service import (
        _build_character_images_dict,
        generate_batch_keyframe_prompts,
        resolve_keyframe_prompt_reference_images,
        RefImageRole,
    )
    from app.models.database import init_asyncpg_pool, get_asyncpg_pool, close_asyncpg_pool

    await init_asyncpg_pool()
    pool = await get_asyncpg_pool()
    run_id = await pool.fetchval(f"SELECT run_id FROM video_detailed_shots WHERE thread_id = '{THREAD_ID}' LIMIT 1")

    rows = await pool.fetch(
        "SELECT * FROM video_detailed_shots WHERE run_id = $1 ORDER BY shot_number",
        run_id
    )
    from app.crud.video.video_story import _row_to_shot
    all_shots = [_row_to_shot(r) for r in rows]

    all_char_ids = set()
    for s in all_shots:
        all_char_ids.update(s.character_ids or [])
    char_images = await _build_character_images_dict(all_char_ids, USER_ID)

    over_limit = [s for s in all_shots if len(s.character_ids or []) > 4]
    under_limit = [s for s in all_shots if 1 <= len(s.character_ids or []) <= 4]
    test_shots = (over_limit[:1] if over_limit else []) + (under_limit[:1] if under_limit else [])
    logger.info(f"Test shots: {[(s.shot_number, len(s.character_ids)) for s in test_shots]}")

    _, prompts = await generate_batch_keyframe_prompts(
        shots_batch=test_shots, character_images=char_images,
        user_option=None, user_input="", generate_first_frame=True,
        generate_last_frame=False, user_id=USER_ID,
    )

    for p in prompts:
        n_chars = len(next((s.character_ids for s in test_shots if s.shot_number == p.shot_number), []))
        check(f"Pipeline shot {p.shot_number}: ref_images populated", len(p.ref_images) > 0)

        char_count = sum(1 for img in p.ref_images if img.role == RefImageRole.CHARACTER)
        sheet_count = sum(1 for img in p.ref_images if img.role == RefImageRole.SHEET)
        ff_count = sum(1 for img in p.ref_images if img.role == RefImageRole.FIRST_FRAME)
        check(f"Pipeline shot {p.shot_number}: no first_frame (首帧生成)", ff_count == 0)

        if n_chars > 4:
            check(f"Pipeline shot {p.shot_number}: has sheet ({n_chars} chars > 4)", sheet_count == 1)
            check(f"Pipeline shot {p.shot_number}: char_count = model_limit-1 = 3", char_count == 3)
        else:
            check(f"Pipeline shot {p.shot_number}: no sheet ({n_chars} chars ≤ 4)", sheet_count == 0)
            check(f"Pipeline shot {p.shot_number}: char_count = {n_chars}", char_count == n_chars)

    resolved = await resolve_keyframe_prompt_reference_images(
        shots_batch=test_shots, character_images=char_images,
        prompts=prompts, user_option=None, user_id=USER_ID,
    )

    for p in resolved:
        check(f"Resolved shot {p.shot_number}: final_prompt set", p.final_prompt is not None and len(p.final_prompt) > 0)
        if p.has_reference_sheet:
            check(
                f"Resolved shot {p.shot_number}: final_prompt has REFERENCE_SHEET prefix",
                "REFERENCE SHEET" in p.final_prompt
            )

    await close_asyncpg_pool()


# ====================== TEST 3: Regenerate 路径 ======================
async def test_regenerate_path():
    logger.info("\n" + "=" * 70)
    logger.info("TEST 3: Regenerate 路径（resolve 填充 ref_images）")
    logger.info("=" * 70)

    from app.services.agent.video.keyframe_generation_service import (
        _build_character_images_dict,
        resolve_keyframe_prompt_reference_images,
        KeyframePromptResult,
        RefImageRole,
    )
    from app.models.database import init_asyncpg_pool, get_asyncpg_pool, close_asyncpg_pool
    from app.crud.video.video_story import _row_to_shot

    await init_asyncpg_pool()
    pool = await get_asyncpg_pool()
    run_id = await pool.fetchval(f"SELECT run_id FROM video_detailed_shots WHERE thread_id = '{THREAD_ID}' LIMIT 1")
    rows = await pool.fetch(
        "SELECT * FROM video_detailed_shots WHERE run_id = $1 ORDER BY shot_number",
        run_id
    )
    all_shots = [_row_to_shot(r) for r in rows]

    over_limit = [s for s in all_shots if len(s.character_ids or []) > 4]
    under_limit = [s for s in all_shots if 1 <= len(s.character_ids or []) <= 4]
    regen_shots = (over_limit[:1] if over_limit else []) + (under_limit[:1] if under_limit else [])

    all_char_ids = set()
    for s in regen_shots:
        all_char_ids.update(s.character_ids or [])
    char_images = await _build_character_images_dict(all_char_ids, USER_ID)

    custom_prompt = "A cinematic scene with all characters. Warm lighting, 16:9."
    prompts = [
        KeyframePromptResult(shot_number=s.shot_number, t2i_prompt=custom_prompt, frame_index=0)
        for s in regen_shots
    ]

    for p in prompts:
        check(f"Regen shot {p.shot_number} pre-resolve: ref_images empty", len(p.ref_images) == 0)

    resolved = await resolve_keyframe_prompt_reference_images(
        shots_batch=regen_shots, character_images=char_images,
        prompts=prompts, user_option=None, user_id=USER_ID,
    )

    for p in resolved:
        n_chars = len(next((s.character_ids for s in regen_shots if s.shot_number == p.shot_number), []))
        check(f"Regen shot {p.shot_number} post-resolve: ref_images filled", len(p.ref_images) > 0)
        check(f"Regen shot {p.shot_number}: final_prompt set", p.final_prompt is not None)

        if n_chars > 4:
            check(f"Regen shot {p.shot_number}: has sheet", p.has_reference_sheet)
            check(f"Regen shot {p.shot_number}: sheet_index not None", p.sheet_index is not None)
            check(f"Regen shot {p.shot_number}: display_ref_urls < all_ref_urls",
                  len(p.display_ref_urls) < len(p.all_ref_urls))
        else:
            check(f"Regen shot {p.shot_number}: no sheet", not p.has_reference_sheet)
            check(f"Regen shot {p.shot_number}: display == all", p.display_ref_urls == p.all_ref_urls)

        for img in p.ref_images:
            check(f"Regen shot {p.shot_number}: role is valid", img.role in (RefImageRole.CHARACTER, RefImageRole.SHEET))

    await close_asyncpg_pool()


# ====================== TEST 4: Eval fix 保留 ref_images ======================
async def test_eval_fix_preserves_ref_images():
    logger.info("\n" + "=" * 70)
    logger.info("TEST 4: Eval fix 保留 ref_images")
    logger.info("=" * 70)

    from app.services.agent.video.keyframe_generation_service import (
        KeyframePromptResult, RefImageInfo, RefImageRole,
    )

    original = KeyframePromptResult(
        shot_number=1,
        t2i_prompt="Original prompt text",
        frame_index=0,
        ref_images=[
            RefImageInfo(url="https://ex.com/a.webp", role=RefImageRole.CHARACTER, label="男主角"),
            RefImageInfo(url="https://ex.com/b.webp", role=RefImageRole.CHARACTER, label="女主角"),
            RefImageInfo(url="https://ex.com/sheet.webp", role=RefImageRole.SHEET, label="Reference Sheet"),
        ],
    )

    fixed = KeyframePromptResult(
        shot_number=original.shot_number,
        t2i_prompt="Fixed prompt text (censored)",
        frame_index=original.frame_index,
        ref_images=list(original.ref_images),
    )

    check("Fix: t2i_prompt changed", fixed.t2i_prompt != original.t2i_prompt)
    check("Fix: ref_images preserved", len(fixed.ref_images) == len(original.ref_images))
    check("Fix: has_reference_sheet preserved", fixed.has_reference_sheet == original.has_reference_sheet)
    check("Fix: sheet_index preserved", fixed.sheet_index == original.sheet_index)
    check("Fix: all_ref_urls preserved", fixed.all_ref_urls == original.all_ref_urls)
    check("Fix: display_ref_urls preserved", fixed.display_ref_urls == original.display_ref_urls)


# ====================== TEST 5: Tail frame — sheet + first_frame 共存 ======================
async def test_tail_frame_sheet_and_first_frame():
    logger.info("\n" + "=" * 70)
    logger.info("TEST 5: 尾帧 — sheet 和 first_frame 共存时 sheet_index 正确")
    logger.info("=" * 70)

    from app.services.agent.video.keyframe_generation_service import (
        KeyframePromptResult, RefImageInfo, RefImageRole,
    )

    p = KeyframePromptResult(
        shot_number=5,
        t2i_prompt="Tail frame prompt",
        frame_index=-1,
        ref_images=[
            RefImageInfo(url="https://ex.com/c1.webp", role=RefImageRole.CHARACTER, label="角色1"),
            RefImageInfo(url="https://ex.com/c2.webp", role=RefImageRole.CHARACTER, label="角色2"),
            RefImageInfo(url="https://ex.com/c3.webp", role=RefImageRole.CHARACTER, label="角色3"),
            RefImageInfo(url="https://ex.com/sheet.webp", role=RefImageRole.SHEET, label="Sheet"),
            RefImageInfo(url="https://ex.com/ff.webp", role=RefImageRole.FIRST_FRAME, label="首帧"),
        ],
    )

    check("Tail: has_reference_sheet=True", p.has_reference_sheet is True)
    check("Tail: sheet_index=3 (not 4/-1)", p.sheet_index == 3)
    check("Tail: all_ref_urls=5", len(p.all_ref_urls) == 5)
    check("Tail: display_ref_urls=4 (3 chars + 1 ff, no sheet)", len(p.display_ref_urls) == 4)
    check("Tail: sheet URL not in display", "https://ex.com/sheet.webp" not in p.display_ref_urls)
    check("Tail: first_frame URL in display", "https://ex.com/ff.webp" in p.display_ref_urls)

    p.final_prompt = p.t2i_prompt
    check("Tail: final_prompt is t2i without Program sheet prefix", p.final_prompt == p.t2i_prompt)


# ====================== TEST 6: Serialization roundtrip ======================
async def test_serialization():
    logger.info("\n" + "=" * 70)
    logger.info("TEST 6: Pydantic serialization roundtrip")
    logger.info("=" * 70)

    from app.services.agent.video.keyframe_generation_service import (
        KeyframePromptResult, RefImageInfo, RefImageRole,
    )

    p = KeyframePromptResult(
        shot_number=1,
        t2i_prompt="test prompt",
        frame_index=0,
        ref_images=[
            RefImageInfo(url="https://ex.com/a.webp", role=RefImageRole.CHARACTER, label="char1"),
            RefImageInfo(url="https://ex.com/sheet.webp", role=RefImageRole.SHEET, label="sheet"),
        ],
        final_prompt="final test",
    )

    d = p.model_dump()
    check("Serialization: ref_images in dict", "ref_images" in d)
    check("Serialization: ref_images has 2 items", len(d["ref_images"]) == 2)
    check("Serialization: role is string", d["ref_images"][0]["role"] == "character")
    check("Serialization: no old fields",
          "shot_reference_image_urls" not in d and "batch_global_image_urls" not in d and "reference_image_urls" not in d)

    p2 = KeyframePromptResult.model_validate(d)
    check("Deserialization: ref_images preserved", len(p2.ref_images) == 2)
    check("Deserialization: has_reference_sheet", p2.has_reference_sheet is True)
    check("Deserialization: sheet_index=1", p2.sheet_index == 1)


# ====================== MAIN ======================
async def main():
    await test_basic_class_behavior()
    await test_pipeline_first_frame()
    await test_regenerate_path()
    await test_eval_fix_preserves_ref_images()
    await test_tail_frame_sheet_and_first_frame()
    await test_serialization()

    logger.info("\n" + "=" * 70)
    logger.info(f"SUMMARY: {passed} passed, {failed} failed")
    logger.info("=" * 70)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
