"""
GPT Image 2：产品 resolution / aspect_ratio 与 WaveSpeed API 档位、下采样目标的映射。

API 使用 resolution=1k|2k|4k（与产品 480p/720p/1080p 命名不同）；成图后由 Media Service 缩放到 TARGET_PIXELS。

选档策略与 Nano Banana 的 `convert_resolution_to_nano_banana_size` 一致：在实测输出栅格上，
取满足 TARGET 宽高的**最小** API 档位，避免无谓 4k 计费。档位输出像素来自
`scripts/gpt_image_2_specs_results.json`（WaveSpeed openai/gpt-image-2 text-to-image, quality=medium）。

本模块只依赖 tool_enums，供 cost 推算、单测 import，不拉起 Redis / account_router。
"""
from typing import Optional, Tuple, Union

from app.models.tool_enums import AspectRatio, Resolution, TARGET_PIXELS

# WaveSpeed GPT Image 2 各档实测输出 (W×H)，与 tool-output-specs §3.5 一致
_GPT_IMAGE_2_OUTPUT_PIXELS: dict[tuple[AspectRatio, str], tuple[int, int]] = {
    (AspectRatio.LANDSCAPE, "1k"): (1360, 768),
    (AspectRatio.SQUARE, "1k"): (1024, 1024),
    (AspectRatio.PORTRAIT, "1k"): (768, 1360),
    (AspectRatio.LANDSCAPE, "2k"): (2560, 1440),
    (AspectRatio.SQUARE, "2k"): (1920, 1920),
    (AspectRatio.PORTRAIT, "2k"): (1440, 2560),
    (AspectRatio.LANDSCAPE, "4k"): (3840, 2160),
    (AspectRatio.SQUARE, "4k"): (2880, 2880),
    (AspectRatio.PORTRAIT, "4k"): (2160, 3840),
}

_GPT_IMAGE_2_TIER_ORDER: tuple[str, ...] = ("1k", "2k", "4k")


def _coerce_resolution(resolution: Union[Resolution, str]) -> Resolution:
    try:
        return Resolution(resolution) if isinstance(resolution, str) else resolution
    except (ValueError, TypeError):
        return Resolution.P1080


def _coerce_aspect_ratio(aspect_ratio: Union[AspectRatio, str, None]) -> AspectRatio:
    if aspect_ratio is None:
        return AspectRatio.LANDSCAPE
    try:
        return AspectRatio(aspect_ratio) if isinstance(aspect_ratio, str) else aspect_ratio
    except (ValueError, TypeError):
        return AspectRatio.LANDSCAPE


def gpt_image_2_api_resolution_and_quality(
    resolution: Union[Resolution, str],
    aspect_ratio: Optional[Union[AspectRatio, str]] = None,
) -> Tuple[str, str]:
    """产品档位 + 宽高比 → 满足 TARGET 的最小 API resolution + quality（当前固定 medium）。"""
    res_enum = _coerce_resolution(resolution)
    ar_enum = _coerce_aspect_ratio(aspect_ratio)
    target = TARGET_PIXELS.get((res_enum, ar_enum))
    quality = "medium"
    if not target:
        return ("4k", quality)
    tw, th = target[0], target[1]
    for tier in _GPT_IMAGE_2_TIER_ORDER:
        out = _GPT_IMAGE_2_OUTPUT_PIXELS.get((ar_enum, tier))
        if out and out[0] >= tw and out[1] >= th:
            return (tier, quality)
    return ("4k", quality)


def gpt_image_2_target_pixels(
    resolution: Union[Resolution, str],
    aspect_ratio: Union[AspectRatio, str],
) -> Tuple[int, int]:
    """与关键帧管线一致：最终应对齐的 (width, height)。"""
    try:
        res = Resolution(resolution) if isinstance(resolution, str) else resolution
    except (ValueError, TypeError):
        res = Resolution.P1080
    try:
        ar = AspectRatio(aspect_ratio) if isinstance(aspect_ratio, str) else aspect_ratio
    except (ValueError, TypeError):
        ar = AspectRatio.LANDSCAPE
    t = TARGET_PIXELS.get((res, ar))
    if not t:
        return (1920, 1080)
    return (t[0], t[1])
