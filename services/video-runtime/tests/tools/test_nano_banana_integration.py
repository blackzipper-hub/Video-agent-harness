"""
Integration tests for Nano Banana (Gemini) AI image generation
This test makes real API calls to Google Gemini service

To run this test:
    pytest tests/tools/test_nano_banana_integration.py -v

This test requires:
    - GOOGLE_API_KEY
    - May consume Gemini API credits
    - Takes several seconds to complete (image generation)
"""
import os
import pytest

# 直接导入核心实现函数（不包含 @tool 装饰器）
from app.tools.image.nano_banana import _generate_image_with_nano_banana
from app.models.tool_enums import AspectRatio, Resolution, ToolType


# 标记为集成测试
pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_real_image_generation_i2i():
    """
    测试真实的图生图（I2I）- 完整流程测试
    
    这个测试会：
    1. 使用参考图片调用真实的 Google Gemini API
    2. 生成基于参考图片的新图像
    3. 保存图像到本地服务器
    4. 验证返回的结果
    """
    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY not set")

    # 测试提示词和参考图片
    test_prompt = """
Based on the reference images, take the character from the first reference image (left position), and the character from the second reference image (right position), place them in a bright and cheerful amusement park full shot. The scene features colorful amusement rides like a carousel, slides, and vibrant balloons scattered around. The first character is standing on the left, smiling brightly, holding hands with the second character on the right, who is also smiling warmly. Both characters exhibit a joyful and friendly interaction, with a warm, playful atmosphere. The background shows other children and parents happily playing and laughing, enhancing the lively and harmonious mood. The lighting is bright and natural with warm tones, sunlight filtering through colorful canopy casting dappled colorful light and shadows. The characters have detailed 3D rendering with natural skin and costume glossiness. Slight glow effects add a dreamy feel, with balloons and flowing ribbons contributing dynamic visual interest. The composition is cinematic with a 16:9 aspect ratio, capturing the full scene and emphasizing the warmth and happiness of the moment.
    """
    
    # 使用一个公开的测试图片URL
    reference_image_urls = [
        "https://cdn-dev.newai.land/images/b637cb94-96b4-41ac-847e-9061397b94d1.webp",
        "https://cdn-dev.newai.land/images/ca0e92e1-7e59-4ddf-ad90-6e14a5f2edb8.webp"  # 示例人物图片
    ]
    
    print("\n" + "="*60)
    print("🍌 开始真实图像生成测试（图生图）")
    print("="*60)
    print(f"📝 测试提示词: {test_prompt[:100]}...")
    print(f"🎨 生成模式: I2I (Image-to-Image)")
    print(f"📷 参考图片: {len(reference_image_urls)} 张")
    print(f"📐 宽高比: 16:9")
    print("="*60 + "\n")
    
    # 调用图像生成核心实现函数（不经过 @tool 装饰器）
    result = await _generate_image_with_nano_banana(
        prompt=test_prompt,
        reference_image_urls=reference_image_urls,
        aspect_ratio=AspectRatio.LANDSCAPE,
        resolution=Resolution.P720,
        model=ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW,
    )

    # 打印结果
    print("\n" + "="*60)
    print("📊 生成结果")
    print("="*60)
    print(result.model_dump_json(indent=2))
    print("="*60 + "\n")

    # 验证结果
    assert result.success is True, f"图像生成失败: {result.error_msg or result.raw_error_msg}"
    assert result.provider == "google", "Provider should be google (Gemini)"
    assert result.image_url, "Image URL should not be empty"

    print(f"\n✅ 图生图成功！")
    print(f"🎨 图像URL: {result.image_url}")
    print(f"📷 使用了 {len(reference_image_urls)} 张参考图片")
    print(f"🍌 提供商: {result.provider}\n")
