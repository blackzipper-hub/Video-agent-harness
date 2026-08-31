"""
Integration tests for WaveSpeed Seedream image editing
This test makes real API calls to WaveSpeed AI service (ByteDance Seedream v4.5)

To run this test:
    pytest tests/tools/test_wavespeed_seedream_integration.py -v

This test requires:
    - WAVESPEED_API_KEY environment variable set (or hardcoded in service)
    - May consume WaveSpeed AI credits
    - Takes several seconds to complete (image editing + polling)
"""
import pytest
from langchain_core.runnables import RunnableConfig

from app.tools.wavespeed_tools import (
    edit_image_with_wavespeed_seedream,
    generate_image_with_wavespeed_seedream_t2i
)
from app.models.image_result import ImageProvider


# 标记为集成测试
pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_real_wavespeed_seedream_edit():
    """
    测试真实的WaveSpeed Seedream图像编辑 - 完整流程测试
    
    这个测试会：
    1. 使用编辑指令和输入图像调用真实的 WaveSpeed API (ByteDance Seedream v4.5)
    2. 编辑输入图像生成新图像
    3. 保存图像到本地服务器
    4. 验证返回的结果
    """
    
    # 测试参数
    test_prompt = "A cute cat eating delicious food from a bowl, showing happy expression"
    
    test_images = [
        "https://cdn-dev.newai.land/images/b10eddb7-4a6c-484b-8d90-85651b7a3994.webp"
    ]
    
    test_aspect_ratio = "16:9"  # 横屏
    test_resolution = "1080p"  # 会被转换为 Seedream 的 "2560*1440"
    
    print("\n" + "="*60)
    print("🖼️ 开始真实WaveSpeed Seedream图像编辑测试")
    print("="*60)
    print(f"📝 编辑指令: {test_prompt}")
    print(f"🖼️ 输入图片: {test_images[0]}")
    print(f"📐 宽高比: {test_aspect_ratio}")
    print(f"📐 分辨率: {test_resolution} (将转换为 Seedream 的 '2560*1440')")
    print("="*60 + "\n")
    
    # 调用WaveSpeed Seedream编辑工具
    result = await edit_image_with_wavespeed_seedream.ainvoke(
        {
            "prompt": test_prompt,
            "images": test_images
        },
        config=RunnableConfig(configurable={
            "aspect_ratio": test_aspect_ratio,
            "resolution": test_resolution
        })
    )
    
    # 打印结果
    print("\n" + "="*60)
    print("📊 编辑结果")
    print("="*60)
    print(f"成功: {result.success}")
    print(f"提供商: {result.provider}")
    print(f"图像URL: {result.image_url}")
    print(f"编辑指令: {result.generated_prompt}")
    print(f"质量: {result.quality}")
    print(f"参考图片: {result.reference_image_urls}")
    if not result.success:
        print(f"错误信息: {result.message}")
    print("="*60 + "\n")
    
    # 验证结果
    assert result.success is True, \
        f"WaveSpeed Seedream图像编辑失败: {result.message}"
    
    assert result.provider == ImageProvider.WAVESPEED, \
        "Provider should be WaveSpeed"
    
    assert result.image_url, "Image URL should not be empty"
    assert result.generated_prompt == test_prompt, "Generated prompt should match input"
    assert result.reference_image_urls == test_images, "Reference images should match input"
    
    print(f"\n✅ WaveSpeed Seedream图像编辑成功！")
    print(f"🖼️ 编辑后图像URL: {result.image_url}")
    print(f"📝 编辑指令: {result.generated_prompt}")
    print(f"📷 使用了 {len(test_images)} 张输入图片")
    print(f"📐 宽高比: {test_aspect_ratio}, 分辨率: {test_resolution}")
    print(f"🏷️  提供商: {result.provider}\n")


@pytest.mark.asyncio
async def test_real_wavespeed_seedream_t2i():
    """
    测试真实的WaveSpeed Seedream文本生成图像（T2I）- 完整流程测试
    
    这个测试会：
    1. 使用文本提示词调用真实的 WaveSpeed API (ByteDance Seedream v4.5)
    2. 生成基于提示词的新图像
    3. 保存图像到本地服务器
    4. 验证返回的结果
    """
    
    # 测试参数
    test_prompt = "Nighttime outdoor photoshoot: A young man stands inside a public phone booth, holding a blue phone receiver to his ear. One hand is casually tucked into his pocket, and he strikes a relaxed posture. He wears a white T-shirt with a pattern, loose brown pants, and jacket draped over his arm. The booth's glass reflects city streetlights with a bokeh effect, creating a vintage film style."
    
    test_aspect_ratio = "16:9"  # 横屏
    test_resolution = "1080p"  # 会被转换为 Seedream 的 "2560*1440"
    
    print("\n" + "="*60)
    print("🎨 开始真实WaveSpeed Seedream T2I图像生成测试")
    print("="*60)
    print(f"📝 生成提示词: {test_prompt[:80]}...")
    print(f"📐 宽高比: {test_aspect_ratio}")
    print(f"📐 分辨率: {test_resolution} (将转换为 Seedream 的 '2560*1440')")
    print("="*60 + "\n")
    
    # 调用WaveSpeed Seedream T2I工具
    result = await generate_image_with_wavespeed_seedream_t2i.ainvoke(
        {
            "prompt": test_prompt
        },
        config=RunnableConfig(configurable={
            "aspect_ratio": test_aspect_ratio,
            "resolution": test_resolution
        })
    )
    
    # 打印结果
    print("\n" + "="*60)
    print("📊 生成结果")
    print("="*60)
    print(f"成功: {result.success}")
    print(f"提供商: {result.provider}")
    print(f"图像URL: {result.image_url}")
    print(f"生成提示词: {result.generated_prompt[:80]}...")
    print(f"质量: {result.quality}")
    if not result.success:
        print(f"错误信息: {result.message}")
    print("="*60 + "\n")
    
    # 验证结果
    assert result.success is True, \
        f"WaveSpeed Seedream T2I图像生成失败: {result.message}"
    
    assert result.provider == ImageProvider.WAVESPEED, \
        "Provider should be WaveSpeed"
    
    assert result.image_url, "Image URL should not be empty"
    assert result.generated_prompt == test_prompt, "Generated prompt should match input"
    
    print(f"\n✅ WaveSpeed Seedream T2I图像生成成功！")
    print(f"🎨 生成图像URL: {result.image_url}")
    print(f"📝 生成提示词: {result.generated_prompt[:80]}...")
    print(f"📐 宽高比: {test_aspect_ratio}, 分辨率: {test_resolution}")
    print(f"🏷️  提供商: {result.provider}\n")


if __name__ == "__main__":
    import asyncio
    import os
    
    # 可以通过环境变量控制测试类型
    test_type = os.getenv("TEST_TYPE", "edit")  # edit 或 t2i
    
    if test_type == "t2i":
        asyncio.run(test_real_wavespeed_seedream_t2i())
    else:
        asyncio.run(test_real_wavespeed_seedream_edit())

