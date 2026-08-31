"""
Reference Sheet 拼接 vs AI融合图 对比测试

1. 选一个超限 shot (Shot 9: 6个角色)
2. 方案A: 用 AI 融合图生成 keyframe（已有的逻辑）
3. 方案B: 用 Reference Sheet 拼接图生成 keyframe
4. 对比两者效果

conda run -n cuti-video-local python tests/services/agent/video/run_sheet_keyframe_test.py
"""
import asyncio
import os
import json
import logging
import math
import tempfile
from io import BytesIO
from typing import List, Tuple, Optional

from dotenv import load_dotenv
load_dotenv(".env.development")
load_dotenv(".env.local", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("sheet_test")

THREAD_ID = "full-803c8251-eff-eff_0"
RUN_ID = "787bf76b-927f-47a1-9948-b06fd5a41a14"
USER_ID = "admin"

import app.services.agent.video.keyframe_generation_service  # noqa: F401


def create_reference_sheet(
    images: List["Image.Image"],
    labels: Optional[List[str]] = None,
    max_cols: int = 3,
    cell_size: Tuple[int, int] = (512, 512),
    padding: int = 8,
    bg_color: Tuple[int, int, int] = (255, 255, 255),
) -> "Image.Image":
    """将多张图片拼成 grid reference sheet

    每张图 resize 到 cell_size 保持比例居中，可选标签。
    """
    from PIL import Image, ImageDraw, ImageFont

    n = len(images)
    cols = min(n, max_cols)
    rows = math.ceil(n / cols)
    label_h = 28 if labels else 0

    canvas_w = cols * cell_size[0] + (cols + 1) * padding
    canvas_h = rows * (cell_size[1] + label_h) + (rows + 1) * padding
    canvas = Image.new("RGB", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(canvas)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    for idx, img in enumerate(images):
        col = idx % cols
        row = idx // cols
        x0 = padding + col * (cell_size[0] + padding)
        y0 = padding + row * (cell_size[1] + label_h + padding)

        img_ratio = img.width / img.height
        cell_ratio = cell_size[0] / cell_size[1]
        if img_ratio > cell_ratio:
            new_w = cell_size[0]
            new_h = int(cell_size[0] / img_ratio)
        else:
            new_h = cell_size[1]
            new_w = int(cell_size[1] * img_ratio)
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        offset_x = x0 + (cell_size[0] - new_w) // 2
        offset_y = y0 + (cell_size[1] - new_h) // 2
        canvas.paste(resized, (offset_x, offset_y))

        if labels and idx < len(labels):
            text_x = x0 + cell_size[0] // 2
            text_y = y0 + cell_size[1] + 4
            draw.text((text_x, text_y), labels[idx], fill=(40, 40, 40), font=font, anchor="mt")

    return canvas


async def download_image(url: str) -> "Image.Image":
    from PIL import Image
    from app.utils.s3_utils import s3_utils

    tmp = tempfile.NamedTemporaryFile(suffix=".webp", delete=False)
    tmp.close()
    ok = await s3_utils.download_file(url, tmp.name)
    if not ok:
        os.unlink(tmp.name)
        raise ValueError(f"Cannot download: {url}")
    img = Image.open(tmp.name).convert("RGB")
    os.unlink(tmp.name)
    return img


async def upload_image(img: "Image.Image", prefix: str = "test-sheet") -> str:
    from app.utils.s3_utils import s3_utils
    import uuid as _uuid

    buf = BytesIO()
    img.save(buf, format="WEBP", quality=90)
    data = buf.getvalue()

    key = f"images/{prefix}-{_uuid.uuid4()}.webp"
    cdn_url = await s3_utils.upload_file(data, key, content_type="image/webp")
    return cdn_url


async def main():
    from PIL import Image
    from app.models.database import init_asyncpg_pool, close_asyncpg_pool
    await init_asyncpg_pool()
    import asyncpg

    DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/storybook_dev")

    from app.services.agent.video.keyframe_generation_service import (
        get_character_ref_images, _build_character_images_dict
    )
    from app.crud.video.video_character import get_character_fusion_images_by_character_ids
    from app.services.agent.video.character_fusion_service import get_model_limit
    from app.services.agent.utils.database_utils import get_characters_from_db
    from unittest.mock import MagicMock

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

    # 选 Shot 9 (6 个角色，最复杂的超限场景)
    target_shot = None
    for s in shots:
        if s["shot_number"] == 9:
            target_shot = s
            break
    if not target_shot:
        logger.error("Shot 9 not found!")
        return

    cids = target_shot["character_ids"] if isinstance(target_shot["character_ids"], list) else json.loads(target_shot["character_ids"])
    char_names = [char_name_map.get(cid, cid[:8]) for cid in cids]
    logger.info(f"目标: Shot {target_shot['shot_number']}, {len(cids)} 个角色: {char_names}")

    # ==================== 构建 Reference Sheet ====================
    logger.info("=" * 60)
    logger.info("Step 1: 构建 Reference Sheet 拼接图")
    logger.info("=" * 60)

    ref_urls = []
    ref_labels = []
    for cid in cids:
        ci = char_images.get(cid)
        if ci and ci.main_image_url:
            ref_urls.append(ci.main_image_url)
            ref_labels.append(char_name_map.get(cid, cid[:8]))

    logger.info(f"  下载 {len(ref_urls)} 张原始参考图...")
    pil_images = await asyncio.gather(*[download_image(url) for url in ref_urls])
    logger.info(f"  拼接 reference sheet (grid layout)...")
    sheet = create_reference_sheet(
        images=list(pil_images),
        labels=ref_labels,
        max_cols=3,
        cell_size=(512, 512),
        padding=12,
    )
    logger.info(f"  Sheet 尺寸: {sheet.width}x{sheet.height}")

    sheet_url = await upload_image(sheet, prefix="ref-sheet")
    logger.info(f"  上传完成: {sheet_url}")

    sheet.save("/tmp/reference_sheet.png")
    logger.info(f"  本地保存: /tmp/reference_sheet.png")

    # ==================== 方案 A: AI融合图 keyframe ====================
    logger.info("=" * 60)
    logger.info("Step 2A: 用 AI 融合图 生成 keyframe (现有逻辑)")
    logger.info("=" * 60)

    all_char_ids = list({cid for s in shots for cid in (s["character_ids"] if isinstance(s["character_ids"], list) else json.loads(s["character_ids"]))})
    pre_fetched_fusions = await get_character_fusion_images_by_character_ids(character_ids=all_char_ids)

    mock_shot = MagicMock()
    mock_shot.shot_number = target_shot["shot_number"]
    mock_shot.character_ids = cids

    fusion_refs = await get_character_ref_images(
        mock_shot, char_images, model_limit=model_limit, user_id=USER_ID,
        pre_fetched_fusions=pre_fetched_fusions,
    )
    logger.info(f"  AI融合图方案: {len(fusion_refs)} 张参考图")

    # 用 Gemini Flash 直接生成 keyframe (简化测试，不走完整 agent 流程)
    from google import genai
    from google.genai import types

    client = genai.Client()

    test_prompt = (
        "A cinematic 1980s Tokyo night scene. The male protagonist in a dark green shirt stands "
        "next to a red vintage car on a coastal highway. The female protagonist with red-tipped bob hair "
        "watches from across the road. Pearl earrings glint under neon lights. The bay and Tokyo tower "
        "are visible in the background. Warm film grain, Wong Kar-wai style lighting. 16:9 aspect ratio."
    )

    async def generate_keyframe_with_refs(ref_image_urls: List[str], label: str) -> Optional[str]:
        parts = []
        for url in ref_image_urls:
            try:
                img = await download_image(url)
                buf = BytesIO()
                img.save(buf, format="PNG")
                parts.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"))
            except Exception as e:
                logger.warning(f"  下载参考图失败: {e}")

        parts.append(types.Part.from_text(text=test_prompt))

        logger.info(f"  [{label}] 调用 Gemini Flash, {len(parts)-1} 张参考图...")
        response = await client.aio.models.generate_content(
            model="gemini-3.1-flash-image-preview",
            contents=parts,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                img = Image.open(BytesIO(part.inline_data.data))
                out_path = f"/tmp/keyframe_{label}.png"
                img.save(out_path)
                logger.info(f"  [{label}] 关键帧已保存: {out_path} ({img.width}x{img.height})")

                out_url = await upload_image(img, prefix=f"keyframe-{label}")
                logger.info(f"  [{label}] CDN: {out_url}")
                return out_path
        logger.warning(f"  [{label}] 未返回图片")
        return None

    result_a = await generate_keyframe_with_refs(fusion_refs, "fusion")

    # ==================== 方案 B: Reference Sheet keyframe (原始 prompt) ====================
    logger.info("=" * 60)
    logger.info("Step 2B: 用 Reference Sheet 拼接图 生成 keyframe (原始 prompt)")
    logger.info("=" * 60)

    result_b = await generate_keyframe_with_refs([sheet_url], "sheet")

    # ==================== 方案 B2: Reference Sheet + 强调 prompt ====================
    logger.info("=" * 60)
    logger.info("Step 2B2: 用 Reference Sheet + 强化 prompt 生成 keyframe")
    logger.info("=" * 60)

    sheet_enhanced_prompt = (
        "IMPORTANT: The attached image is a CHARACTER REFERENCE SHEET — it is a grid containing "
        "separate reference photos of different characters and objects. Each cell shows ONE character/object. "
        "DO NOT replicate this grid layout. DO NOT create a multi-panel or storyboard image. "
        "Instead, use each character/object from the reference sheet as visual reference to generate "
        "a SINGLE coherent cinematic scene:\n\n"
        + test_prompt
    )

    async def generate_keyframe_with_enhanced_prompt(ref_image_urls: List[str], label: str, custom_prompt: str) -> Optional[str]:
        parts = []
        for url in ref_image_urls:
            try:
                img = await download_image(url)
                buf = BytesIO()
                img.save(buf, format="PNG")
                parts.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"))
            except Exception as e:
                logger.warning(f"  下载参考图失败: {e}")

        parts.append(types.Part.from_text(text=custom_prompt))

        logger.info(f"  [{label}] 调用 Gemini Flash, {len(parts)-1} 张参考图...")
        response = await client.aio.models.generate_content(
            model="gemini-3.1-flash-image-preview",
            contents=parts,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                img = Image.open(BytesIO(part.inline_data.data))
                out_path = f"/tmp/keyframe_{label}.png"
                img.save(out_path)
                logger.info(f"  [{label}] 关键帧已保存: {out_path} ({img.width}x{img.height})")

                out_url = await upload_image(img, prefix=f"keyframe-{label}")
                logger.info(f"  [{label}] CDN: {out_url}")
                return out_path
        logger.warning(f"  [{label}] 未返回图片")
        return None

    result_b2 = await generate_keyframe_with_enhanced_prompt([sheet_url], "sheet_v2", sheet_enhanced_prompt)

    # ==================== 方案 C: 原始图片直接传 (baseline) ====================
    logger.info("=" * 60)
    logger.info("Step 2C: 原始图片直接传 (baseline, 可能超限)")
    logger.info("=" * 60)

    result_c = await generate_keyframe_with_refs(ref_urls[:4], "baseline_4img")

    # ==================== 总结 ====================
    logger.info("=" * 60)
    logger.info("对比结果:")
    logger.info(f"  方案A  (AI融合图):              {result_a}")
    logger.info(f"  方案B  (Reference Sheet 原始):  {result_b}")
    logger.info(f"  方案B2 (Reference Sheet 强化):  {result_b2}")
    logger.info(f"  方案C  (原始4张):               {result_c}")
    logger.info("请查看 /tmp/keyframe_*.png 和 /tmp/reference_sheet.png 对比效果")
    logger.info("=" * 60)

    await pool.close()
    await close_asyncpg_pool()


if __name__ == "__main__":
    asyncio.run(main())
