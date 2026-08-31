"""
简单的 Lipsync API 测试
只测试实际 API 调用

To run this test:
    python tests/tools/test_lipsync_integration.py
"""
import asyncio

# 直接导入核心实现函数
from app.tools.lipsync.latentsync import _generate_lipsync_with_wavespeed_impl


async def test_lipsync_api():
    """测试真实的唇形同步 API 调用"""
    
    # 测试音频和视频URL
    test_audio_url = "https://d1q70pf5vjeyhc.cloudfront.net/predictions/a611f8a029fa434280c29a857d611795/1.mp3"
    test_video_url = "https://cdn-dev.newai.land/videos/pollo_cmhgx3o730cl1x8vzz36ifbum_fe164ad7.mp4"
    
    print("🎭 开始唇形同步测试")
    print(f"🎵 音频: {test_audio_url}")
    print(f"📹 视频: {test_video_url}")
    print("⏳ 生成中...")
    
    # 调用 API
    result = await _generate_lipsync_with_wavespeed_impl(
        audio_url=test_audio_url,
        video_url=test_video_url,
        model="bytedance/latentsync"
    )
    
    # 打印结果
    print(f"\n✅ 成功: {result.success}")
    if result.success:
        print(f"🎬 结果视频: {result.video_url}")
    else:
        print(f"❌ 错误: {result.message}")


if __name__ == "__main__":
    asyncio.run(test_lipsync_api())
