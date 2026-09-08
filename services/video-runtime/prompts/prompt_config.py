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
    VIDEO_AUDIO_TRANSCRIPTION = "video_audio_transcription"
    VIDEO_CONSISTENCY_CHECK = "video_consistency_check"
    IMAGE_CHARACTER_CONSISTENCY_CHECK = "image_character_consistency_check"
    VIDEO_MUSIC_SMART_CLIP_ANALYSIS = "video_music_smart_clip_analysis"


sys.path.append(str(Path(__file__).parent.parent))
from app.schemas.video_llm import CharacterConsistencyResult, VideoConsistencyCheckResult
from app.tools.transcribe.gemini import GeminiTranscriptionResult


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


PROMPTS_CONFIG: Dict[PromptName, Dict[str, Any]] = {
    PromptName.VIDEO_AUDIO_TRANSCRIPTION: {
        "description": "视频音频转录分析 - 使用 Gemini 对音频进行转录和视频创作分析",
        "schema": GeminiTranscriptionResult,
        "tags": ["video", "music_generation", "transcription"],
        "model_config": {
            "role": "multimodal",
            "timeout": 240,
        },
    },
    PromptName.VIDEO_CONSISTENCY_CHECK: {
        "description": "I2V 视频一致性校验 - 首帧与生成视频一致，且不出现首帧中不存在/不可推断的内容",
        "schema": VideoConsistencyCheckResult,
        "tags": ["video", "consistency", "first_frame"],
        "model_config": {
            "role": "multimodal",
            "timeout": 120,
            "temperature": 1.0,
            "max_output_tokens": 8192,
        },
    },
    PromptName.IMAGE_CHARACTER_CONSISTENCY_CHECK: {
        "description": "I2I 角色一致性校验 - 生成图与参考角色一致",
        "schema": CharacterConsistencyResult,
        "tags": ["image", "consistency"],
        "model_config": {
            "role": "multimodal",
            "timeout": 120,
            "temperature": 1.0,
            "max_output_tokens": 8192,
        },
    },
    PromptName.VIDEO_MUSIC_SMART_CLIP_ANALYSIS: {
        "description": "根据转录推荐目标时长的智能裁切窗口",
        # schema 在 music_smart_clip_service 里，调用时 structured_schema= 传入，避免循环 import
        "schema": None,
        "tags": ["music", "smart_clip"],
        "model_config": {
            "role": "multimodal",
            "temperature": 0.2,
            "timeout": 120,
        },
    },
}
