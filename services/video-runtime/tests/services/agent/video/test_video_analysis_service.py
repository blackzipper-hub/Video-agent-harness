"""
Unit tests for video_analysis_service - 真实LLM调用测试
"""
import pytest

from app.services.agent.video.video_analysis_service import _analyze_video_requirements
from app.models.video_state import AudioTranscription, AudioSegment, VideoAnalysisResult
from langchain_openai import ChatOpenAI


class TestVideoAnalysisService:
    """Test _analyze_video_requirements - 核心分析逻辑"""
    
    @pytest.mark.asyncio
    async def test_video_only_analysis(self):
        """测试纯视频分析（无音频输入）- 真实LLM"""
        # 创建真实的分析模型 - 与实际代码保持一致
        llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0.5, timeout=180, stream_usage=True)
        analysis_model = llm.with_structured_output(VideoAnalysisResult, include_raw=True)
        
        # 调用核心逻辑
        result, raw_message = await _analyze_video_requirements(
            user_input="生成一个吐槽对雷军营销速度第一安全不第一，营销海报小子的抖音视频",
            images=[],
            audio_transcription=None,
            target_duration=30,
            analysis_model=analysis_model
        )
        
        # 验证结果
        assert result is not None
        assert result.duration == 30
        assert result.detected_language in ["zh", "en"]
        
        print(f"✅ 测试通过:")
        print(f"   video_type={result.video_type}")
        print(f"   duration={result.duration}")
        print(f"   language={result.detected_language}")
        print(f"   purpose={result.purpose[:50]}...")
    
    @pytest.mark.asyncio
    async def test_audio_driven_analysis(self):
        """测试音频驱动分析 - 真实LLM"""
        # 创建音频转录
        mock_transcription = AudioTranscription(
            task="transcribe",
            language="zh",
            duration=25.0,
            text="欢迎使用我们的AI助手产品，它可以帮助您提高工作效率，简化日常任务。",
            segments=[
                AudioSegment(id=1, start=0, end=25, duration=25.0, text="欢迎使用AI助手", seek=0)
            ],
            audio_url="http://example.com/audio.mp3"
        )
        
        # 创建真实的分析模型 - 与实际代码保持一致
        llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0.5, timeout=180, stream_usage=True)
        analysis_model = llm.with_structured_output(VideoAnalysisResult, include_raw=True)
        
        # 调用核心逻辑
        result, raw_message = await _analyze_video_requirements(
            user_input="请根据我上传的音频制作一个配套的视频",
            images=["http://example.com/background.jpg"],
            audio_transcription=mock_transcription,
            target_duration=25,
            analysis_model=analysis_model
        )
        
        # 验证结果
        assert result is not None
        assert result.duration == 25
        assert result.detected_language in ["zh", "en"]
        
        print(f"✅ 测试通过:")
        print(f"   video_type={result.video_type}")
        print(f"   duration={result.duration}")
        print(f"   language={result.detected_language}")
        print(f"   purpose={result.purpose[:50]}...")