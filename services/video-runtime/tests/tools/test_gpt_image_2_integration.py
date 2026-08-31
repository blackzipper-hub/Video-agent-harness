"""
Integration tests for OpenAI GPT Image 2 via WaveSpeed (T2I + Edit).

pytest tests/tools/test_gpt_image_2_integration.py -v -m integration

Requires WAVESPEED_API_KEY; consumes API credits（每个参数化用例各 1 次 API）。

覆盖：产品 resolution 480p / 720p / 1080p（16:9 下 API 分别为 **1k / 1k / 2k**，见 `gpt_image_2_mapping.py`）× T2I 与 Edit；
宽高比在此文件中固定 16:9（与管线最常见一致）；9×9 宽高比矩阵由 test_gpt_image_2_mapping.py 覆盖。
"""
import os
import pytest

from app.llm.wavespeed_service import WaveSpeedService
from app.models.tool_enums import AspectRatio, Resolution
from app.tools.image.gpt_image_2_mapping import (
    gpt_image_2_api_resolution_and_quality,
    gpt_image_2_target_pixels,
)


pytestmark = pytest.mark.integration

_REF_IMAGE = "https://cdn-dev.newai.land/images/b637cb94-96b4-41ac-847e-9061397b94d1.webp"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_resolution",
    [Resolution.P480, Resolution.P720, Resolution.P1080],
)
async def test_gpt_image_2_t2i_real_each_product_resolution(user_resolution):
    if not os.getenv("WAVESPEED_API_KEY"):
        pytest.skip("WAVESPEED_API_KEY not set")

    tw, th = gpt_image_2_target_pixels(user_resolution, AspectRatio.LANDSCAPE)
    gpt_res, quality = gpt_image_2_api_resolution_and_quality(user_resolution, AspectRatio.LANDSCAPE)
    svc = WaveSpeedService()
    result = await svc.generate_image_gpt_image_2_t2i(
        prompt="A small red ceramic mug on a wooden table, soft window light, product photo.",
        aspect_ratio=AspectRatio.LANDSCAPE.value,
        gpt_resolution=gpt_res,
        quality=quality,
        target_width=tw,
        target_height=th,
    )
    assert result.success is True, result.error_msg or result.raw_error_msg
    assert result.image_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_resolution",
    [Resolution.P480, Resolution.P720, Resolution.P1080],
)
async def test_gpt_image_2_edit_real_each_product_resolution(user_resolution):
    if not os.getenv("WAVESPEED_API_KEY"):
        pytest.skip("WAVESPEED_API_KEY not set")

    tw, th = gpt_image_2_target_pixels(user_resolution, AspectRatio.LANDSCAPE)
    gpt_res, quality = gpt_image_2_api_resolution_and_quality(user_resolution, AspectRatio.LANDSCAPE)
    svc = WaveSpeedService()
    result = await svc.edit_image_gpt_image_2(
        prompt="Keep the subject; add a soft pastel sky background behind them.",
        images=[_REF_IMAGE],
        aspect_ratio=AspectRatio.LANDSCAPE.value,
        gpt_resolution=gpt_res,
        quality=quality,
        target_width=tw,
        target_height=th,
    )
    assert result.success is True, result.error_msg or result.raw_error_msg
    assert result.image_url
