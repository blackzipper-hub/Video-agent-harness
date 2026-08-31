"""
Integration tests for OpenAI Sora 2 video generation
This test makes real API calls to OpenAI Sora service

To run this test:
    pytest tests/tools/test_opensora_integration.py -v -s -m integration

This test requires:
    - OPENAI_API_KEY environment variable set
    - May consume OpenAI credits
    - Takes several minutes to complete (video generation + polling)
"""
import os

import pytest

from app.tools.video.sora import _generate_video
from app.models.image_result import VideoProvider


pytestmark = [pytest.mark.integration, pytest.mark.slow]


def _require_openai_key():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")


@pytest.mark.asyncio
async def test_real_opensora_i2v_sora_2():
    """OpenAI Sora 2 I2V：走 _generate_video + account_router，含创建任务与轮询至完成。"""
    _require_openai_key()

    test_image_url = "https://cdn-dev.newai.land/images/dc43fc11-03a0-4616-83ca-9381c743c558.webp"
    prompt = (
        "Subtle slow camera push-in; warm indoor light; the subject holds still with a calm expression, "
        "slight natural blink; no text overlays; cinematic, shallow depth of field."
    )
    duration = 4

    print("\n" + "=" * 60)
    print("Sora 2 I2V (real API, full poll)")
    print("=" * 60)
    print(f"image={test_image_url}")
    print(f"prompt={prompt[:120]}...")
    print(f"duration={duration}s aspect=16:9 res=720p model=sora-2")
    print("=" * 60 + "\n")

    result = await _generate_video(
        i2v_prompt=prompt,
        start_image_url=test_image_url,
        duration=duration,
        aspect_ratio="16:9",
        resolution="720p",
        model="sora-2",
        runtime=None,
    )
    
    print("\n" + "=" * 60)
    print("Sora 2 I2V 结果")
    print("=" * 60)
    print(f"success={result.success}")
    print(f"video_url={result.video_url}")
    print(f"message={result.message}")
    print(f"provider={result.provider}")
    print(f"duration={result.duration}")
    print(f"resolution={getattr(result, 'resolution', None)}")
    print("=" * 60 + "\n")

    assert result.success is True, f"I2V sora-2 failed: {result.message}"
    assert result.provider == VideoProvider.OPENAI_SORA.value
    assert result.video_url and str(result.video_url).startswith(("http://", "https://"))
    assert float(result.duration or 0) == float(duration)


@pytest.mark.asyncio
async def test_real_opensora_t2v_sora_2():
    """OpenAI Sora 2 T2V：无参考图，仅文本。"""
    _require_openai_key()

    prompt = (
        "A single red rubber ball on a wooden table, soft window light, "
        "very slow subtle shadow movement, static camera, photorealistic, no people."
    )

    print("\n" + "=" * 60)
    print("Sora 2 T2V (real API, full poll)")
    print("=" * 60)
    print(f"prompt={prompt[:120]}...")
    print("duration=4s aspect=16:9 res=720p model=sora-2")
    print("=" * 60 + "\n")

    result = await _generate_video(
        i2v_prompt=prompt,
        start_image_url=None,
        duration=4,
        aspect_ratio="16:9",
        resolution="720p",
        model="sora-2",
        runtime=None,
    )

    print("\n" + "=" * 60)
    print("Sora 2 T2V 结果")
    print("=" * 60)
    print(f"success={result.success}")
    print(f"video_url={result.video_url}")
    print(f"message={result.message}")
    print("=" * 60 + "\n")

    assert result.success is True, f"T2V sora-2 failed: {result.message}"
    assert result.provider == VideoProvider.OPENAI_SORA.value
    assert result.video_url and str(result.video_url).startswith(("http://", "https://"))


@pytest.mark.asyncio
async def test_real_opensora_i2v_sora_2_pro():
    """OpenAI Sora 2 Pro I2V：与标准版同入口，模型 sora-2-pro。"""
    _require_openai_key()

    test_image_url = "https://cdn-dev.newai.land/images/5bc522f7-26ab-4c7c-9a9b-31a4caa9c158.webp"
    prompt = (
        "Gentle handheld micro-movement; golden hour alley; dog and person barely move; "
        "film grain; no on-screen text."
    )

    print("\n" + "=" * 60)
    print("Sora 2 Pro I2V (real API, full poll)")
    print("=" * 60)
    print(f"image={test_image_url}")
    print(f"model=sora-2-pro duration=4 aspect=16:9 res=720p")
    print("=" * 60 + "\n")

    result = await _generate_video(
        i2v_prompt=prompt,
        start_image_url=test_image_url,
        duration=4,
        aspect_ratio="16:9",
        resolution="720p",
        model="sora-2-pro",
        runtime=None,
    )

    print("\n" + "=" * 60)
    print("Sora 2 Pro I2V 结果")
    print("=" * 60)
    print(f"success={result.success}")
    print(f"video_url={result.video_url}")
    print(f"message={result.message}")
    print(f"resolution={getattr(result, 'resolution', None)}")
    print("=" * 60 + "\n")

    assert result.success is True, f"I2V sora-2-pro failed: {result.message}"
    assert result.provider == VideoProvider.OPENAI_SORA.value
    assert result.video_url and str(result.video_url).startswith(("http://", "https://"))

