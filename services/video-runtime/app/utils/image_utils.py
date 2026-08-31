"""
图像通用工具：下采样等，供 Nano Banana、Seedream、WaveSpeed 等复用。
"""
import io
import logging
from typing import Tuple

from PIL import Image

logger = logging.getLogger(__name__)

# 差异 < 此阈值时强制 resize 到目标尺寸（轻微拉伸，体感不可见）；>= 时保持等比不拉伸，避免明显形变
DOWNSCALE_FORCE_EXACT_THRESHOLD = 0.03  # 3%


def downsample_to_target_sync(image_bytes: bytes, target_width: int, target_height: int) -> Tuple[bytes, dict]:
    """关键帧下采样：优先对齐标准分辨率，保证 Pipeline 一致、避免 Assemble 黑边与奇数/非标像素。

    策略（工程对齐 vs 数学等比）：
    - 先按比例缩小到不超过目标框，得到 (new_w, new_h)。
    - 若与目标相对差异 < 3%：强制 resize 到 (target_width, target_height)，保证输出标准尺寸（<2% 拉伸视觉不可见）。
    - 若差异 >= 3%：保持等比输出，不拉伸（如 2.5 Flash 1080p 原图不足时保持 1344x768）。
    统一输出 webp。在 executor 中调用避免阻塞。
    返回 (输出字节, 元数据) 供日志与报告使用。
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    scale = min(target_width / w, target_height / h, 1.0)
    locked = False
    force_exact = False
    out_w, out_h = w, h
    if scale < 1.0:
        new_w = max(1, round(w * scale))
        new_h = max(1, round(h * scale))
        diff_w = abs(new_w - target_width) / target_width if target_width else 0
        diff_h = abs(new_h - target_height) / target_height if target_height else 0
        rel_diff = max(diff_w, diff_h)
        if rel_diff < DOWNSCALE_FORCE_EXACT_THRESHOLD:
            new_w, new_h = target_width, target_height
            force_exact = True
            locked = True
        else:
            locked = False
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        out_w, out_h = new_w, new_h
    meta = {
        "orig_w": w, "orig_h": h,
        "target_w": target_width, "target_h": target_height,
        "scale": scale, "locked": locked, "force_exact": force_exact,
        "out_w": out_w, "out_h": out_h,
    }
    logger.info(
        "下采样: 原图 %dx%d → 目标 %dx%d, scale=%.4f, locked=%s, force_exact=%s → 输出 %dx%d",
        w, h, target_width, target_height, scale, locked, force_exact, out_w, out_h,
    )
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=90)
    return buf.getvalue(), meta
