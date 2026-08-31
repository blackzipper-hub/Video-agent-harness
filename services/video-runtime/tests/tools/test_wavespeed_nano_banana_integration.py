"""
Integration tests for WaveSpeed Nano Banana image generation
This test makes real API calls to WaveSpeed AI service (Google Nano Banana)

To run this test:
    pytest tests/tools/test_wavespeed_nano_banana_integration.py -v

This test requires:
    - WAVESPEED_API_KEY environment variable set (or hardcoded in service)
    - May consume WaveSpeed AI credits
    - Takes several seconds to complete (image generation + polling)
"""
import pytest
import json
import os

from app.tools.wavespeed_tools import (
    generate_image_with_wavespeed_nano_banana_t2i,
    edit_image_with_wavespeed_nano_banana
)
from app.models.image_result import ImageProvider


# 标记为集成测试
pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_real_wavespeed_nano_banana_t2i():
    """
    测试真实的WaveSpeed Nano Banana文本生成图像（T2I）- 完整流程测试
    
    这个测试会：
    1. 使用文本提示词调用真实的 WaveSpeed API (Google Nano Banana)
    2. 生成基于提示词的新图像
    3. 保存图像到本地服务器
    4. 验证返回的结果
    """
    
    # 测试提示词（与你提供的示例一致）
    test_prompt = "A little girl blowing soap bubbles in a backyard, sunlight making rainbow colors in the bubbles, candid photography style."
    
    print("\n" + "="*60)
    print("🎨 开始真实WaveSpeed Nano Banana T2I图像生成测试")
    print("="*60)
    print(f"📝 测试提示词: {test_prompt}")
    print(f"🎨 生成模式: T2I (Text-to-Image)")
    print(f"📐 输出格式: PNG")
    print("="*60 + "\n")
    
    # 调用WaveSpeed Nano Banana T2I工具
    result = await generate_image_with_wavespeed_nano_banana_t2i.ainvoke({
        "prompt": test_prompt
    })
    
    # 打印结果
    print("\n" + "="*60)
    print("📊 生成结果")
    print("="*60)
    print(f"成功: {result.success}")
    print(f"提供商: {result.provider}")
    print(f"图像URL: {result.image_url}")
    print(f"生成提示词: {result.generated_prompt}")
    print(f"质量: {result.quality}")
    if not result.success:
        print(f"错误信息: {result.message}")
    print("="*60 + "\n")
    
    # 验证结果
    assert result.success is True, \
        f"WaveSpeed Nano Banana T2I图像生成失败: {result.message}"
    
    assert result.provider == ImageProvider.WAVESPEED, \
        "Provider should be WaveSpeed"
    
    assert result.image_url, "Image URL should not be empty"
    assert result.generated_prompt == test_prompt, "Generated prompt should match input"
    
    print(f"\n✅ WaveSpeed Nano Banana T2I图像生成成功！")
    print(f"🎨 图像URL: {result.image_url}")
    print(f"📝 提示词: {result.generated_prompt}")
    print(f"🏷️  提供商: {result.provider}\n")


@pytest.mark.asyncio
async def test_real_wavespeed_nano_banana_edit():
    """
    测试真实的WaveSpeed Nano Banana图像编辑 - 完整流程测试
    
    这个测试会：
    1. 使用编辑指令和输入图像调用真实的 WaveSpeed API (Google Nano Banana)
    2. 编辑输入图像生成新图像
    3. 保存图像到本地服务器
    4. 验证返回的结果
    """
    
    # 测试参数（与你提供的示例一致）
    test_prompt = "Replace the stick in the girl's hand with a flame."
    test_images = [
        "https://d1q70pf5vjeyhc.cloudfront.net/media/fb8f674bbb1a429d947016fd223cfae1/images/1756458671588525508_ACMHEBxu.jpeg"
    ]
    test_output_format = "png"
    
    print("\n" + "="*60)
    print("🖼️ 开始真实WaveSpeed Nano Banana图像编辑测试")
    print("="*60)
    print(f"📝 编辑指令: {test_prompt}")
    print(f"🖼️ 输入图片: {test_images[0]}")
    print(f"📄 输出格式: {test_output_format}")
    print("="*60 + "\n")
    
    # 调用WaveSpeed Nano Banana编辑工具
    result = await edit_image_with_wavespeed_nano_banana.ainvoke({
        "prompt": test_prompt,
        "images": test_images,
        "output_format": test_output_format
    })
    
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
        f"WaveSpeed Nano Banana图像编辑失败: {result.message}"
    
    assert result.provider == ImageProvider.WAVESPEED, \
        "Provider should be WaveSpeed"
    
    assert result.image_url, "Image URL should not be empty"
    assert result.generated_prompt == test_prompt, "Generated prompt should match input"
    assert result.reference_image_urls == test_images, "Reference images should match input"
    
    print(f"\n✅ WaveSpeed Nano Banana图像编辑成功！")
    print(f"🖼️ 编辑后图像URL: {result.image_url}")
    print(f"📝 编辑指令: {result.generated_prompt}")
    print(f"📷 使用了 {len(test_images)} 张输入图片")
    print(f"🏷️  提供商: {result.provider}\n")


if __name__ == "__main__":
    import asyncio
    
    # 可以通过环境变量控制测试类型
    test_type = os.getenv("TEST_TYPE", "t2i")  # t2i 或 edit
    
    if test_type == "edit":
        asyncio.run(test_real_wavespeed_nano_banana_edit())
    else:
        asyncio.run(test_real_wavespeed_nano_banana_t2i())
