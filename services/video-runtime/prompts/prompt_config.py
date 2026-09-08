"""
Prompt 配置 - 统一管理所有 prompt 的配置信息
"""
import sys
from pathlib import Path
from typing import Dict, Any
from enum import Enum
from langchain_core.runnables import Runnable
from app.llm.openai_failover import FailoverChatOpenAI as ChatOpenAI


class PromptName(str, Enum):
    """
    Prompt 名称枚举
    
    命名规范: {agent}_{node}_{function}
    - agent: video (VideoAgent)
    - node: video_generation, keyframe_generation
    - function: 具体功能描述
    """
    
    # ==================== Video Generation Prompts ====================
    VIDEO_VIDEO_GENERATION_STYLE_DETECTION = "video_video_generation_style_detection"
    VIDEO_VIDEO_GENERATION_PROMPT_BATCH_GENERATION = "video_video_generation_prompt_batch_generation"
    VIDEO_VIDEO_GENERATION_PROMPT_EVALUATION_FIX = "video_video_generation_prompt_evaluation_fix"
    VIDEO_VIDEO_GENERATION_TOOL_EXECUTION = "video_video_generation_tool_execution"
    
    # ==================== Keyframe Generation Prompts ====================
    VIDEO_KEYFRAME_GENERATION_PROMPT_BATCH_GENERATION = "video_keyframe_generation_prompt_batch_generation"
    VIDEO_KEYFRAME_GENERATION_PROMPT_EVALUATION_FIX = "video_keyframe_generation_prompt_evaluation_fix"
    VIDEO_KEYFRAME_GENERATION_TOOL_EXECUTION = "video_keyframe_generation_tool_execution"
    
    # ==================== Outline Generation Prompts ====================
    VIDEO_OUTLINE_GENERATION_AUDIO_DRIVEN = "video_outline_generation_audio_driven"
    VIDEO_OUTLINE_GENERATION_VIDEO_DRIVEN = "video_outline_generation_video_driven"
    
    # ==================== Scene Generation Prompts ====================
    VIDEO_SCENE_GENERATION_AUDIO_DRIVEN = "video_scene_generation_audio_driven"
    VIDEO_SCENE_GENERATION_VIDEO_DRIVEN = "video_scene_generation_video_driven"
    
    # ==================== Storyboard Detail Generation Prompts ====================
    VIDEO_STORYBOARD_DETAIL_GENERATION = "video_storyboard_detail_generation"
    VIDEO_STORYBOARD_FIRST_FRAME_REVISION = "video_storyboard_first_frame_revision"
    VIDEO_PER_SHOT_GENERATION_ROUTING = "video_per_shot_generation_routing"
    
    # ==================== Visual Elements Matching Prompts ====================
    VIDEO_VISUAL_ELEMENTS_MATCHING = "video_visual_elements_matching"
    
    # ==================== Main Character Design Prompts ====================
    VIDEO_MAIN_CHARACTER_GENERATION = "video_main_character_generation"
    VIDEO_MAIN_CHARACTER_MATCHING = "video_main_character_matching"
    VIDEO_MAIN_CHARACTER_IMAGE_GENERATION = "video_main_character_image_generation"
    VIDEO_MAIN_CHARACTER_MULTI_VIEW_GENERATION = "video_main_character_multi_view_generation"
    VIDEO_SINGLE_CHARACTER_TOOL_CALL_PROMPT = "video_single_character_tool_call_prompt"
    
    # ==================== Character Fusion Prompts ====================
    VIDEO_CHARACTER_FUSION_IMAGE_GENERATION = "video_character_fusion_image_generation"
    
    # ==================== Video Analysis Prompts ====================
    VIDEO_REQUIREMENTS_ANALYSIS = "video_requirements_analysis"
    VIDEO_GEMINI_ANALYSIS = "video_gemini_analysis"
    VIDEO_CONTENT_CATEGORY_EARLY_INFERENCE = "video_content_category_early_inference"
    
    # ==================== Video Lipsync Prompts ====================
    VIDEO_LIPSYNC_SUITABILITY_ANALYSIS = "video_lipsync_suitability_analysis"
    
    # ==================== Agent Router Prompts ====================
    AGENT_ROUTER_ANALYSIS = "agent_router_analysis"
    AGENT_ROUTER_CLARIFY = "agent_router_clarify"
    AGENT_ROUTER_USER_OPTION_MERGE = "agent_router_user_option_merge"
    
    # ==================== Story Generation Prompts ====================
    STORY_DIRECT_GENERATION = "story_direct_generation"
    
    # ==================== Music Generation Prompts ====================
    MUSIC_AGENT_TOOL_EXECUTION = "music_agent_tool_execution"
    
    # ==================== Image Generation Prompts ====================
    IMAGE_AGENT_TOOL_EXECUTION = "image_agent_tool_execution"

    # ==================== Video Gen (单模型直生) Prompts ====================
    VIDEO_AGENT_TOOL_EXECUTION = "video_agent_tool_execution"
    
    # ==================== Audio Transcription & Music Generation Prompts ====================
    VIDEO_AUDIO_TRANSCRIPTION = "video_audio_transcription"
    VIDEO_MUSIC_INTENT_ANALYSIS = "video_music_intent_analysis"
    VIDEO_MUSIC_GENERATION = "video_music_generation"
    VIDEO_MUSIC_BGM_GENERATION = "video_music_bgm_generation"
    VIDEO_MUSIC_SMART_CLIP_ANALYSIS = "video_music_smart_clip_analysis"
    
    # ==================== Subtitle Prompts ====================
    VIDEO_SUBTITLE_LINE_GROUPING = "video_subtitle_line_grouping"

    # ==================== Utils Prompts ====================
    VIDEO_COMPLETION_MESSAGE = "video_completion_message"

    # ==================== Audio Effect Generation ====================
    VIDEO_AUDIO_EFFECT_GENERATION = "video_audio_effect_generation"

    # ==================== Narration Generation ====================
    VIDEO_NARRATION_GENERATION = "video_narration_generation"

    # ==================== Keyframe Reflection ====================
    VIDEO_SINGLE_KEYFRAME_REFLECTION_PROMPT = "video_single_keyframe_reflection_prompt"

    # ==================== Regenerate: instruction → full prompt (no image) ====================
    VIDEO_INSTRUCTION_MERGE_TO_FULL_PROMPT = "video_instruction_merge_to_full_prompt"

    # ==================== Regenerate: 融合编辑弹窗 — 建议指令芯片（按 artifact 分模板，多模态） ====================
    VIDEO_ARTIFACT_SUGGEST_PRESETS_CHARACTER = "video_artifact_suggest_presets_character"
    VIDEO_ARTIFACT_SUGGEST_PRESETS_KEYFRAME = "video_artifact_suggest_presets_keyframe"
    VIDEO_ARTIFACT_SUGGEST_PRESETS_VIDEO = "video_artifact_suggest_presets_video"

    # ==================== Image Character Consistency (I2I 人物一致性校验) ====================
    IMAGE_CHARACTER_CONSISTENCY_CHECK = "image_character_consistency_check"

    # ==================== Video Consistency (I2V 首帧一致性 + 首帧违规) ====================
    VIDEO_CONSISTENCY_CHECK = "video_consistency_check"

    # ==================== Vision Analyze (图片/视频理解) ====================
    VISION_ANALYZE = "vision_analyze"

    # ==================== Video Edit Agent (Companion Agent via MCP) ====================
    VIDEO_EDIT_AGENT = "video_edit_agent"

# 导入结构化输出模型
sys.path.append(str(Path(__file__).parent.parent))
from app.schemas.video_llm import (
    StyleDetectionResult,
    BatchVideoPromptResult,
    BatchPromptEvaluationResult,
    BatchKeyframePromptFixResult,
    CharacterConsistencyResult,
    VideoConsistencyCheckResult,
    GeminiVideoAnalysisResult,
    KeyframeReflectionVlmStructuredOutput,
)
from app.models.image_result import (
    VideoGenerationResult,
    ImageGenerationResult,
    CharacterImageGenerationResult,
    MusicGenerationResult,
    StoryGenerationResult,
    SpeechGenerationResult,
    AudioGenerationResult,
)
from app.models.video_state import (
    VideoAnalysisResult,
    ContentCategoryEarlyOutput,
    StoryOutlineForLLMMode,
    ScenesCollectionForLLM,
    StoryboardDetailLLMOutput,
    CharacterProfiles,
)
from app.schemas.keyframe_prompt import (
    BatchKeyframePromptResult,
)
from app.schemas.response import ClarifyResponse
from app.tools.transcribe.gemini import GeminiTranscriptionResult
from app.schemas.subtitle import SubtitleGroupsResponse
from app.models.user_options import UserOption
from app.schemas.per_shot_routing import PerShotGenerationRoutingOutput
from app.schemas.prompt_edit_presets import PromptEditPresetsOutput

# 尝试导入 Google LLM 和 Safety Settings
try:
    from langchain_google_genai import ChatGoogleGenerativeAI, HarmBlockThreshold, HarmCategory
    HAS_GOOGLE = True
    # Google Safety Settings - 统一配置，禁用所有安全过滤
    GOOGLE_SAFETY_SETTINGS = {
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    }
except ImportError:
    HAS_GOOGLE = False
    GOOGLE_SAFETY_SETTINGS = {}


# OpenAI reasoning 系列：不可传自定义 temperature（传 0 会 400）
_OPENAI_REASONING_MODELS = frozenset({
    "gpt-5-nano",
    "gpt-5-mini",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
})

# GPT-5.6 官方推荐 Responses API（推理质量更好；且 Chat Completions 上
# function tools + 默认 reasoning 会 400）。gpt-5 / 5.4 / 4.1 不受此限制，保持原样。
_OPENAI_GPT_56_MODELS = frozenset({
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
})


def create_llm(model_config: Dict[str, Any]) -> Runnable:
    """
    根据配置创建 LLM 实例
    
    Args:
        model_config: 模型配置字典，包含 model, temperature, max_tokens, timeout 等
            - model: 可以是字符串（如 "gpt-4.1-mini"）或 LLMModel enum
            - role: 可选，"text"|"tool"|"multimodal"，由 LLM_QUALITY 解析为具体 model
        
    Returns:
        配置好的 LLM 实例
    """
    from app.models.tool_enums import LLMModel
    from prompts.llm_model_profiles import resolve_model_config

    model_config = resolve_model_config(model_config)
    model_name_raw = model_config.get("model", LLMModel.GPT_4_1_MINI.value)
    
    # 规范化模型名称：如果是 LLMModel enum，直接使用；如果是字符串，验证并转换为 enum
    if isinstance(model_name_raw, LLMModel):
        llm_model = model_name_raw
    else:
        # 验证模型名称是否在 enum 中
        try:
            llm_model = LLMModel(model_name_raw)
        except ValueError:
            raise ValueError(
                f"不支持的模型名称: {model_name_raw}。"
                f"支持的模型: {[m.value for m in LLMModel]}"
            )
    
    model_name = llm_model.value
    temperature = model_config.get("temperature")
    max_tokens = model_config.get("max_tokens")
    timeout = model_config.get("timeout", 180)
    # OpenAI streaming 时必须有 stream_usage=True，否则 on_llm_end 拿不到 usage（全为 0）
    # Gemini 默认就有 usage，不需要此参数
    stream_usage = model_config.get("stream_usage", True)
    streaming = model_config.get("streaming")

    _openai_models = {
        LLMModel.GPT_4_1_MINI,
        LLMModel.GPT_5_NANO,
        LLMModel.GPT_5_MINI,
        LLMModel.GPT_5_4_MINI,
        LLMModel.GPT_5_4_NANO,
        LLMModel.GPT_5_6_SOL,
        LLMModel.GPT_5_6_TERRA,
        LLMModel.GPT_5_6_LUNA,
    }
    _gemini_models = {
        LLMModel.GEMINI_2_5_FLASH,
        LLMModel.GEMINI_3_FLASH_PREVIEW,
        LLMModel.GEMINI_3_PRO_PREVIEW,
        LLMModel.GEMINI_3_1_PRO_PREVIEW,
        LLMModel.GEMINI_2_0_FLASH,
    }
    
    # 根据 enum 类型选择 provider
    if llm_model in _openai_models:
        # OpenAI 模型
        openai_kwargs = dict(
            model=model_name,
            max_tokens=max_tokens,
            timeout=timeout,
            stream_usage=stream_usage
        )
        if streaming is not None:
            openai_kwargs["streaming"] = streaming
        # GPT-5 / 5.4 / 5.6 为推理模型，仅接受默认 temperature
        if temperature is not None and model_name not in _OPENAI_REASONING_MODELS:
            openai_kwargs["temperature"] = temperature

        # GPT-5.6：默认 Responses（官方）；显式 use_responses_api=False 时退回 Chat Completions
        # 并强制 reasoning_effort=none，否则 bind_tools 会 400。
        use_responses_api = model_config.get("use_responses_api")
        if model_name in _OPENAI_GPT_56_MODELS:
            if use_responses_api is None:
                openai_kwargs["use_responses_api"] = True
            else:
                openai_kwargs["use_responses_api"] = bool(use_responses_api)
            if "reasoning_effort" in model_config:
                openai_kwargs["reasoning_effort"] = model_config["reasoning_effort"]
            elif openai_kwargs.get("use_responses_api") is False:
                openai_kwargs["reasoning_effort"] = "none"
        else:
            if use_responses_api is not None:
                openai_kwargs["use_responses_api"] = bool(use_responses_api)
            if "reasoning_effort" in model_config:
                openai_kwargs["reasoning_effort"] = model_config["reasoning_effort"]
        return ChatOpenAI(**openai_kwargs)
    elif llm_model in _gemini_models:
        # Gemini 模型
        if not HAS_GOOGLE:
            raise ImportError(f"需要安装 langchain-google-genai 才能使用 {model_name}")
        # Gemini 配置：统一使用 GOOGLE_SAFETY_SETTINGS 禁用安全过滤
        # Gemini 使用 max_output_tokens 而不是 max_tokens
        # 优先使用配置中的 max_output_tokens，如果没有则使用已取的 max_tokens
        max_output_tokens = model_config.get("max_output_tokens") or max_tokens
        gemini_kwargs = {
            "model": model_name,
            "timeout": timeout,
            "safety_settings": GOOGLE_SAFETY_SETTINGS
        }
        if max_output_tokens:
            gemini_kwargs["max_output_tokens"] = max_output_tokens
        if temperature is not None:
            gemini_kwargs["temperature"] = temperature
        if streaming is not None:
            gemini_kwargs["streaming"] = streaming
        return ChatGoogleGenerativeAI(**gemini_kwargs)
    else:
        # 理论上不会到这里，因为所有模型都已处理
        raise ValueError(f"未处理的模型类型: {llm_model}")


# Prompt 配置映射
# 使用 PromptName Enum 作为 key，确保类型安全
PROMPTS_CONFIG: Dict[PromptName, Dict[str, Any]] = {
    # ==================== Video Generation Prompts ====================
    PromptName.VIDEO_VIDEO_GENERATION_STYLE_DETECTION: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频风格检测 - 从用户输入和故事大纲中识别视频风格类型",
        "schema": StyleDetectionResult,
        "tags": ["video", "video_generation", "style"],
        "model_config": {
            # detect_video_style 使用传入的 llm (video_agent_service: gpt-4.1-mini)
            "role": "text",
            "temperature": 0.5,
            "timeout": 180
        },
        "resilience": {
            "model_fallback_chain": [
                {"role": "multimodal", "timeout": 240, "max_output_tokens": 8192},
            ],
        },
    },
    PromptName.VIDEO_VIDEO_GENERATION_PROMPT_BATCH_GENERATION: {
        # file removed — craft in kit/skills; model_config only
        "description": "批量视频 Prompt 生成 - 为多个连续镜头生成连贯的视频提示词",
        "schema": BatchVideoPromptResult,
        "tags": ["video", "video_generation", "prompt"],
        "model_config": {
            # generate_batch_video_prompts 使用 model_service.gemini_llm
            "role": "multimodal",
            "timeout": 240,
            "max_output_tokens": 8192  # 增加输出token限制，从默认2048增加到8192，支持生成更长的批量prompts
        },
        "resilience": {
            "model_fallback_chain": [
                {
                    "role": "tool",
                    "temperature": 0.5,
                    "timeout": 240,
                    "max_output_tokens": 8192,
                },
            ],
            "same_route_extra_retries": 2,
        },
    },
    PromptName.VIDEO_VIDEO_GENERATION_PROMPT_EVALUATION_FIX: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频 Prompt 评估与修正 - I2V 首帧约束（对照首帧图修正 i2v_prompt）",
        "schema": BatchPromptEvaluationResult,
        "tags": ["video", "video_generation", "evaluation"],
        "model_config": {
            # evaluate_and_fix_batch_prompts 使用 model_service.gemini_llm
            "role": "multimodal",
            "timeout": 240
        },
        "resilience": {
            "model_fallback_chain": [
                {
                    "role": "tool",
                    "temperature": 0.5,
                    "timeout": 240,
                    "max_output_tokens": 8192,
                },
            ],
            "same_route_extra_retries": 2,
        },
    },
    PromptName.VIDEO_VIDEO_GENERATION_TOOL_EXECUTION: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频工具执行 - 指导 LLM 调用视频生成工具（Pollo/Sora）",
        "schema": VideoGenerationResult,
        "tags": ["video", "video_generation", "tool"],
        "model_config": {
            # execute_batch_video_generation 使用传入的 llm (video_agent_service: gpt-4.1-mini)
            # 创建 react agent 时: create_react_agent(llm, tools=video_tools)
            "role": "tool",
            "temperature": 0.5,
            "timeout": 180
        },
        "resilience": {
            "model_fallback_chain": [
                {
                    "model": "gemini-3-flash-preview",
                    # Gemini 3：官方建议默认 1.0；须覆盖主跳 GPT 的 temperature，避免合并继承 0.5
                    "temperature": 1.0,
                    "timeout": 240,
                    "max_output_tokens": 8192,
                },
            ],
            "same_route_extra_retries": 2,
        },
    },
    
    # ==================== Keyframe Generation Prompts ====================
    PromptName.VIDEO_KEYFRAME_GENERATION_PROMPT_BATCH_GENERATION: {
        # file removed — craft in kit/skills; model_config only
        "description": "批量关键帧 Prompt 生成 - 为多个连续镜头生成连贯的关键帧提示词",
        "schema": BatchKeyframePromptResult,
        "tags": ["video", "keyframe_generation", "prompt"],
        "model_config": {
            # generate_batch_keyframe_prompts 使用 model_service.gemini_llm
            "role": "multimodal",
            "timeout": 300,
            "max_output_tokens": 8192
        },
        "resilience": {
            "model_fallback_chain": [
                {
                    "role": "tool",
                    "temperature": 0.5,
                    "timeout": 300,
                    "max_output_tokens": 8192,
                },
            ],
            "same_route_extra_retries": 2,
        },
    },
    PromptName.VIDEO_KEYFRAME_GENERATION_PROMPT_EVALUATION_FIX: {
        # file removed — craft in kit/skills; model_config only
        "description": "关键帧 Prompt 评估与修正 - 内容审核（针对图像）",
        "schema": BatchKeyframePromptFixResult,
        "tags": ["video", "keyframe_generation", "evaluation"],
        "model_config": {
            # evaluate_and_fix_batch_keyframe_prompts 使用 model_service.gemini_llm
            "role": "multimodal",
            "timeout": 240
        },
        "resilience": {
            "model_fallback_chain": [
                {
                    "role": "tool",
                    "temperature": 0.5,
                    "timeout": 240,
                    "max_output_tokens": 8192,
                },
            ],
            "same_route_extra_retries": 2,
        },
    },
    PromptName.VIDEO_KEYFRAME_GENERATION_TOOL_EXECUTION: {
        # file removed — craft in kit/skills; model_config only
        "description": "关键帧工具执行 - 指导 LLM 调用关键帧生成工具（Flux等）",
        "schema": ImageGenerationResult,
        "tags": ["video", "keyframe_generation", "tool"],
        "model_config": {
            # execute_batch_keyframe_generation 使用传入的 llm (video_agent_service: gpt-4.1-mini)
            # 创建 react agent 时: create_react_agent(llm, tools=keyframe_tools)
            "role": "tool",
            "temperature": 0.5,
            "timeout": 180
        },
        "resilience": {
            "model_fallback_chain": [
                {
                    "model": "gemini-3-flash-preview",
                    # Gemini 3：官方建议默认 1.0；须覆盖主跳 GPT 的 temperature，避免合并继承 0.5
                    "temperature": 1.0,
                    "timeout": 240,
                    "max_output_tokens": 8192,
                },
            ],
            "same_route_extra_retries": 2,
        },
    },
    
    # ==================== Outline Generation Prompts ====================
    PromptName.VIDEO_OUTLINE_GENERATION_AUDIO_DRIVEN: {
        # file removed — craft in kit/skills; model_config only
        "description": "音频驱动的故事梗概生成 - 基于音频转录创作故事梗概",
        "schema": StoryOutlineForLLMMode,
        "tags": ["video", "outline_generation", "audio_driven"],
        "model_config": {
            # 使用 gemini 生成故事梗概
            "role": "multimodal",
            "timeout": 180
        }
    },
    PromptName.VIDEO_OUTLINE_GENERATION_VIDEO_DRIVEN: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频驱动的故事梗概生成 - 基于用户需求和分析创作故事梗概",
        "schema": StoryOutlineForLLMMode,
        "tags": ["video", "outline_generation", "video_driven"],
        "model_config": {
            # 使用 gemini 生成故事梗概
            "role": "multimodal",
            "timeout": 180
        }
    },
    
    # ==================== Scene Generation Prompts ====================
    PromptName.VIDEO_SCENE_GENERATION_AUDIO_DRIVEN: {
        # file removed — craft in kit/skills; model_config only
        "description": "音频驱动的场景分镜生成 - 基于音频转录内容生成与音频同步的场景分镜",
        "schema": ScenesCollectionForLLM,
        "tags": ["video", "scene_generation", "audio_driven"],
        "model_config": {
            # 使用 gemini 生成场景分镜
            "role": "multimodal",
            "timeout": 240
        }
    },
    PromptName.VIDEO_SCENE_GENERATION_VIDEO_DRIVEN: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频驱动的场景分镜生成 - 基于故事大纲和角色信息生成场景分镜",
        "schema": ScenesCollectionForLLM,
        "tags": ["video", "scene_generation", "video_driven"],
        "model_config": {
            # 使用 gemini 生成场景分镜
            "role": "multimodal",
            "timeout": 240
        }
    },
    
    # ==================== Storyboard Detail Generation Prompts ====================
    PromptName.VIDEO_STORYBOARD_DETAIL_GENERATION: {
        # file removed — craft in kit/skills; model_config only
        "description": "详细分镜生成 - 将场景大纲细化为具体的分镜脚本",
        "schema": StoryboardDetailLLMOutput,
        "tags": ["video", "storyboard_detail_generation"],
        "model_config": {
            # 使用 gemini 生成详细分镜
            "role": "multimodal",
            "timeout": 240
        }
    },
    PromptName.VIDEO_STORYBOARD_FIRST_FRAME_REVISION: {
        # file removed — craft in kit/skills; model_config only
        "description": "分镜首帧合规修订 - 检查并修订违反 I2V 首帧约束的镜头描述",
        "schema": None,  # FirstFrameRevisionOutput 在代码中处理
        "tags": ["video", "storyboard", "first_frame_revision"],
        "model_config": {
            "role": "multimodal",
            "timeout": 120
        },
        "resilience": {
            "model_fallback_chain": [
                {"role": "multimodal", "timeout": 120},
                {"role": "tool", "temperature": 0.5, "timeout": 180},
            ],
            "same_route_extra_retries": 2,
        },
    },
    PromptName.VIDEO_PER_SHOT_GENERATION_ROUTING: {
        "file": "video/per_shot_generation_routing/video_per_shot_generation_routing.mustache",
        "description": "Per-shot 生成路由 - 内容是否适合口型、推荐图像/普通视频/口型工具（结构化输出 PerShotGenerationRoutingOutput）",
        "schema": PerShotGenerationRoutingOutput,
        "tags": ["video", "routing", "lipsync"],
        "model_config": {
            "role": "multimodal",
            "timeout": 180,
            "max_output_tokens": 8192,
        },
        "resilience": {
            "model_fallback_chain": [
                {"role": "multimodal", "timeout": 180, "max_output_tokens": 8192},
                {"role": "tool", "temperature": 0.5, "timeout": 180},
            ],
        },
    },
    
    # ==================== Visual Elements Matching Prompts ====================
    PromptName.VIDEO_VISUAL_ELEMENTS_MATCHING: {
        # file removed — craft in kit/skills; model_config only
        "description": "视觉元素匹配 - 从场景描述中识别和匹配visual elements（人物角色、重要物品、核心场所）",
        "schema": None,  # VisualElementsMatchingResult 在代码中处理
        "tags": ["video", "visual_elements", "matching"],
        "model_config": {
            # 使用 gemini 进行视觉元素匹配
            "role": "multimodal",
            "timeout": 180
        }
    },
    
    # ==================== Main Character Design Prompts ====================
    PromptName.VIDEO_MAIN_CHARACTER_GENERATION: {
        # file removed — craft in kit/skills; model_config only
        "description": "主要角色生成 - 基于故事大纲创造主要角色和重要配角",
        "schema": CharacterProfiles,
        "tags": ["video", "main_character_design", "generation"],
        "model_config": {
            # 使用 gemini 生成角色
            "role": "multimodal",
            "timeout": 180
        }
    },
    PromptName.VIDEO_MAIN_CHARACTER_MATCHING: {
        # file removed — craft in kit/skills; model_config only
        "description": "角色图片匹配 - 将角色设计与参考图片进行匹配",
        "schema": None,  # CharacterImageMatchingResult 在代码中处理
        "tags": ["video", "main_character_design", "matching"],
        "model_config": {
            # 使用 gemini 进行图片匹配
            "role": "multimodal",
            "timeout": 180
        }
    },
    PromptName.VIDEO_MAIN_CHARACTER_IMAGE_GENERATION: {
        # file removed — craft in kit/skills; model_config only
        "description": "角色图片生成 - 使用图像生成工具为角色生成设计图",
        "schema": CharacterImageGenerationResult,
        "tags": ["video", "main_character_design", "image_generation"],
        "model_config": {
            # 使用 gpt；Gemini 2.5+ 可与 ProviderStrategy 配合（见 tests/llm/test_provider_strategy_gemini_real.py），
            # 但需在终局 prompt 中禁止 ```json 围栏并强调必填字段，否则解析易失败
            "role": "tool",
            "temperature": 0.5,
            "timeout": 180
        }
    },
    PromptName.VIDEO_MAIN_CHARACTER_MULTI_VIEW_GENERATION: {
        # file removed — craft in kit/skills; model_config only
        "description": "多视角参考图生成 - 为视觉元素生成包含多个视角的单张参考图（Character Sheet）",
        "schema": ImageGenerationResult,
        "tags": ["video", "character_design", "multi_view"],
        "model_config": {
            # 使用 gpt；Gemini 2.5+ 可与 ProviderStrategy 配合（见 tests/llm/test_provider_strategy_gemini_real.py），
            # 但需在终局 prompt 中禁止 ```json 围栏并强调必填字段，否则解析易失败
            "role": "tool",
            "temperature": 0.5,
            "timeout": 180
        }
    },
    PromptName.VIDEO_SINGLE_CHARACTER_TOOL_CALL_PROMPT: {
        # file removed — craft in kit/skills; model_config only
        "description": "单角色工具调用提示词 - 自定义提示词重新生成角色图时使用（regenerate 场景）",
        "schema": CharacterImageGenerationResult,
        "tags": ["video", "main_character_design", "tool_call"],
        "model_config": {
            "role": "tool",
            "temperature": 0.5,
            "timeout": 180
        }
    },
    
    # ==================== Character Fusion Prompts ====================
    PromptName.VIDEO_CHARACTER_FUSION_IMAGE_GENERATION: {
        # file removed — craft in kit/skills; model_config only
        "description": "角色融合图生成 - 将多个角色的图片融合成一张组合参考图",
        "schema": ImageGenerationResult,
        "tags": ["video", "character_fusion", "image_generation"],
        "model_config": {
            # 使用 gpt；Gemini 2.5+ 可与 ProviderStrategy 配合（见 tests/llm/test_provider_strategy_gemini_real.py），
            # 但需在终局 prompt 中禁止 ```json 围栏并强调必填字段，否则解析易失败
            "role": "tool",
            "temperature": 0.5,
            "timeout": 180
        }
    },
    
    # ==================== Video Analysis Prompts ====================
    PromptName.VIDEO_REQUIREMENTS_ANALYSIS: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频需求分析 - 分析用户输入并提取结构化的视频制作需求",
        "schema": VideoAnalysisResult,
        "tags": ["video", "video_analysis", "requirements"],
        "model_config": {
            # Gemini 3: official google_search + FC (research folded into analysis)
            "model": "gemini-3.1-pro-preview",
            "timeout": 240,
        }
    },

    PromptName.VIDEO_CONTENT_CATEGORY_EARLY_INFERENCE: {
        # file removed — craft in kit/skills; model_config only
        "description": "user_input 阶段 content_category 早判 - input: user_input/panel_content_category",
        "schema": ContentCategoryEarlyOutput,
        "tags": ["video", "content_category"],
        "model_config": {
            "role": "text",
            "temperature": 0.2,
            "timeout": 60,
        },
    },
    
    PromptName.VIDEO_GEMINI_ANALYSIS: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频 Gemini 分析 - 使用 Gemini 分析视频内容、风格和制作技术",
        "schema": GeminiVideoAnalysisResult,
        "tags": ["video", "analysis", "gemini"],
        "model_config": {
            # 使用 gemini-2.5-flash 进行视频分析
            "role": "multimodal",
            "timeout": 180
        }
    },
    
    # ==================== Video Lipsync Prompts ====================
    PromptName.VIDEO_LIPSYNC_SUITABILITY_ANALYSIS: {
        "file": "video/lipsync/video_lipsync_suitability_analysis.mustache",
        "description": "视频配音适合性分析 - 判断视频是否适合进行配音处理",
        "schema": None,  # 运行时传递 VideoUnderstandingResult
        "tags": ["video", "lipsync", "suitability"],
        "model_config": {
            # 使用 gemini-2.5-flash 进行视频分析
            "role": "multimodal",
            "timeout": 180
        }
    },
    
    # ==================== Agent Router Prompts ====================
    PromptName.AGENT_ROUTER_ANALYSIS: {
        "file": "agent_router/agent_router_analysis.mustache",
        "description": "Agent 路由分析 - 分析用户输入并决定使用哪个 Agent",
        "schema": None,  # RouterAnalysisResult 在代码中处理
        "tags": ["agent_router", "analysis"],
        "model_config": {
            # 使用 gpt-4.1-mini 进行快速路由分析
            "role": "text",
            "temperature": 0.1,
            "timeout": 180
        }
    },
    
    # ==================== Story Generation Prompts ====================
    PromptName.STORY_DIRECT_GENERATION: {
        "file": "story/story_direct_generation.mustache",
        "description": "故事直接生成 - LLM 直接创作故事（支持流式输出）",
        "schema": None,  # 直接输出文本，不需要结构化
        "tags": ["story", "direct", "streaming"],
        "model_config": {
            # 使用 gpt-4.1-mini 直接生成故事（支持流式输出）
            "role": "text",
            "temperature": 0.7,
            "timeout": 180
        }
    },
    
    # ==================== Agent Router Prompts ====================
    PromptName.AGENT_ROUTER_CLARIFY: {
        "file": "agent_router/agent_router_clarify.mustache",
        "description": "Agent Router 澄清 - 当用户意图不明确时生成友好的澄清消息（流式纯文本）",
        "schema": None,  # 流式输出，不使用结构化输出
        "tags": ["agent_router", "clarify", "streaming"],
        "model_config": {
            # 使用 gpt-4.1-mini 进行快速澄清响应生成
            "role": "text",
            "temperature": 0.7,
            "timeout": 180
        }
    },
    PromptName.AGENT_ROUTER_USER_OPTION_MERGE: {
        "file": "agent_router/agent_router_user_option_merge.mustache",
        "description": "Agent Router 用户选项合并 - 根据用户输入智能覆盖用户选项",
        "schema": UserOption,
        "tags": ["agent_router", "user_option"],
        "model_config": {
            # 使用 gpt-4.1-mini 进行快速用户选项合并（与其他 agent router prompts 保持一致）
            "role": "text",
            "temperature": 0.3,  # 较低温度确保准确性
            "timeout": 180
        }
    },
    
    # ==================== Music Generation Prompts ====================
    PromptName.MUSIC_AGENT_TOOL_EXECUTION: {
        "file": "music/music_agent_tool_execution.mustache",
        "description": "音乐生成 Agent 工具执行 - 指导 LLM 调用音乐生成工具创作音乐（支持流式输出）",
        "schema": None,  # 使用 create_agent，不需要 structured output
        "tags": ["music", "agent", "tool_execution"],
        "model_config": {
            # 使用 gpt-4.1-mini 作为 agent
            "role": "tool",
            "temperature": 0.7,
            "timeout": 180
        }
    },
    
    # ==================== Image Generation Prompts ====================
    PromptName.IMAGE_AGENT_TOOL_EXECUTION: {
        "file": "image/image_agent_tool_execution.mustache",
        "description": "图像生成 Agent 工具执行 - 指导 LLM 调用图像生成工具创建图像（支持流式输出）",
        "schema": None,  # 使用 create_agent，不需要 structured output
        "tags": ["image", "agent", "tool_execution"],
        "model_config": {
            # 使用 gpt-4.1-mini 作为 agent
            "role": "tool",
            "temperature": 0.7,
            "timeout": 180
        }
    },

    # ==================== Video Gen (单模型直生) Prompts ====================
    PromptName.VIDEO_AGENT_TOOL_EXECUTION: {
        "file": "video_gen/video_agent_tool_execution.mustache",
        "description": "视频直生 Agent 工具执行 - 指导 LLM 调用视频生成工具（T2V/I2V）直接出片，支持流式 + 多视频",
        "schema": None,  # 使用 create_agent，不需要 structured output
        "tags": ["video_gen", "agent", "tool_execution"],
        "model_config": {
            # 使用 gpt-4.1-mini 作为 agent（与 image agent 一致）
            "role": "tool",
            "temperature": 0.7,
            "timeout": 180
        }
    },
    
    # ==================== Music Generation Prompts ====================
    PromptName.VIDEO_AUDIO_TRANSCRIPTION: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频音频转录分析 - 使用 Gemini 对音频进行转录和视频创作分析",
        "schema": GeminiTranscriptionResult,
        "tags": ["video", "music_generation", "transcription"],
        "model_config": {
            # 使用 gemini-2.5-flash 进行音频转录
            "role": "multimodal",
            "timeout": 240
        }
    },
    
    PromptName.VIDEO_MUSIC_INTENT_ANALYSIS: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频音乐意图分析 - 三选一：lyrics_provided | auto_lyrics_song | instrumental_bgm",
        "schema": None,  # MusicIntentAnalysis，运行时传递
        "tags": ["video", "music_generation", "intent"],
        "model_config": {
            # 使用 gpt-4.1-mini 进行快速意图分析
            "role": "text",
            "temperature": 0.3,
            "timeout": 180
        }
    },
    
    PromptName.VIDEO_MUSIC_GENERATION: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频音乐生成（歌词模式）- 使用 Suno API 生成带歌词的歌曲（create_agent with tools）",
        "schema": MusicGenerationResult,
        "tags": ["video", "music_generation", "generation", "lyrics"],
        "model_config": {
            # 使用 gpt-4.1-mini 进行音乐工具执行（与 keyframe tool execution 保持一致）
            "role": "tool",
            "temperature": 0.5,
            "timeout": 180
        }
    },
    
    PromptName.VIDEO_MUSIC_BGM_GENERATION: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频音乐生成（BGM模式）- 使用 Suno API 生成纯器乐背景音乐（create_agent with tools）",
        "schema": MusicGenerationResult,
        "tags": ["video", "music_generation", "generation", "bgm", "instrumental"],
        "model_config": {
            # 使用 gpt-4.1-mini 进行音乐工具执行（与 keyframe tool execution 保持一致）
            "role": "tool",
            "temperature": 0.5,
            "timeout": 180
        }
    },
    PromptName.VIDEO_MUSIC_SMART_CLIP_ANALYSIS: {
        # file removed — craft in kit/skills; model_config only
        "description": "视频音乐智能剪辑分析 - 复用转录的 sections/segments，输出候选裁切点 + 推荐选区",
        # schema 在 music_smart_clip_service 内定义并通过 structured_schema= 显式传入，避免循环依赖
        "schema": None,
        "tags": ["video", "music_generation", "smart_clip"],
        "model_config": {
            "role": "multimodal",
            "temperature": 0.2,
            "timeout": 120,
        },
    },

    # ==================== Subtitle Prompts ====================
    PromptName.VIDEO_SUBTITLE_LINE_GROUPING: {
        # file removed — craft in kit/skills; model_config only
        "description": "字幕行分组 - 将词汇按语义和语法规则分组成字幕行",
        "schema": SubtitleGroupsResponse,
        "tags": ["video", "subtitle", "grouping"],
        "model_config": {
            "role": "multimodal",
            "timeout": 120
        }
    },

    # ==================== Utils Prompts ====================
    PromptName.VIDEO_COMPLETION_MESSAGE: {
        "file": "video/utils/generate_completion_message.mustache",
        "description": "任务完成消息生成 - 基于对话历史生成用户友好的完成消息（支持流式输出）",
        "schema": None,  # 直接输出文本，不需要结构化
        "tags": ["video", "utils", "completion", "streaming"],
        "model_config": {
            # 使用 gpt-4.1-mini 生成简洁的完成消息
            "role": "text",
            "temperature": 0.7,
            "timeout": 180
        }
    },

    # ==================== Audio Effect Generation ====================
    PromptName.VIDEO_AUDIO_EFFECT_GENERATION: {
        # file removed — craft in kit/skills; model_config only
        "description": "音效生成 - 为视频片段生成音效，input: shot_number/bridge_indicator/is_bridge/duration/video_url/motion_prompt/prompt",
        "schema": AudioGenerationResult,
        "tags": ["video", "audio_effect"],
        "model_config": {
            "role": "tool",
            "temperature": 0.5,
            "timeout": 180,
            "stream_usage": True
        }
    },
    PromptName.VIDEO_NARRATION_GENERATION: {
        # file removed — craft in kit/skills; model_config only
        "description": "旁白生成 - 为镜头生成旁白语音，input: shot_number/bridge_indicator/shot_type/duration/scene_description/lighting/visual_effects/narration/speaker_context/recommended_voice_id",
        "schema": SpeechGenerationResult,
        "tags": ["video", "narration"],
        "model_config": {
            "role": "tool",
            "temperature": 0.5,
            "timeout": 180,
            "stream_usage": True
        }
    },

    PromptName.VIDEO_SINGLE_KEYFRAME_REFLECTION_PROMPT: {
        # file removed — craft in kit/skills; model_config only
        "description": "单关键帧反思分析提示词 - input: shot_number/shot_uuid/character_names/character_info/scene_description",
        "schema": KeyframeReflectionVlmStructuredOutput,
        "tags": ["video", "keyframe_reflection"],
        "model_config": {
            "role": "multimodal",
            "timeout": 240,
            "temperature": 0.3
        }
    },
    PromptName.VIDEO_INSTRUCTION_MERGE_TO_FULL_PROMPT: {
        # file removed — craft in kit/skills; model_config only
        "description": "将用户自然语言修改说明融合进当前完整提示词（关键帧 t2i / 角色 t2i / 镜头 motion），仅文本、不调用生图",
        "tags": ["video", "regenerate", "prompt_edit"],
        "model_config": {
            "role": "text",
            "temperature": 0.4,
            "timeout": 120,
            "max_output_tokens": 8192,
        },
    },
    PromptName.VIDEO_ARTIFACT_SUGGEST_PRESETS_CHARACTER: {
        # file removed — craft in kit/skills; model_config only
        "description": "融合编辑弹窗 — 角色主图 — 建议指令芯片（可选参考图）",
        "schema": PromptEditPresetsOutput,
        "tags": ["video", "regenerate", "prompt_edit", "multimodal"],
        "model_config": {
            "role": "multimodal",
            "temperature": 0.55,
            "max_output_tokens": 2048,
            "timeout": 120,
        },
        "resilience": {
            "model_fallback_chain": [
                {"role": "tool", "temperature": 0.55, "max_output_tokens": 2048, "timeout": 120},
            ],
            "same_route_extra_retries": 1,
        },
    },
    PromptName.VIDEO_ARTIFACT_SUGGEST_PRESETS_KEYFRAME: {
        # file removed — craft in kit/skills; model_config only
        "description": "融合编辑弹窗 — 关键帧 — 建议指令芯片（可选关键帧图）",
        "schema": PromptEditPresetsOutput,
        "tags": ["video", "regenerate", "prompt_edit", "multimodal"],
        "model_config": {
            "role": "multimodal",
            "temperature": 0.55,
            "max_output_tokens": 2048,
            "timeout": 120,
        },
        "resilience": {
            "model_fallback_chain": [
                {"role": "tool", "temperature": 0.55, "max_output_tokens": 2048, "timeout": 120},
            ],
            "same_route_extra_retries": 1,
        },
    },
    PromptName.VIDEO_ARTIFACT_SUGGEST_PRESETS_VIDEO: {
        # file removed — craft in kit/skills; model_config only
        "description": "融合编辑弹窗 — 镜头视频 — 建议指令芯片（关键帧图 + 可选成片片段）",
        "schema": PromptEditPresetsOutput,
        "tags": ["video", "regenerate", "prompt_edit", "multimodal"],
        "model_config": {
            "role": "multimodal",
            "temperature": 0.55,
            "max_output_tokens": 2048,
            "timeout": 180,
        },
        "resilience": {
            "model_fallback_chain": [
                {"role": "tool", "temperature": 0.55, "max_output_tokens": 2048, "timeout": 180},
            ],
            "same_route_extra_retries": 1,
        },
    },

    PromptName.IMAGE_CHARACTER_CONSISTENCY_CHECK: {
        # file removed — craft in kit/skills; model_config only
        "description": "I2I 角色一致性校验 - 生成提示词是否引用角色(人/动物/主角等)，再判断生成图与参考图该角色是否一致(has_character/is_consistent/reason)",
        "schema": CharacterConsistencyResult,
        "tags": ["image", "keyframe", "consistency"],
        "model_config": {
            "role": "multimodal",
            "timeout": 120,
            "temperature": 1.0,
            "max_output_tokens": 8192
        }
    },
    PromptName.VIDEO_CONSISTENCY_CHECK: {
        # file removed — craft in kit/skills; model_config only
        "description": "I2V 视频一致性校验 - 首帧与生成视频一致，且不出现首帧中不存在/不可推断的内容；输出 passed/failure_reason_type/suggested_prompt",
        "schema": VideoConsistencyCheckResult,
        "tags": ["video", "consistency", "first_frame"],
        "model_config": {
            "role": "multimodal",
            "timeout": 120,
            "temperature": 1.0,
            "max_output_tokens": 8192
        }
    },
    PromptName.VISION_ANALYZE: {
        "description": "Vision Analyze - 图片/视频理解，analyze_image / analyze_video tool 使用",
        "tags": ["vision", "multimodal", "tool"],
        "model_config": {
            "role": "multimodal",
            "temperature": 0,
            "timeout": 120,
        }
    },
    PromptName.VIDEO_EDIT_AGENT: {
        "description": "Video Edit Agent (Companion Agent) - MCP 暴露给 ChatAgent，内部编排子 tool",
        "tags": ["video", "edit", "companion", "mcp"],
        "model_config": {
            # role=tool → LLM_QUALITY 映射（best=gpt-5.6-terra）。推理模型不接受 temperature。
            "role": "tool",
            "timeout": 300,
            "streaming": False
        }
    },
}

