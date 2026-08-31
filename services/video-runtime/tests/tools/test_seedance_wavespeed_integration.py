"""
Integration tests for WaveSpeed Seedance video generation
This test makes real API calls to WaveSpeed AI service

To run this test:
    pytest tests/tools/test_seedance_wavespeed_integration.py -v

This test requires:
    - WAVESPEED_API_KEY environment variable set
    - May consume WaveSpeed AI credits
    - Takes several minutes to complete (video generation + polling)
"""
import pytest
import json
import os
import asyncio
import random
import time

from app.tools.wavespeed_tools import generate_video_with_wavespeed_seedance
from app.models.image_result import VideoProvider


# 标记为集成测试
pytestmark = pytest.mark.integration

# 并发测试控制变量
CONCURRENT_REQUESTS = int(os.getenv("CONCURRENT_REQUESTS", "5"))  # 默认5个并发，可通过环境变量控制

# 随机提示词模板列表，避免缓存
PROMPT_TEMPLATES = [
    "A cinematic sci-fi scene with {element} glowing brightly. The massive structure rotates slowly, revealing intricate details. Camera performs a {camera_move} movement. Style: photorealistic, epic scale, {lighting} lighting.",
    "An awe-inspiring futuristic landscape featuring {element} with pulsating energy. The scene shows {action} with smooth motion. Camera: {camera_move} shot. Atmosphere: {lighting}, dramatic shadows.",
    "A breathtaking space station with {element} activating in sequence. {action} creates dynamic movement. Camera work: {camera_move} perspective. Visual style: hyper-detailed, {lighting} ambiance.",
    "Epic orbital structure with {element} coming online. The scene depicts {action} with fluid animation. Cinematography: {camera_move} angle. Mood: {lighting}, sense of wonder.",
    "Majestic cosmic installation where {element} illuminate the darkness. {action} brings the scene to life. Camera technique: {camera_move} motion. Aesthetic: {lighting}, cinematic grandeur."
]

ELEMENTS = ["energy circuits", "docking bay lights", "hull patterns", "navigation beacons", "power cores"]
ACTIONS = ["ships emerging from bays", "lights flickering on", "structure rotating", "energy flowing", "systems activating"]
CAMERA_MOVES = ["slow orbital pan", "dramatic pull-back", "sweeping wide shot", "steady zoom out", "graceful arc"]
LIGHTING_STYLES = ["golden hour", "stark contrast", "soft ambient", "dramatic chiaroscuro", "ethereal glow"]


@pytest.mark.asyncio
async def test_real_seedance_video_generation():
    """测试真实的WaveSpeed Seedance视频生成"""
    # 使用测试图片（与用户提供的示例相同）
    test_image_url = "https://d1q70pf5vjeyhc.cloudfront.net/media/92ecf66930134a49a5a425b9def0c266/images/1761364509066743304_F5nL7tQ8.png"
    prompt = """A cinematic, awe-inspiring sci-fi video, bringing the input image to breathtaking life.

The scene opens holding on the colossal orbital ring structure station, dwarfing the planet below, exactly as pictured. The scale is immense.

Slowly, majestically, sections of the station begin to activate. Intricate patterns of bright white or blue energy lights start coursing along its vast hull like a complex circuit board coming online. Docking bay lights flicker on in sequence down its length.

Tiny specks (spaceships) emerge from docking bays and begin to move purposefully along designated flight paths around the station. Some leave faint engine trails.

The entire massive structure perhaps very slowly, almost imperceptibly, rotates, revealing more of its complex geometry against the backdrop of the swirling clouds of the planet and the darkness of space.

Camera: A very slow, grand, sweeping orbital pan or a dramatic pull-back (zoom out), further emphasizing the unbelievable scale and intricate detail of the station against the planet.

Style: Photorealistic, masterpiece, hyper-detailed, epic scale, cinematic lighting (strong contrast between sunlit side and shadowed side), sense of wonder and immense power."""
    
    print("\n" + "="*60)
    print("🎬 开始真实WaveSpeed Seedance视频生成测试")
    print("="*60)
    print(f"📷 测试图片: {test_image_url}")
    print(f"⏱️  视频时长: 5秒")
    print(f"📐 分辨率: 480p")
    print(f"🎥 相机固定: False")
    print(f"🎲 随机种子: -1")
    print("="*60 + "\n")
    
    # 调用Seedance视频生成工具
    result = await generate_video_with_wavespeed_seedance.ainvoke({
        "i2v_prompt": prompt,
        "start_image_url": test_image_url,
        "duration": 2,
        "resolution": "480p",
        "end_image_url": None
    })
    
    # 打印结果
    print("\n" + "="*60)
    print("📊 生成结果")
    print("="*60)
    print(f"成功: {result.success}")
    print(f"提供商: {result.provider}")
    print(f"视频URL: {result.video_url}")
    print(f"时长: {result.duration}")
    print(f"分辨率: {result.resolution}")
    print(f"消息: {result.message}")
    if not result.success:
        print(f"错误信息: {result.message}")
    print("="*60 + "\n")
    
    # 验证结果
    assert result.success is True, \
        f"Seedance视频生成失败: {result.message}"
    
    assert result.provider == VideoProvider.WAVESPEED, \
        "Provider should be WaveSpeed"
    
    assert result.video_url, "Video URL should not be empty"
    
    assert result.duration == 5.0, "Duration should be 5.0 seconds"
    assert result.resolution == "480p", "Resolution should be 480p"
    
    print(f"\n✅ WaveSpeed Seedance视频生成成功！")
    print(f"🎥 视频URL: {result.video_url}")
    print(f"⏱️  时长: {result.duration}秒")
    print(f"📐 分辨率: {result.resolution}\n")


def generate_random_prompt():
    """生成随机化的提示词，避免缓存"""
    template = random.choice(PROMPT_TEMPLATES)
    return template.format(
        element=random.choice(ELEMENTS),
        action=random.choice(ACTIONS),
        camera_move=random.choice(CAMERA_MOVES),
        lighting=random.choice(LIGHTING_STYLES)
    )


async def single_video_generation(request_id: int, test_image_url: str):
    """单个视频生成任务"""
    try:
        # 生成随机提示词和种子
        prompt = generate_random_prompt()
        seed = random.randint(1, 1000000)
        
        print(f"\n🎬 请求 #{request_id} 开始")
        print(f"🎲 随机种子: {seed}")
        print(f"📝 提示词: {prompt[:100]}...")
        
        start_time = time.time()
        
        # 调用Seedance视频生成工具
        result = await generate_video_with_wavespeed_seedance.ainvoke({
            "i2v_prompt": prompt,
            "start_image_url": test_image_url,
            "duration": 2,  # 使用最短时长
            "resolution": "480p",  # 使用最便宜分辨率
            "end_image_url": None
        })
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"✅ 请求 #{request_id} 完成，耗时: {duration:.2f}秒")
        print(f"🎥 视频URL: {result.video_url[:50]}..." if result.video_url else "无URL")
        
        return {
            "request_id": request_id,
            "success": result.success,
            "duration": duration,
            "video_url": result.video_url,
            "error": result.message if not result.success else None,
            "seed": seed,
            "prompt_preview": prompt[:50] + "..."
        }
        
    except Exception as e:
        end_time = time.time()
        duration = end_time - start_time
        print(f"❌ 请求 #{request_id} 失败: {str(e)}")
        return {
            "request_id": request_id,
            "success": False,
            "duration": duration,
            "video_url": None,
            "error": str(e),
            "seed": seed,
            "prompt_preview": prompt[:50] + "..."
        }


@pytest.mark.asyncio
async def test_concurrent_seedance_video_generation():
    """测试并发Seedance视频生成"""
    test_image_url = "https://d1q70pf5vjeyhc.cloudfront.net/media/92ecf66930134a49a5a425b9def0c266/images/1761364509066743304_F5nL7tQ8.png"
    
    print("\n" + "="*80)
    print(f"🚀 开始并发WaveSpeed Seedance视频生成测试")
    print("="*80)
    print(f"📷 测试图片: {test_image_url}")
    print(f"🔢 并发数量: {CONCURRENT_REQUESTS}")
    print(f"⏱️  视频时长: 2秒")
    print(f"📐 分辨率: 480p")
    print(f"🎲 随机种子: 是")
    print(f"📝 随机提示词: 是")
    print("="*80 + "\n")
    
    # 记录开始时间
    overall_start_time = time.time()
    
    # 创建并发任务
    tasks = []
    for i in range(CONCURRENT_REQUESTS):
        task = single_video_generation(i + 1, test_image_url)
        tasks.append(task)
    
    # 并发执行所有任务
    print(f"🔄 启动 {CONCURRENT_REQUESTS} 个并发请求...")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 计算总耗时
    overall_end_time = time.time()
    total_duration = overall_end_time - overall_start_time
    
    # 统计结果
    successful_results = []
    failed_results = []
    
    for result in results:
        if isinstance(result, Exception):
            failed_results.append({"error": str(result)})
        elif result["success"]:
            successful_results.append(result)
        else:
            failed_results.append(result)
    
    # 打印详细统计
    print("\n" + "="*80)
    print("📊 并发测试结果统计")
    print("="*80)
    print(f"🎯 总请求数: {CONCURRENT_REQUESTS}")
    print(f"✅ 成功数量: {len(successful_results)}")
    print(f"❌ 失败数量: {len(failed_results)}")
    print(f"📈 成功率: {len(successful_results)/CONCURRENT_REQUESTS*100:.1f}%")
    print(f"⏱️  总耗时: {total_duration:.2f}秒")
    
    if successful_results:
        avg_duration = sum(r["duration"] for r in successful_results) / len(successful_results)
        min_duration = min(r["duration"] for r in successful_results)
        max_duration = max(r["duration"] for r in successful_results)
        print(f"⏱️  平均单个耗时: {avg_duration:.2f}秒")
        print(f"⏱️  最快耗时: {min_duration:.2f}秒")
        print(f"⏱️  最慢耗时: {max_duration:.2f}秒")
    
    print("="*80)
    
    # 打印成功的结果详情
    if successful_results:
        print("\n✅ 成功的请求:")
        for result in successful_results:
            print(f"  请求#{result['request_id']}: {result['duration']:.2f}s, 种子:{result['seed']}, 提示:{result['prompt_preview']}")
    
    # 打印失败的结果详情
    if failed_results:
        print("\n❌ 失败的请求:")
        for result in failed_results:
            if "request_id" in result:
                print(f"  请求#{result['request_id']}: {result['error']}")
            else:
                print(f"  异常: {result['error']}")
    
    print("\n" + "="*80 + "\n")
    
    # 验证至少有一些成功的请求
    assert len(successful_results) > 0, f"所有 {CONCURRENT_REQUESTS} 个请求都失败了"
    
    # 验证成功率不低于50%（可以根据实际情况调整）
    success_rate = len(successful_results) / CONCURRENT_REQUESTS
    assert success_rate >= 0.5, f"成功率太低: {success_rate*100:.1f}%，期望至少50%"
    
    # 验证每个成功的结果
    for result in successful_results:
        assert result["video_url"], f"请求#{result['request_id']} 缺少视频URL"
        assert result["duration"] > 0, f"请求#{result['request_id']} 耗时异常"
    
    print(f"🎉 并发测试通过！成功率: {success_rate*100:.1f}%")


if __name__ == "__main__":
    import asyncio
    
    # 可以通过环境变量控制测试类型
    test_type = os.getenv("TEST_TYPE", "single")  # single 或 concurrent
    
    if test_type == "concurrent":
        asyncio.run(test_concurrent_seedance_video_generation())
    else:
        asyncio.run(test_real_seedance_video_generation())
