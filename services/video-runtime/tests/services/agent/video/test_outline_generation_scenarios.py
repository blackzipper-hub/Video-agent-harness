"""
Outline Generation 故事梗概生成测试
测试故事梗概生成和 ReAct Agent 机制（参考 test_main_character_design_scenarios.py）
"""
import pytest
import os
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
from langchain_openai import ChatOpenAI
from unittest.mock import AsyncMock, Mock, patch

# API key 从环境读取（集成测试需自行提供 OPENAI_API_KEY，不再硬编码密钥）
if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "sk-test-placeholder"

from app.services.agent.video.outline_generation_service import _generate_outline_with_react_agent
from app.models.video_state import (
    VideoAnalysisResult, 
    AudioTranscription, 
    AudioSegment, 
    UserInput, 
    StoryOutline
)
from app.models.user_options import UserOption, VideoGenerationTool
from app.utils.language_context import set_current_language


# ==================== 测试场景定义 ====================

class OutlineScenario:
    """故事梗概测试配置（避免以Test开头，防止pytest误识别）"""
    def __init__(
        self,
        name: str,
        story_type: str = "modern_urban",  # "modern_urban", "fantasy", "animation", "documentary", "tutorial"
        duration: int = 30,
        mode: str = "video",  # "video" or "audio"
        style_preference: str = "auto",  # "auto", "cartoon", "realistic", "anime"
        description: str = "",
        language: str = "zh",  # "zh" or "en"
        expected_chapters: int = 3,  # 期望生成的章节数量
        test_react_agent: bool = True,  # 是否测试ReAct Agent功能
        generate_html_report: bool = False  # 是否生成HTML可视化报告
    ):
        self.name = name
        self.story_type = story_type
        self.duration = duration
        self.mode = mode
        self.style_preference = style_preference
        self.description = description
        self.language = language
        self.expected_chapters = expected_chapters
        self.test_react_agent = test_react_agent
        self.generate_html_report = generate_html_report
    
    def __str__(self):
        return f"{self.name} ({self.story_type}, {self.duration}s, {self.language})"


# 定义所有测试场景
TEST_SCENARIOS = [
    OutlineScenario(
        name="short_modern_urban_zh",
        story_type="modern_urban",
        duration=30,
        mode="video",
        style_preference="realistic",
        description="30s modern urban story, realistic style, Chinese",
        language="zh",
        expected_chapters=3
    )
]


# ==================== Fixtures ====================

def create_video_analysis_result(scenario: OutlineScenario) -> VideoAnalysisResult:
    """
    创建测试视频分析结果
    根据场景类型和语言创建不同的分析内容
    """
    
    # 根据故事类型和语言设置内容
    analysis_configs = {
        "zh": {
            "video_type": "抖音短视频",
            "main_character": "营销海报小子",
            "purpose": "吐槽雷军营销速度快但安全性不足，表达对其营销策略的批评和调侃",
            "key_elements": [
                "雷军的营销速度第一", 
                "安全不第一的对比吐槽", 
                "营销海报小子作为主要角色进行表达",
                "幽默和讽刺的语言风格",
                "适合抖音平台的短视频节奏和表现形式"
            ],
            "style_preferences": [
                "幽默风趣",
                "讽刺调侃",
                "快节奏剪辑",
                "视觉冲击力强的海报元素"
            ],
            "target_audience": "关注科技产品和雷军相关话题的抖音用户，喜欢轻松幽默内容的年轻群体",
        },
        "en": {
            "video_type": "Urban Life Short Video",
            "main_character": "Urban Youth",
            "purpose": "Showcase the authentic aspects of modern urban life and convey a positive attitude",
            "key_elements": ["Urban living", "Work pressure", "Life balance", "Relationships", "Personal growth"],
            "style_preferences": ["Realistic", "Modern minimalist", "Emotional resonance"],
            "target_audience": "Urban youth aged 18-35",
        }
    }
    
    config = analysis_configs[scenario.language]
    
    return VideoAnalysisResult(
        video_type=config["video_type"],
        duration=scenario.duration,
        main_character=config["main_character"],
        purpose=config["purpose"],
        key_elements=config["key_elements"],
        style_preferences=config["style_preferences"],
        target_audience=config["target_audience"],
        next_action="制定详细脚本和分镜方案" if scenario.language == "zh" else "Develop detailed script and storyboard plan",
    )


def create_user_input(scenario: OutlineScenario) -> UserInput:
    """
    创建测试用户输入
    根据场景类型和语言创建不同的用户输入
    """
    
    # 根据故事类型和语言设置用户输入
    input_configs = {
        "zh": f"制作一个{scenario.duration}秒的都市生活视频，展现年轻人的工作和生活",
        "en": f"Create a {scenario.duration}-second urban life video showcasing young people's work and life"
    }
    
    user_input_text = input_configs[scenario.language]
    
    return UserInput(
        user_input=user_input_text,
        images=[],
        audio_files=[],
        user_option=UserOption(
            video_generation_tool=VideoGenerationTool.POLLO_SEEDANCE,
            duration=scenario.duration
        )
    )


def get_audio_transcription(scenario: OutlineScenario) -> Optional[AudioTranscription]:
    """
    获取音频转录数据
    根据场景模式返回相应的音频数据
    """
    if scenario.mode != "audio":
        return None
    
    # 根据语言创建不同的音频转录内容
    if scenario.language == "zh":
        if scenario.story_type == "documentary":
            text = "科技正在改变我们的生活。从人工智能到物联网，新技术不断涌现。我们需要了解这些变化，适应数字化时代的挑战和机遇。"
            segments = [
                AudioSegment(
                    uuid="seg-1",
                    id=1,
                    start=0.0,
                    end=8.0,
                    duration=8.0,
                    text="科技正在改变我们的生活。从人工智能到物联网，新技术不断涌现。"
                ),
                AudioSegment(
                    uuid="seg-2",
                    id=2,
                    start=8.0,
                    end=scenario.duration,
                    duration=scenario.duration - 8.0,
                    text="我们需要了解这些变化，适应数字化时代的挑战和机遇。"
                )
            ]
        else:  # tutorial
            text = "今天我们来学习一个实用的技能。首先，我们需要准备必要的工具和材料。然后按照步骤逐一操作，确保每个环节都正确完成。"
            segments = [
                AudioSegment(
                    uuid="seg-1",
                    id=1,
                    start=0.0,
                    end=15.0,
                    duration=15.0,
                    text="今天我们来学习一个实用的技能。首先，我们需要准备必要的工具和材料。"
                ),
                AudioSegment(
                    uuid="seg-2",
                    id=2,
                    start=15.0,
                    end=scenario.duration,
                    duration=scenario.duration - 15.0,
                    text="然后按照步骤逐一操作，确保每个环节都正确完成。"
                )
            ]
    else:  # English
        if scenario.story_type == "documentary":
            text = "Technology is transforming our lives. From artificial intelligence to the Internet of Things, new technologies are constantly emerging. We need to understand these changes and adapt to the challenges and opportunities of the digital age."
            segments = [
                AudioSegment(
                    uuid="seg-1",
                    id=1,
                    start=0.0,
                    end=12.0,
                    duration=12.0,
                    text="Technology is transforming our lives. From artificial intelligence to the Internet of Things, new technologies are constantly emerging."
                ),
                AudioSegment(
                    uuid="seg-2",
                    id=2,
                    start=12.0,
                    end=scenario.duration,
                    duration=scenario.duration - 12.0,
                    text="We need to understand these changes and adapt to the challenges and opportunities of the digital age."
                )
            ]
        else:  # tutorial
            text = "Today we're going to learn a practical skill. First, we need to prepare the necessary tools and materials. Then we'll follow the steps one by one, ensuring each step is completed correctly."
            segments = [
                AudioSegment(
                    uuid="seg-1",
                    id=1,
                    start=0.0,
                    end=15.0,
                    duration=15.0,
                    text="Today we're going to learn a practical skill. First, we need to prepare the necessary tools and materials."
                ),
                AudioSegment(
                    uuid="seg-2",
                    id=2,
                    start=15.0,
                    end=scenario.duration,
                    duration=scenario.duration - 15.0,
                    text="Then we'll follow the steps one by one, ensuring each step is completed correctly."
                )
            ]
    
    return AudioTranscription(
        task="transcribe",
        language=scenario.language,
        duration=float(scenario.duration),
        text=text,
        audio_url="https://example.com/test_audio.mp3",
        segments=segments
    )


# ==================== 测试类 ====================

@pytest.mark.parametrize("scenario", TEST_SCENARIOS, ids=[s.name for s in TEST_SCENARIOS])
@pytest.mark.asyncio
class TestOutlineGenerationScenarios:
    """Outline Generation 故事梗概生成场景测试"""
    
    async def test_outline_generation_scenario(self, scenario: OutlineScenario):
        """测试故事梗概生成场景"""
        print(f"\n{'='*80}")
        print(f"📖 测试故事梗概生成场景: {scenario.name}")
        print(f"   描述: {scenario.description}")
        print(f"   故事类型: {scenario.story_type}")
        print(f"   时长: {scenario.duration}秒")
        print(f"   模式: {scenario.mode}")
        print(f"   风格偏好: {scenario.style_preference}")
        print(f"   语言: {scenario.language}")
        print(f"   期望章节数: {scenario.expected_chapters}")
        print(f"{'='*80}\n")
        
        # 设置语言上下文
        set_current_language(scenario.language)
        
        # 记录测试开始时间
        start_time = datetime.now()
        
        # 创建测试数据
        analysis_result = create_video_analysis_result(scenario)
        user_input = create_user_input(scenario)
        audio_transcription = get_audio_transcription(scenario)
        
        print(f"📊 测试数据:")
        print(f"   视频类型: {analysis_result.video_type}")
        print(f"   主要角色: {analysis_result.main_character}")
        print(f"   目标受众: {analysis_result.target_audience}")
        if audio_transcription:
            print(f"   音频时长: {audio_transcription.duration:.1f}秒")
            print(f"   音频语言: {audio_transcription.language}")
        print()
        
        # Mock send_event_func
        mock_send_event = AsyncMock()
        
        print(f"🤖 开始调用ReAct Agent生成故事梗概...")
        
        # 调用核心逻辑
        story_outline, messages = await _generate_outline_with_react_agent(
            user_input_data=user_input,
            analysis_data=analysis_result,
            audio_transcription=audio_transcription,
            send_event_func=mock_send_event
        )
        
        # 验证生成结果
        assert story_outline is not None, "故事梗概不应为空"
        assert isinstance(story_outline, StoryOutline), "应该返回StoryOutline对象"
        assert story_outline.title, "故事标题不应为空"
        assert story_outline.theme, "故事主题不应为空"
        assert story_outline.description, "故事描述不应为空"
        
        # 验证时长
        expected_duration = audio_transcription.duration if audio_transcription else scenario.duration
        assert abs(story_outline.total_duration - expected_duration) <= 2.0, \
            f"总时长应该接近目标时长: {story_outline.total_duration} vs {expected_duration}"
        
        # 验证章节
        assert story_outline.structure is not None, "故事结构不应为空"
        assert story_outline.structure.chapters is not None, "章节列表不应为空"
        assert len(story_outline.structure.chapters) > 0, "应该生成至少一个章节"
        
        chapters = story_outline.structure.chapters
        
        print(f"\n📖 故事梗概生成完成:")
        print(f"   标题: {story_outline.title}")
        print(f"   主题: {story_outline.theme}")
        print(f"   总时长: {story_outline.total_duration:.1f}秒 (目标: {expected_duration}秒)")
        print(f"   章节数: {len(chapters)}")
        print(f"   风格指南: {story_outline.style_guide}")
        print()
        
        # 验证章节数量（允许更大范围的偏差，因为LLM生成具有随机性）
        expected_min = max(1, scenario.expected_chapters - 2)  # 允许少2个章节
        expected_max = scenario.expected_chapters + 3  # 允许多3个章节
        assert expected_min <= len(chapters) <= expected_max, \
            f"章节数量应在 {expected_min}-{expected_max} 之间，实际: {len(chapters)}"
        
        # 验证每个章节的基本信息
        total_chapter_duration = 0
        for i, chapter in enumerate(chapters):
            print(f"   章节{i+1}: {chapter.title}")
            print(f"     描述: {chapter.description}")
            print(f"     时长: {chapter.duration:.1f}秒")
            print()
            
            # 基本字段验证
            assert chapter.title, f"章节{i+1}标题不应为空"
            assert chapter.description, f"章节{i+1}描述不应为空"
            assert chapter.duration > 0, f"章节{i+1}时长应大于0"
            assert chapter.order == i, f"章节{i+1}顺序应该为 0-based order={i}"
            
            total_chapter_duration += chapter.duration
        
        # 验证章节总时长与故事总时长一致
        assert abs(total_chapter_duration - story_outline.total_duration) <= 1.0, \
            f"章节总时长({total_chapter_duration:.1f}秒)应该与故事总时长({story_outline.total_duration:.1f}秒)一致"
        
        # 语言一致性验证
        if scenario.language == "zh":
            # 中文场景，检查是否包含中文字符
            chinese_chars_found = any('\u4e00' <= char <= '\u9fff' for char in story_outline.title)
            if not chinese_chars_found:
                print(f"⚠️  故事标题可能不是中文: {story_outline.title}")
        elif scenario.language == "en":
            # 英文场景，检查是否主要是英文
            if len(story_outline.title) > 10:
                chinese_chars = sum(1 for char in story_outline.title if '\u4e00' <= char <= '\u9fff')
                if chinese_chars > len(story_outline.title) * 0.3:  # 如果中文字符超过30%
                    print(f"⚠️  故事标题可能不是英文: {story_outline.title}")
        
        # 记录测试结束时间
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        print(f"⏱️ 测试耗时: {duration:.1f}秒")
        print(f"✅ 故事梗概生成测试通过!")
        print(f"{'='*80}\n")

    @pytest.mark.asyncio
    async def test_outline_structure_validation(self, scenario: OutlineScenario):
        """测试故事梗概结构验证（轻量级测试）"""
        print(f"\n{'='*40}")
        print(f"🔍 测试故事梗概结构: {scenario.name}")
        print(f"   语言: {scenario.language}")
        print(f"   时长: {scenario.duration}秒")
        print(f"{'='*40}")
        
        # 设置语言上下文
        set_current_language(scenario.language)
        
        # 创建测试数据
        analysis_result = create_video_analysis_result(scenario)
        user_input = create_user_input(scenario)
        audio_transcription = get_audio_transcription(scenario)
        
        # Mock send_event_func
        mock_send_event = AsyncMock()
        
        # 调用核心逻辑
        story_outline, messages = await _generate_outline_with_react_agent(
            user_input_data=user_input,
            analysis_data=analysis_result,
            audio_transcription=audio_transcription,
            send_event_func=mock_send_event
        )
        
        # 基本结构验证
        assert story_outline is not None, "故事梗概不应为空"
        assert hasattr(story_outline, 'structure'), "应该有结构属性"
        assert hasattr(story_outline.structure, 'chapters'), "结构应该有章节属性"
        
        chapters = story_outline.structure.chapters
        assert isinstance(chapters, list), "章节应该是列表"
        assert len(chapters) > 0, "应该有至少一个章节"
        
        # 验证章节顺序
        for i, chapter in enumerate(chapters):
            assert chapter.order == i, f"章节{i+1}顺序应该为 0-based order={i}"
            assert hasattr(chapter, 'id'), "章节应该有ID"
            assert hasattr(chapter, 'title'), "章节应该有标题"
            assert hasattr(chapter, 'description'), "章节应该有描述"
            assert hasattr(chapter, 'duration'), "章节应该有时长"
        
        # 验证时长分配合理性
        total_duration = sum(chapter.duration for chapter in chapters)
        expected_duration = audio_transcription.duration if audio_transcription else scenario.duration
        
        assert abs(total_duration - expected_duration) <= 2.0, \
            f"总时长分配应该合理: {total_duration} vs {expected_duration}"
        
        # 验证每个章节时长合理（更宽松的验证）
        avg_duration = expected_duration / len(chapters)
        for chapter in chapters:
            # 每个章节时长应该大于0且不超过总时长的80%（更宽松的验证）
            min_duration = 1.0  # 至少1秒
            max_duration = expected_duration * 0.8  # 不超过总时长的80%
            assert min_duration <= chapter.duration <= max_duration, \
                f"章节'{chapter.title}'时长({chapter.duration:.1f}秒)应在合理范围内({min_duration:.1f}-{max_duration:.1f}秒)"
        
        print(f"   ✅ 结构验证通过: {len(chapters)}个章节")
        print(f"   ✅ 时长验证通过: {total_duration:.1f}秒")
        print(f"   ✅ 章节顺序正确")


if __name__ == "__main__":
    # pytest tests/unit/services/agent/video/test_outline_generation_scenarios.py -v -s
    pytest.main([__file__, "-v", "-s", "--log-cli-level=INFO"])
