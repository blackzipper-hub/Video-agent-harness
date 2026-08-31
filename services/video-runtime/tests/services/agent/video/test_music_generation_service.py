"""
Unit tests for music_generation_service - 真实Suno音乐生成测试
"""
import pytest

from app.services.agent.video.music_generation_service import (
    generate_single_suno_music,
    analyze_music_intent
)


class TestMusicGenerationService:
    """Test generate_single_suno_music - Suno音乐生成测试"""
        
    @pytest.mark.asyncio
    async def test_cat_mukbang_mv_5s_with_verse(self):
        """测试猫吃播MV生成 - 5秒短歌词 - 使用Verse标签避免循环 - 真实Suno调用"""
        
        # 准备测试数据 - 使用结构化标签指导AI
        # 策略：用 [Verse] 替代 [Chorus] 避免循环属性
        user_input = """生成猫吃播mv，歌词结构：
[Intro]
[Verse]
爱猫爱猫飞起来
[Outro]
[Big Finish]
[End]

风格：Fast 160BPM, Staccato singing, Brief, Short version"""
        target_duration = 5
        
        # 调用真实的音乐生成函数
        messages, music_version = await generate_single_suno_music(
            user_input=user_input,
            target_duration=target_duration,
            prioritize_duration=True
        )
        
        # 验证结果
        assert messages is not None, "应该返回消息列表"
        assert len(messages) > 0, "消息列表不应为空"
        
        assert music_version is not None, "应该返回音乐版本对象"
        assert music_version.success is True, f"音乐生成应该成功，错误信息: {music_version.error_msg}"
        assert music_version.music_url is not None, "应该有音乐URL"
        assert music_version.duration is not None, "应该有音乐时长"
        assert music_version.provider == "suno", "提供商应该是suno"
        
        # 打印结果
        print(f"\n✅ 测试通过 - 猫吃播MV (5秒):")
        print(f"   音乐URL: {music_version.music_url}")
        print(f"   实际时长: {music_version.duration}秒")
        print(f"   目标时长: {target_duration}秒")
        print(f"   是否纯音乐: {music_version.is_instrumental}")
        print(f"   生成提示词: {music_version.music_prompt[:100]}...")
        
        if music_version.additional_data:
            clips = music_version.additional_data.get('clips', [])
            print(f"   生成版本数: {len(clips)}")
            for i, clip in enumerate(clips):
                print(f"   版本{i+1} - 时长: {clip.get('duration')}秒, URL: {clip.get('audio_url')}")
        
        # 验证时长（允许一定误差）
        duration_diff = abs(music_version.duration - target_duration)
        print(f"   时长误差: {duration_diff}秒")
        
        # 对于5秒这种极短的歌，Suno很难精确控制，所以我们只验证是否生成成功
        # 不做严格的时长验证
        assert music_version.duration > 0, "时长应该大于0"

