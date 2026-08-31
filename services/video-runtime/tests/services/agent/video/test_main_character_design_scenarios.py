"""
Main Character Design 角色设计测试
测试主要角色设计生成和图片匹配机制（参考 test_scene_scenarios.py）
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

from app.services.agent.video.main_character_design_service import (
    _match_characters_with_images,
)
from app.models.video_state import (
    StoryOutline, StoryStructure, StoryChapter,
    AudioTranscription, AudioSegment, CharacterProfile, CharacterProfiles
)
from app.models.user_options import UserOption
from app.utils.language_context import set_current_language


# ==================== 测试场景定义 ====================

class CharacterDesignScenario:
    """角色设计测试配置（避免以Test开头，防止pytest误识别）"""
    def __init__(
        self,
        name: str,
        story_type: str = "modern_urban",  # "modern_urban", "fantasy", "animation", "documentary"
        style_preference: str = "auto",  # "auto", "cartoon", "realistic", "anime", "3d"
        mode: str = "video",  # "video" or "audio"
        has_reference_images: bool = False,
        description: str = "",
        language: str = "zh",  # "zh" or "en"
        audio_data_key: str = None,  # 从 test_data 加载音频数据的键名
        expected_character_count: int = 2,  # 期望生成的角色数量
        test_image_matching: bool = False,  # 是否测试图片匹配功能
        generate_html_report: bool = False  # 是否生成HTML可视化报告
    ):
        self.name = name
        self.story_type = story_type
        self.style_preference = style_preference
        self.mode = mode
        self.has_reference_images = has_reference_images
        self.description = description
        self.language = language
        self.audio_data_key = audio_data_key
        self.expected_character_count = expected_character_count
        self.test_image_matching = test_image_matching
        self.generate_html_report = generate_html_report
    
    def __str__(self):
        return f"{self.name} ({self.story_type}, {self.style_preference}, {self.language})"


# 定义所有测试场景
TEST_SCENARIOS = [
    # ========== 中文场景 ==========
    
    # 现代都市风格 - 中文
    CharacterDesignScenario(
        name="modern_urban_auto_zh",
        story_type="modern_urban",
        style_preference="auto",
        mode="video",
        description="Modern urban story, auto style detection, Chinese",
        language="zh",
        expected_character_count=2
    ),
    CharacterDesignScenario(
        name="modern_urban_realistic_zh",
        story_type="modern_urban",
        style_preference="realistic",
        mode="video",
        description="Modern urban story, realistic style, Chinese",
        language="zh",
        expected_character_count=2
    ),
    
    # 动画/卡通风格 - 中文
    CharacterDesignScenario(
        name="animation_cartoon_zh",
        story_type="animation",
        style_preference="cartoon",
        mode="video",
        description="Animation story, cartoon style, Chinese",
        language="zh",
        expected_character_count=3
    ),
    CharacterDesignScenario(
        name="fantasy_anime_zh",
        story_type="fantasy",
        style_preference="anime",
        mode="video",
        description="Fantasy story, anime style, Chinese",
        language="zh",
        expected_character_count=3
    ),
    
    # 音频驱动场景 - 中文
    CharacterDesignScenario(
        name="audio_driven_realistic_zh",
        story_type="documentary",
        style_preference="realistic",
        mode="audio",
        description="Audio-driven documentary, realistic style, Chinese",
        language="zh",
        expected_character_count=2
    ),
    
    # 图片匹配测试 - 中文
    CharacterDesignScenario(
        name="image_matching_cartoon_zh",
        story_type="animation",
        style_preference="cartoon",
        mode="video",
        has_reference_images=True,
        test_image_matching=True,
        description="Test character-image matching with cartoon style, Chinese",
        language="zh",
        expected_character_count=2
    ),
    
    # ========== 英文场景 ==========
    
    # 现代都市风格 - 英文
    CharacterDesignScenario(
        name="modern_urban_auto_en",
        story_type="modern_urban",
        style_preference="auto",
        mode="video",
        description="Modern urban story, auto style detection, English",
        language="en",
        expected_character_count=2
    ),
    CharacterDesignScenario(
        name="modern_urban_realistic_en",
        story_type="modern_urban",
        style_preference="realistic",
        mode="video",
        description="Modern urban story, realistic style, English",
        language="en",
        expected_character_count=2
    ),
    
    # 动画/卡通风格 - 英文
    CharacterDesignScenario(
        name="animation_cartoon_en",
        story_type="animation",
        style_preference="cartoon",
        mode="video",
        description="Animation story, cartoon style, English",
        language="en",
        expected_character_count=3
    ),
    CharacterDesignScenario(
        name="fantasy_3d_en",
        story_type="fantasy",
        style_preference="3d",
        mode="video",
        description="Fantasy story, 3D style, English",
        language="en",
        expected_character_count=3
    ),
    
    # 音频驱动场景 - 英文
    CharacterDesignScenario(
        name="audio_driven_realistic_en",
        story_type="documentary",
        style_preference="realistic",
        mode="audio",
        description="Audio-driven documentary, realistic style, English",
        language="en",
        expected_character_count=2
    ),
    
    # 图片匹配测试 - 英文
    CharacterDesignScenario(
        name="image_matching_realistic_en",
        story_type="modern_urban",
        style_preference="realistic",
        mode="video",
        has_reference_images=True,
        test_image_matching=True,
        description="Test character-image matching with realistic style, English",
        language="en",
        expected_character_count=2
    ),
]


# ==================== Fixtures ====================

def create_story_outline(scenario: CharacterDesignScenario) -> StoryOutline:
    """
    创建测试故事大纲
    根据场景类型和语言创建不同的故事内容
    """
    
    # 根据故事类型和语言设置内容
    story_configs = {
        "modern_urban": {
            "zh": {
                "title": "都市青年的一天",
                "theme": "现代都市生活",
                "description": "讲述一位年轻人在繁忙都市中的日常生活，展现现代人的工作与生活平衡",
                "key_message": "在快节奏的都市生活中寻找属于自己的节奏",
                "style_guide": "现代简约风格，真实自然" if scenario.style_preference == "realistic" else "现代都市风格"
            },
            "en": {
                "title": "A Day in Urban Life",
                "theme": "Modern Urban Living",
                "description": "Following a young person's daily life in a bustling city, showcasing work-life balance",
                "key_message": "Finding your own rhythm in the fast-paced urban life",
                "style_guide": "Modern minimalist style, realistic and natural" if scenario.style_preference == "realistic" else "Modern urban style"
            }
        },
        "animation": {
            "zh": {
                "title": "奇幻冒险之旅",
                "theme": "动画冒险",
                "description": "一群可爱的角色踏上充满想象力的奇幻冒险旅程",
                "key_message": "友谊和勇气能够克服一切困难",
                "style_guide": "卡通动画风格，色彩鲜艳" if scenario.style_preference == "cartoon" else "动画风格"
            },
            "en": {
                "title": "Fantasy Adventure Journey",
                "theme": "Animated Adventure",
                "description": "A group of cute characters embark on an imaginative fantasy adventure",
                "key_message": "Friendship and courage can overcome all difficulties",
                "style_guide": "Cartoon animation style, vibrant colors" if scenario.style_preference == "cartoon" else "Animation style"
            }
        },
        "fantasy": {
            "zh": {
                "title": "魔法世界传说",
                "theme": "奇幻魔法",
                "description": "在一个充满魔法的世界里，英雄们为了拯救世界而战斗",
                "key_message": "正义终将战胜邪恶",
                "style_guide": "动漫风格，细腻精美" if scenario.style_preference == "anime" else "奇幻风格"
            },
            "en": {
                "title": "Magical World Legend",
                "theme": "Fantasy Magic",
                "description": "In a world full of magic, heroes fight to save the world",
                "key_message": "Justice will ultimately triumph over evil",
                "style_guide": "Anime style, detailed and exquisite" if scenario.style_preference == "anime" else "Fantasy style"
            }
        },
        "documentary": {
            "zh": {
                "title": "科技改变生活",
                "theme": "科技纪录片",
                "description": "探索现代科技如何改变我们的日常生活和工作方式",
                "key_message": "科技让生活更美好",
                "style_guide": "纪实摄影风格，真实自然"
            },
            "en": {
                "title": "Technology Changes Life",
                "theme": "Technology Documentary",
                "description": "Exploring how modern technology changes our daily life and work",
                "key_message": "Technology makes life better",
                "style_guide": "Documentary photography style, realistic and natural"
            }
        }
    }
    
    config = story_configs.get(scenario.story_type, story_configs["modern_urban"])[scenario.language]
    
    # 创建章节
    chapter = StoryChapter(
        id="chapter1",
        chapter_number=1,
        order=1,
        title=config["title"],
        description=config["description"],
        duration=60  # 默认60秒
    )
    
    return StoryOutline(
        title=config["title"],
        theme=config["theme"],
        description=config["description"],
        key_message=config["key_message"],
        style_guide=config["style_guide"],
        total_duration=60,
        structure=StoryStructure(chapters=[chapter])
    )


def get_reference_images(scenario: CharacterDesignScenario) -> List[str]:
    """
    获取参考图片URL列表
    根据场景设置返回相应的测试图片
    """
    if not scenario.has_reference_images:
        return []
    
    # 根据风格类型返回不同的测试图片
    if scenario.style_preference == "cartoon":
        return [
            "https://example.com/cartoon_character1.jpg",
            "https://example.com/cartoon_character2.jpg"
        ]
    elif scenario.style_preference == "realistic":
        return [
            "https://example.com/realistic_person1.jpg",
            "https://example.com/realistic_person2.jpg"
        ]
    else:
        return [
            "https://example.com/reference_image1.jpg",
            "https://example.com/reference_image2.jpg"
        ]


def get_audio_transcription(scenario: CharacterDesignScenario) -> Optional[AudioTranscription]:
    """
    获取音频转录数据
    根据场景模式返回相应的音频数据
    """
    if scenario.mode != "audio":
        return None
    
    # 根据语言创建不同的音频转录内容
    if scenario.language == "zh":
        text = "欢迎观看我们的科技纪录片。今天我们将探索人工智能如何改变我们的生活。从智能手机到自动驾驶汽车，科技正在以前所未有的速度发展。"
        segments = [
            AudioSegment(
                uuid="seg-1",
                id=1,
                start=0.0,
                end=15.0,
                duration=15.0,
                text="欢迎观看我们的科技纪录片。今天我们将探索人工智能如何改变我们的生活。"
            ),
            AudioSegment(
                uuid="seg-2",
                id=2,
                start=15.0,
                end=30.0,
                duration=15.0,
                text="从智能手机到自动驾驶汽车，科技正在以前所未有的速度发展。"
            )
        ]
    else:
        text = "Welcome to our technology documentary. Today we will explore how artificial intelligence is changing our lives. From smartphones to autonomous vehicles, technology is advancing at an unprecedented pace."
        segments = [
            AudioSegment(
                uuid="seg-1",
                id=1,
                start=0.0,
                end=15.0,
                duration=15.0,
                text="Welcome to our technology documentary. Today we will explore how artificial intelligence is changing our lives."
            ),
            AudioSegment(
                uuid="seg-2",
                id=2,
                start=15.0,
                end=30.0,
                duration=15.0,
                text="From smartphones to autonomous vehicles, technology is advancing at an unprecedented pace."
            )
        ]
    
    return AudioTranscription(
        task="transcribe",
        language=scenario.language,
        duration=30.0,
        text=text,
        audio_url="https://example.com/test_audio.mp3",
        segments=segments
    )


# ==================== 测试类 ====================

@pytest.mark.parametrize("scenario", TEST_SCENARIOS, ids=[s.name for s in TEST_SCENARIOS])
@pytest.mark.asyncio
class TestMainCharacterDesignScenarios:
    """Main Character Design 角色设计场景测试"""
    
    async def test_character_generation_scenario(self, scenario: CharacterDesignScenario):
        """测试角色生成场景"""
        # 跳过有参考图片的场景，因为example.com的URL会导致OpenAI API错误
        if scenario.has_reference_images:
            pytest.skip("跳过有参考图片的角色生成测试，避免无效URL错误")
        
        print(f"\n{'='*80}")
        print(f"🎭 测试角色设计场景: {scenario.name}")
        print(f"   描述: {scenario.description}")
        print(f"   故事类型: {scenario.story_type}")
        print(f"   风格偏好: {scenario.style_preference}")
        print(f"   模式: {scenario.mode}")
        print(f"   语言: {scenario.language}")
        print(f"   期望角色数: {scenario.expected_character_count}")
        print(f"   参考图片: {'有' if scenario.has_reference_images else '无'}")
        print(f"{'='*80}\n")
        
        # 设置语言上下文
        set_current_language(scenario.language)
        
        # 记录测试开始时间
        start_time = datetime.now()
        
        # 创建测试数据
        story_outline = create_story_outline(scenario)
        reference_images = get_reference_images(scenario)
        audio_transcription = get_audio_transcription(scenario)
        
        print(f"📖 故事信息:")
        print(f"   标题: {story_outline.title}")
        print(f"   主题: {story_outline.theme}")
        print(f"   风格指南: {story_outline.style_guide}")
        if audio_transcription:
            print(f"   音频时长: {audio_transcription.duration:.1f}秒")
            print(f"   音频语言: {audio_transcription.language}")
        print()
        
        pytest.skip(
            "character craft migrated to character-director deep-agent; "
            "mustache build_prompt_for_main_character_generation removed"
        )
        raw_result = None  # unreachable
        
        character_profiles = raw_result["parsed"]
        raw_message = raw_result["raw"]
        
        # 验证生成结果
        assert character_profiles is not None, "角色档案不应为空"
        assert isinstance(character_profiles, CharacterProfiles), "应该返回CharacterProfiles对象"
        assert len(character_profiles.characters) > 0, "应该生成至少一个角色"
        
        characters = character_profiles.characters
        user_message = character_profiles.user_message
        
        print(f"\n🎭 角色生成完成:")
        print(f"   生成角色数: {len(characters)}")
        print(f"   用户消息: {user_message}")
        print()
        
        # 验证角色数量（允许一定范围的偏差）
        expected_min = max(1, scenario.expected_character_count - 1)
        expected_max = scenario.expected_character_count + 2
        assert expected_min <= len(characters) <= expected_max, \
            f"角色数量应在 {expected_min}-{expected_max} 之间，实际: {len(characters)}"
        
        # 验证每个角色的基本信息
        for i, character in enumerate(characters):
            print(f"   角色{i+1}: {character.name}")
            print(f"     描述: {character.description}")
            print(f"     性格: {character.personality}")
            print(f"     外观: {character.appearance}")
            print(f"     作用: {character.role}")
            print(f"     风格: {character.style}")
            print(f"     体型: {character.body_type}")
            print()
            
            # 基本字段验证
            assert character.name, f"角色{i+1}名称不应为空"
            assert character.description, f"角色{i+1}描述不应为空"
            assert character.personality, f"角色{i+1}性格不应为空"
            assert character.appearance, f"角色{i+1}外观不应为空"
            
            # 风格一致性验证
            if scenario.style_preference != "auto":
                # 如果指定了风格，检查是否包含相关关键词
                style_keywords = {
                    "realistic": ["真实", "摄影", "写实", "realistic", "photography", "photographic"],
                    "cartoon": ["卡通", "动画", "cartoon", "animated", "animation"],
                    "anime": ["动漫", "二次元", "anime", "manga"],
                    "3d": ["3D", "三维", "立体", "three-dimensional"]
                }
                
                if scenario.style_preference in style_keywords:
                    keywords = style_keywords[scenario.style_preference]
                    style_text = character.style.lower()
                    has_keyword = any(keyword.lower() in style_text for keyword in keywords)
                    if not has_keyword:
                        print(f"⚠️  角色{i+1}风格可能不匹配期望: 期望包含{keywords}相关词汇，实际: {character.style}")
        
        # 语言一致性验证
        if scenario.language == "zh":
            # 中文场景，检查是否包含中文字符
            chinese_chars_found = any('\u4e00' <= char <= '\u9fff' for char in user_message or "")
            if not chinese_chars_found and user_message:
                print(f"⚠️  用户消息可能不是中文: {user_message}")
        elif scenario.language == "en":
            # 英文场景，检查是否主要是英文
            if user_message and len(user_message) > 10:
                chinese_chars = sum(1 for char in user_message if '\u4e00' <= char <= '\u9fff')
                if chinese_chars > len(user_message) * 0.3:  # 如果中文字符超过30%
                    print(f"⚠️  用户消息可能不是英文: {user_message}")
        
        # 记录测试结束时间
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        print(f"⏱️ 测试耗时: {duration:.1f}秒")
        print(f"✅ 角色生成测试通过!")
        print(f"{'='*80}\n")

    @pytest.mark.asyncio
    async def test_character_image_matching_scenario(self, scenario: CharacterDesignScenario):
        """测试角色图片匹配场景"""
        if not scenario.test_image_matching:
            pytest.skip("只测试图片匹配相关的场景")
        
        print(f"\n{'='*80}")
        print(f"🖼️ 测试角色图片匹配: {scenario.name}")
        print(f"   描述: {scenario.description}")
        print(f"   风格偏好: {scenario.style_preference}")
        print(f"   语言: {scenario.language}")
        print(f"{'='*80}\n")
        
        # 设置语言上下文
        set_current_language(scenario.language)
        
        # 创建测试角色
        test_characters = []
        if scenario.language == "zh":
            test_characters = [
                CharacterProfile(
                    id="char-1",
                    name="小明",
                    description="活泼开朗的男孩",
                    personality="开朗、好奇、友善",
                    appearance="短发、大眼睛、穿着休闲T恤",
                    role="主角",
                    style="卡通风格" if scenario.style_preference == "cartoon" else "真实摄影风格",
                    body_type="中等身材"
                ),
                CharacterProfile(
                    id="char-2",
                    name="小红",
                    description="温柔可爱的女孩",
                    personality="温柔、细心、善良",
                    appearance="长发、甜美笑容、穿着连衣裙",
                    role="配角",
                    style="卡通风格" if scenario.style_preference == "cartoon" else "真实摄影风格",
                    body_type="娇小"
                )
            ]
        else:
            test_characters = [
                CharacterProfile(
                    id="char-1",
                    name="Alex",
                    description="Energetic and cheerful boy",
                    personality="Cheerful, curious, friendly",
                    appearance="Short hair, big eyes, wearing casual T-shirt",
                    role="Main character",
                    style="Cartoon style" if scenario.style_preference == "cartoon" else "Realistic photography style",
                    body_type="Medium build"
                ),
                CharacterProfile(
                    id="char-2",
                    name="Emma",
                    description="Gentle and lovely girl",
                    personality="Gentle, careful, kind",
                    appearance="Long hair, sweet smile, wearing dress",
                    role="Supporting character",
                    style="Cartoon style" if scenario.style_preference == "cartoon" else "Realistic photography style",
                    body_type="Petite"
                )
            ]
        
        # 获取参考图片
        reference_images = get_reference_images(scenario)
        
        print(f"🎭 测试角色:")
        for i, char in enumerate(test_characters):
            print(f"   角色{i+1}: {char.name} - {char.description}")
            print(f"     风格: {char.style}")
        print(f"📸 参考图片: {len(reference_images)} 张")
        print()
        
        # 使用真实LLM进行匹配
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3, timeout=180, stream_usage=True)
        
        print(f"🔍 开始角色图片匹配...")
        
        matching_result = await _match_characters_with_images(
            characters=test_characters,
            images=reference_images,
            llm=llm
        )
        
        # 验证匹配结果
        assert "matched_characters" in matching_result, "应该包含匹配的角色"
        assert "unmatched_characters" in matching_result, "应该包含未匹配的角色"
        assert "unused_images" in matching_result, "应该包含未使用的图片"
        
        matched_characters = matching_result["matched_characters"]
        unmatched_characters = matching_result["unmatched_characters"]
        unused_images = matching_result["unused_images"]
        
        print(f"\n🔍 匹配结果:")
        print(f"   匹配成功: {len(matched_characters)} 个角色")
        print(f"   需要生成: {len(unmatched_characters)} 个角色")
        print(f"   未使用图片: {len(unused_images)} 张")
        print()
        
        # 验证匹配逻辑
        total_processed = len(matched_characters) + len(unmatched_characters)
        assert total_processed == len(test_characters), \
            f"处理的角色总数应该等于输入角色数: {total_processed} != {len(test_characters)}"
        
        # 打印匹配详情
        if matched_characters:
            print(f"✅ 匹配成功的角色:")
            for character, image_url in matched_characters:
                print(f"   - {character.name}: {image_url}")
        
        if unmatched_characters:
            print(f"🎨 需要生成图片的角色:")
            for character in unmatched_characters:
                print(f"   - {character.name}: {character.style}")
        
        if unused_images:
            print(f"📸 未使用的图片:")
            for image_url in unused_images:
                print(f"   - {image_url}")
        
        print(f"✅ 角色图片匹配测试通过!")
        print(f"{'='*80}\n")

    @pytest.mark.asyncio
    async def test_prompt_construction_scenario(self, scenario: CharacterDesignScenario):
        """测试提示词构建场景（轻量级测试）"""
        pytest.skip(
            "character craft migrated to character-director deep-agent; "
            "mustache build_prompt_for_main_character_generation removed"
        )
        print(f"\n{'='*40}")
        print(f"📝 测试提示词构建: {scenario.name}")
        print(f"   语言: {scenario.language}")
        print(f"   风格: {scenario.style_preference}")
        print(f"{'='*40}")
        
        # 设置语言上下文
        set_current_language(scenario.language)
        
        # 创建测试数据
        story_outline = create_story_outline(scenario)
        reference_images = get_reference_images(scenario)
        audio_transcription = get_audio_transcription(scenario)
        
        messages, _ = None, None
        
        # 基本验证
        assert len(messages) == 2, "应该包含系统消息和用户消息"
        
        system_message = messages[0]
        user_message = messages[1]
        
        assert system_message.content, "系统消息不应为空"
        assert user_message.content, "用户消息不应为空"
        
        # 验证消息内容包含关键信息
        system_content = system_message.content
        assert "角色设计师" in system_content or "character designer" in system_content.lower(), \
            "系统消息应该包含角色设计师相关内容"
        
        # 验证用户消息格式
        if reference_images:
            # 有图片时应该是多模态格式
            assert isinstance(user_message.content, list), "有图片时应该使用多模态格式"
            text_parts = [part for part in user_message.content if part.get("type") == "text"]
            image_parts = [part for part in user_message.content if part.get("type") == "image_url"]
            assert len(text_parts) >= 1, "应该包含文本部分"
            assert len(image_parts) == len(reference_images), f"图片部分数量应该匹配: {len(image_parts)} != {len(reference_images)}"
        else:
            # 无图片时应该是纯文本格式
            assert isinstance(user_message.content, str), "无图片时应该使用纯文本格式"
        
        # 验证故事信息是否包含在提示词中
        if isinstance(user_message.content, str):
            content_text = user_message.content
        else:
            content_text = " ".join([part.get("text", "") for part in user_message.content if part.get("type") == "text"])
        
        assert story_outline.title in content_text, "应该包含故事标题"
        # 检查描述或关键信息是否包含在提示词中（主题可能不会直接包含）
        assert story_outline.description in content_text or story_outline.key_message in content_text, "应该包含故事描述或关键信息"
        
        # 验证音频信息（如果有）
        if audio_transcription:
            assert str(audio_transcription.duration) in content_text, "应该包含音频时长信息"
            assert audio_transcription.text in content_text, "应该包含音频文本"
        
        print(f"   ✅ 系统消息长度: {len(system_content)} 字符")
        print(f"   ✅ 用户消息类型: {'多模态' if isinstance(user_message.content, list) else '纯文本'}")
        if reference_images:
            print(f"   ✅ 图片数量: {len(reference_images)}")
        if audio_transcription:
            print(f"   ✅ 音频时长: {audio_transcription.duration:.1f}秒")
        
        print(f"   ✅ 提示词构建验证通过!")


if __name__ == "__main__":
    # 运行测试
    # 全部场景: pytest tests/unit/services/agent/video/test_main_character_design_scenarios.py -v -s --log-cli-level=INFO
    # 单个场景: pytest tests/unit/services/agent/video/test_main_character_design_scenarios.py::TestMainCharacterDesignScenarios::test_character_generation_scenario[modern_urban_auto_zh] -v -s
    pytest.main([__file__, "-v", "-s", "--log-cli-level=INFO"])
