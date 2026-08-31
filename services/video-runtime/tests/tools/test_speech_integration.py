"""
简单的 WaveSpeed 语音生成 API 测试

To run this test:
    python tests/tools/test_speech_integration.py
"""
import asyncio

# 直接导入语音生成工具
from app.tools.wavespeed_tools import generate_speech_with_wavespeed


async def test_speech_api():
    """测试语音生成 API"""
    
    test_text = "这是一个特朗普的故事"
    
    print("🔊 开始语音生成测试")
    print(f"📝 文本: {test_text}")
    print("⏳ 生成中...")
    
    # 调用语音生成（测试所有参数）
    result = await generate_speech_with_wavespeed.ainvoke({
        "text": test_text,
        "voice_id": "Chinese (Mandarin)_HK_Flight_Attendant",
        "emotion": "neutral",
        "speed": 1.0,
        "pitch": 0,
        "volume": 1.0
    })
    
    # 打印结果
    print(f"\n✅ 成功: {result.success}")
    if result.success:
        print(f"🎵 音频URL: {result.audio_url}")
    else:
        print(f"❌ 错误: {result.message}")


if __name__ == "__main__":
    asyncio.run(test_speech_api())
