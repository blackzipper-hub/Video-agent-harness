"""
Integration tests for Pollo AI video generation
This test makes real API calls to Pollo AI service

To run this test:
    pytest tests/tools/test_pollo_integration.py -v

This test requires:
    - POLLO_API_KEY environment variable set
    - May consume Pollo AI credits
    - Takes several minutes to complete (video generation + polling)
"""
import pytest
import json
import os

# 直接导入核心实现函数（不包含 @tool 装饰器）
from app.tools.pollo_tools import _generate_video_with_pollo_impl
from app.models.image_result import VideoProvider


# 标记为集成测试
pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_real_video_generation():
    # 使用测试图片（可以替换为任何图片URL）
    test_image_url = "https://cdn-dev.newai.land/images/6ebb22d4-76ae-4d1e-8546-3e518b8a769a.webp"
    prompt = """
A bright and cheerful amusement park full shot with vibrant colors and lively atmosphere. The quirky hero and cute companion are at the center, holding hands, joyfully jumping and spinning with smooth, playful motions. The hero's face beams with a wide smile, eyes sparkling, and body moving with agile, exaggerated gestures, while the companion laughs happily, gently bouncing and twirling. Around them, colorful balloons float softly, ribbons flutter in the breeze, and children and parents play and laugh in the background with natural, lively movements. The camera performs a slow, steady overhead wide-angle orbit around the scene, smoothly circling the amusement park to reveal its breadth and warmth without cutting or zooming in. Warm sunlight filters through the colorful canopy, casting dappled, multicolored light and soft shadows that gradually shift as the camera moves. Subtle glow effects enhance the dreamy, magical feel, with realistic 3D rendering showing natural skin gloss and reflective surfaces on rides like carousel and slides. The entire scene flows continuously with gentle swaying, bouncing, and joyful energy, creating a harmonious and heartwarming mood.
"""
    
    print("\n" + "="*60)
    print("🎬 开始真实视频生成测试")
    print("="*60)
    print(f"📷 测试图片: {test_image_url}")
    print(f"⏱️  视频时长: 5秒")
    print(f"📐 分辨率: 480p")
    print("="*60 + "\n")
    
    # 调用视频生成核心实现函数（不经过 @tool 装饰器）
    result = await _generate_video_with_pollo_impl(
        i2v_prompt=prompt,
        start_image_url=test_image_url,
        duration=5,
        resolution="720p"
    )
    
    # 解析结果
    result_data = json.loads(result)
    
    # 打印结果
    print("\n" + "="*60)
    print("📊 生成结果")
    print("="*60)
    print(json.dumps(result_data, indent=2))
    print("="*60 + "\n")
    
    # 验证结果
    assert result_data['success'] is True, \
        f"视频生成失败: {result_data.get('error_message', 'Unknown error')}"
    
    assert result_data['provider'] == VideoProvider.POLLO, \
        "Provider should be Pollo"
    
    assert 'video_url' in result_data, "Missing video URL in result"
    assert result_data['video_url'], "Video URL should not be empty"
    
    assert result_data['duration'] == 5, "Duration should be 5 seconds"
    assert result_data['resolution'] == '480p', "Resolution should be 480p"
    
    print(f"\n✅ 视频生成成功！")
    print(f"🎥 视频URL: {result_data['video_url']}")
    print(f"⏱️  时长: {result_data['duration']}秒")
    print(f"📐 分辨率: {result_data['resolution']}\n")

