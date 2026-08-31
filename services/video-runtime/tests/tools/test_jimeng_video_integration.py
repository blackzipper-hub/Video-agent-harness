"""
Integration tests for Jimeng Video AI video generation
This test makes real API calls to Jimeng Video service

To run this test:
    python tests/tools/test_jimeng_video_integration.py

This test requires:
    - Jimeng Video API credentials (configured in jimeng_video_service.py)
    - May consume Jimeng Video API credits
    - Takes several minutes to complete (video generation)
"""
import json
import asyncio

# 直接导入核心实现函数（不包含 @tool 装饰器）
from app.tools.new.jimeng_video_tools import _generate_video_with_jimeng
from app.models.image_result import VideoProvider


async def test_real_video_generation_i2v():
    """
    测试真实的图像到视频生成 (I2V) - 完整流程测试
    
    这个测试会：
    1. 使用图像URL和文本提示词调用真实的即梦视频 API
    2. 生成基于图像的视频
    3. 验证返回的结果
    
    注意：需要配置有效的 API 密钥
    严格按照火山引擎即梦视频官方文档实现
    参考: https://www.volcengine.com/docs/85621/1785204
    """
    
    # 测试提示词
    test_image_url = "https://newai.land/api/photos/2edcb07f-a5db-42ab-a619-21dd2b6b318d.webp"
    test_prompt = """
A bright and cheerful amusement park full shot with vibrant colors and lively atmosphere. The quirky hero and cute companion are at the center, holding hands, joyfully jumping and spinning with smooth, playful motions. The hero's face beams with a wide smile, eyes sparkling, and body moving with agile, exaggerated gestures, while the companion laughs happily, gently bouncing and twirling. Around them, colorful balloons float softly, ribbons flutter in the breeze, and children and parents play and laugh in the background with natural, lively movements. The camera performs a slow, steady overhead wide-angle orbit around the scene, smoothly circling the amusement park to reveal its breadth and warmth without cutting or zooming in. Warm sunlight filters through the colorful canopy, casting dappled, multicolored light and soft shadows that gradually shift as the camera moves. Subtle glow effects enhance the dreamy, magical feel, with realistic 3D rendering showing natural skin gloss and reflective surfaces on rides like carousel and slides. The entire scene flows continuously with gentle swaying, bouncing, and joyful energy, creating a harmonious and heartwarming mood.
"""
    
    print("\n" + "="*60)
    print("🎬 开始真实视频生成测试（图像到视频）")
    print("="*60)
    print(f"📝 测试提示词: {test_prompt[:100]}...")
    print(f"🎨 生成模式: I2V (Image-to-Video)")
    print(f"📷 参考图像: {len(test_image_url)} 张")
    print(f"🎭 模型: jimeng_i2v_first_v30")
    print(f"🎬 时长: 5秒")
    print(f"🎲 随机种子: -1 (随机)")
    print("="*60 + "\n")
    
    # 调用视频生成核心实现函数（不经过 @tool 装饰器）
    # 严格按照火山引擎官方文档的参数
    result = await _generate_video_with_jimeng(
        prompt=test_prompt,
        image_urls=[test_image_url],
        seed=-1,
        duration=5
    )
    
    # 打印结果
    print("\n" + "="*60)
    print("📊 生成结果")
    print("="*60)
    print(f"成功: {result.success}")
    print(f"视频URL: {result.video_url}")
    print(f"提供商: {result.provider}")
    print(f"消息: {result.message}")
    print(f"生成的提示词: {result.generated_prompt}")
    print(f"时长: {result.duration}秒")
    print("="*60 + "\n")
    
    # 验证结果
    assert result.success is True, \
        f"视频生成失败: {result.message}"
    
    assert result.provider == VideoProvider.JIMENG_VIDEO, \
        "Provider should be Jimeng Video"
    
    assert result.video_url, "Video URL should not be empty"
    assert result.generated_prompt == test_prompt.strip(), "Generated prompt should match input"
    assert result.duration == 5.0, "Duration should be 5 seconds"
    
    print(f"\n✅ 图像到视频生成成功！")
    print(f"🎬 视频URL: {result.video_url}")
    print(f"🎭 提供商: {result.provider}")
    print(f"⏱️  时长: {result.duration}秒\n")


if __name__ == "__main__":
    asyncio.run(test_real_video_generation_i2v())