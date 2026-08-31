"""基于成图的编辑：在业务层按 ``UserOption.image_generation_tool`` 走与关键帧 I2I 相同的 wrapper 降级链。"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, List, Optional

from app.models.image_result import ImageGenerationResult, ImageProvider
from app.models.tool_enums import DefaultValues
from app.models.user_options import DEFAULT_IMAGE_TOOL, ImageGenerationTool, UserOption
from app.tools.context_schemas import ImageGenerationContext
from app.tools.image.image_tool_wrapper import _get_i2i_chain, _run_i2i_loop

logger = logging.getLogger(__name__)


async def run_regenerate_image_edit_i2i(
    *,
    instruction: str,
    reference_image_urls: List[str],
    user_option: Optional[Any],
    shot_for_resolve: Optional[Any] = None,
    detected_language: Optional[str] = None,
    skip_consistency_check: bool = False,
) -> ImageGenerationResult:
    """使用当前成图 URL + 编辑说明，按用户图像工具链执行 I2I（不经 LLM agent）。

    ``shot_for_resolve`` 非空时先 ``resolve_user_option_for_shot(..., "image")``，与批量关键帧一致；
    角色再生无 shot 时传 ``None``，仅用请求体 / rebuild 后的 ``user_option``。
    """
    urls = [u for u in (reference_image_urls or []) if (u or "").strip()]
    ins = (instruction or "").strip()
    if not urls or not ins:
        return ImageGenerationResult.error_result(
            error_message="image_edit 需要非空 instruction 与至少一张成图 URL",
            provider=ImageProvider.WAVESPEED.value,
        )

    uo: Any = user_option if user_option is not None else UserOption()
    if shot_for_resolve is not None:
        from app.services.agent.video.per_shot_generation_routing_service import (
            resolve_user_option_for_shot,
        )

        resolved = resolve_user_option_for_shot(uo, shot_for_resolve, "image")
        if resolved is not None:
            uo = resolved

    image_tool = getattr(uo, "image_generation_tool", None) or ImageGenerationTool.AUTO
    if image_tool == ImageGenerationTool.AUTO:
        image_tool = DEFAULT_IMAGE_TOOL
    chain_i2i = _get_i2i_chain(image_tool)
    if not chain_i2i:
        return ImageGenerationResult.error_result(
            error_message="无可用 I2I 工具链",
            provider=ImageProvider.WAVESPEED.value,
        )

    ar = getattr(uo, "aspect_ratio", None) or DefaultValues.IMAGE_ASPECT_RATIO
    res = getattr(uo, "resolution", None) or DefaultValues.IMAGE_RESOLUTION
    primary = chain_i2i[0]
    context = ImageGenerationContext(
        aspect_ratio=ar,
        resolution=res,
        reference_image_urls=list(urls),
        model=primary.tool_type,
        language=detected_language,
        skip_consistency_check=skip_consistency_check,
    )
    runtime = SimpleNamespace(context=context)
    return await _run_i2i_loop(ins, list(urls), runtime, chain_i2i)
