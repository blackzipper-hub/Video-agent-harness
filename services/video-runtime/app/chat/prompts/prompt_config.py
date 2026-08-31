"""
Prompt 配置（Cuti-VideoChatAgent 精简版）

与 Cuti-VideoAgent 的 ``prompts/prompt_config.py`` 对齐方式：仅收录 Agent Router 相关条目及 ``create_llm``，
便于本仓库独立加载 Mustache、``llm_resilience``、多模态 pipeline。若需与 VideoAgent 全量 PROMPTS_CONFIG 一致，
请从 VideoAgent 同步该文件并合并条目（见 ``docs/MIGRATION_FROM_VIDEO_AGENT.md``）。
"""
from enum import Enum
from typing import Any, Dict

from langchain_core.runnables import Runnable
from app.llm.openai_failover import FailoverChatOpenAI as ChatOpenAI

from app.chat.models.user_options import UserOption


class PromptName(str, Enum):
    """本仓库当前仅迁移 Agent Router 相关 prompt 名（与 VideoAgent 字符串值一致）。"""

    AGENT_ROUTER_ANALYSIS = "agent_router_analysis"
    AGENT_ROUTER_MULTIMODAL_SUMMARY = "agent_router_multimodal_summary"
    AGENT_ROUTER_CHAT = "agent_router_chat"
    AGENT_ROUTER_CLARIFY = "agent_router_clarify"
    AGENT_ROUTER_CONFIRMATION_CHECK = "agent_router_confirmation_check"
    AGENT_ROUTER_ACTION_SUGGESTIONS = "agent_router_action_suggestions"
    AGENT_ROUTER_DIRECT_GENERATE_BRIEF = "agent_router_direct_generate_brief"
    AGENT_ROUTER_USER_OPTION_MERGE = "agent_router_user_option_merge"
    AGENT_ROUTER_SHIELD_LLM_SCREEN = "agent_router_shield_llm_screen"
    AGENT_ROUTER_SHIELD_REFUSAL = "agent_router_shield_refusal"
    VIDEO_EDIT = "video_edit"


try:
    from langchain_google_genai import ChatGoogleGenerativeAI, HarmBlockThreshold, HarmCategory

    HAS_GOOGLE = True
    GOOGLE_SAFETY_SETTINGS = {
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    }
except ImportError:
    HAS_GOOGLE = False
    GOOGLE_SAFETY_SETTINGS = {}


_OPENAI_REASONING_MODELS = frozenset({
    "gpt-5-nano",
    "gpt-5-mini",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
})

# 与 video prompts.create_llm 对齐：GPT-5.6 默认 Responses API
_OPENAI_GPT_56_MODELS = frozenset({
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
})


def create_llm(model_config: Dict[str, Any]) -> Runnable:
    """根据 ``model_config`` 创建 LLM（与 Cuti-VideoAgent 行为一致；支持 role + LLM_QUALITY）。"""
    from app.chat.models.tool_enums import LLMModel
    from prompts.llm_model_profiles import resolve_model_config

    model_config = resolve_model_config(model_config)
    model_name_raw = model_config.get("model", LLMModel.GPT_4_1_MINI.value)
    if isinstance(model_name_raw, LLMModel):
        llm_model = model_name_raw
    else:
        try:
            llm_model = LLMModel(model_name_raw)
        except ValueError as e:
            raise ValueError(
                f"不支持的模型名称: {model_name_raw}。支持的模型: {[m.value for m in LLMModel]}"
            ) from e

    model_name = llm_model.value
    temperature = model_config.get("temperature")
    max_tokens = model_config.get("max_tokens")
    timeout = model_config.get("timeout", 180)
    streaming = model_config.get("streaming")
    stream_usage = model_config.get("stream_usage", True)

    _openai = {
        LLMModel.GPT_4_1_MINI,
        LLMModel.GPT_5_NANO,
        LLMModel.GPT_5_MINI,
        LLMModel.GPT_5_4_MINI,
        LLMModel.GPT_5_4_NANO,
        LLMModel.GPT_5_6_SOL,
        LLMModel.GPT_5_6_TERRA,
        LLMModel.GPT_5_6_LUNA,
    }
    if llm_model in _openai:
        openai_kwargs: Dict[str, Any] = dict(
            model=model_name,
            max_tokens=max_tokens,
            timeout=timeout,
            stream_usage=stream_usage,
        )
        if streaming is not None:
            openai_kwargs["streaming"] = streaming
        if temperature is not None and model_name not in _OPENAI_REASONING_MODELS:
            openai_kwargs["temperature"] = temperature
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
    if llm_model in (
        LLMModel.GEMINI_2_5_FLASH,
        LLMModel.GEMINI_3_FLASH_PREVIEW,
        LLMModel.GEMINI_3_PRO_PREVIEW,
        LLMModel.GEMINI_3_1_PRO_PREVIEW,
        LLMModel.GEMINI_2_0_FLASH,
    ):
        if not HAS_GOOGLE:
            raise ImportError(f"需要安装 langchain-google-genai 才能使用 {model_name}")
        max_output_tokens = model_config.get("max_output_tokens") or max_tokens
        gemini_kwargs: Dict[str, Any] = {
            "model": model_name,
            "timeout": timeout,
            "safety_settings": GOOGLE_SAFETY_SETTINGS,
        }
        if max_output_tokens:
            gemini_kwargs["max_output_tokens"] = max_output_tokens
        if temperature is not None:
            gemini_kwargs["temperature"] = temperature
        if streaming is not None:
            gemini_kwargs["streaming"] = streaming
        return ChatGoogleGenerativeAI(**gemini_kwargs)
    raise ValueError(f"未处理的模型类型: {llm_model}")


PROMPTS_CONFIG: Dict[PromptName, Dict[str, Any]] = {
    PromptName.AGENT_ROUTER_ANALYSIS: {
        "file": "agent_router/agent_router_analysis.mustache",
        "description": "Agent 路由分析 - 分析用户输入并决定使用哪个 Agent",
        "schema": None,
        "tags": ["agent_router", "analysis"],
        "model_config": {
            "role": "multimodal",
            "temperature": 0.1,
            "timeout": 180,
        },
    },
    PromptName.AGENT_ROUTER_MULTIMODAL_SUMMARY: {
        "file": "agent_router/agent_router_multimodal_summary.mustache",
        "description": "Agent Router 多模态摘要 - 将图片音频视频提炼为可供对话使用的文字摘要",
        "schema": None,
        "tags": ["agent_router", "multimodal", "summary"],
        "model_config": {
            "role": "multimodal",
            "temperature": 0.2,
            "timeout": 180,
        },
    },
    PromptName.AGENT_ROUTER_CLARIFY: {
        "file": "agent_router/agent_router_clarify.mustache",
        "description": "Agent Router 澄清 - 当用户意图不明确时生成友好的澄清消息",
        "schema": None,
        "tags": ["agent_router", "clarify", "streaming"],
        "model_config": {
            "role": "text",
            "temperature": 0.7,
            "timeout": 180,
            "streaming": True,
        },
    },
    PromptName.AGENT_ROUTER_CHAT: {
        "file": "agent_router/agent_router_chat.mustache",
        "description": "Agent Router 需求确认对话 - 围绕创作任务补齐风格、内容、约束",
        "schema": None,
        "tags": ["agent_router", "chat", "streaming"],
        "model_config": {
            "role": "text",
            "temperature": 0.8,
            "timeout": 180,
            "streaming": True,
        },
    },
    PromptName.AGENT_ROUTER_CONFIRMATION_CHECK: {
        "file": "agent_router/agent_router_confirmation_check.mustache",
        "description": "Agent Router 需求确认检查 - 判断是否已具备生成条件，并输出确认摘要",
        "schema": None,
        "tags": ["agent_router", "confirmation", "structured"],
        "model_config": {
            "role": "text",
            "temperature": 0.1,
            "timeout": 180,
        },
    },
    PromptName.AGENT_ROUTER_ACTION_SUGGESTIONS: {
        "file": "agent_router/agent_router_action_suggestions.mustache",
        "description": "Agent Router 推荐动作：JSON reply_type + label/message，动态条数，不含直接生成",
        "schema": None,
        "tags": ["agent_router", "action_suggestions", "structured"],
        "model_config": {
            "role": "text",
            "temperature": 0.6,
            "timeout": 180,
        },
    },
    PromptName.AGENT_ROUTER_DIRECT_GENERATE_BRIEF: {
        "file": "agent_router/agent_router_direct_generate_brief.mustache",
        "description": "直接生成：无现成 summary_input 时，根据对话节选生成一条可执行的创作任务说明",
        "schema": None,
        "tags": ["agent_router", "direct_generate", "summary"],
        "model_config": {
            "role": "text",
            "temperature": 0.2,
            "timeout": 120,
        },
    },
    PromptName.AGENT_ROUTER_USER_OPTION_MERGE: {
        "file": "agent_router/agent_router_user_option_merge.mustache",
        "description": "Agent Router 用户选项合并 - 根据用户输入智能覆盖用户选项",
        "schema": UserOption,
        "tags": ["agent_router", "user_option"],
        "model_config": {
            "role": "text",
            "temperature": 0.3,
            "timeout": 180,
        },
    },
    PromptName.AGENT_ROUTER_SHIELD_LLM_SCREEN: {
        "file": "agent_router/agent_router_shield_llm_screen.mustache",
        "description": "Input Rail LLM 元查询 / 套取信息分类（结构化输出）",
        "schema": None,
        "tags": ["agent_router", "shield", "classifier"],
        "model_config": {
            "role": "text",
            "temperature": 0,
            "timeout": 1.5,
        },
    },
    PromptName.AGENT_ROUTER_SHIELD_REFUSAL: {
        "file": "agent_router/agent_router_shield_refusal.mustache",
        "description": "Shield 拦截后的自然语言拒答（不落库，仅流式推送）",
        "schema": None,
        "tags": ["agent_router", "shield", "refusal"],
        "model_config": {
            "role": "text",
            "temperature": 0.6,
            "timeout": 25,
        },
    },
    PromptName.VIDEO_EDIT: {
        "file": None,
        "description": "Video Edit Agent - 通过 MCP tools 查看/调整视频生成任务",
        "schema": None,
        "tags": ["video_edit", "mcp", "streaming"],
        "model_config": {
            "role": "tool",
            "timeout": 300,
            "streaming": True,
        },
    },
}
