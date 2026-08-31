"""
模型服务模块
提供统一的模型管理和初始化功能
"""

import logging
from typing import Optional
from app.llm.openai_failover import FailoverChatOpenAI as ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.language_models import BaseLanguageModel

from app.config import get_settings
from app.models.video_state import StoryOutline

logger = logging.getLogger(__name__)


class ModelService:
    """模型服务类，管理所有LLM模型的初始化"""
    
    def __init__(self):
        self.settings = get_settings()
        self._llm: Optional[BaseLanguageModel] = None
        self._math_llm: Optional[BaseLanguageModel] = None
        self._outline_model: Optional[BaseLanguageModel] = None
        self._gemini_llm: Optional[BaseLanguageModel] = None
        self._gemini_llm_3: Optional[BaseLanguageModel] = None
    
    @property
    def llm(self) -> BaseLanguageModel:
        """获取基础LLM模型"""
        if self._llm is None:
            self._llm = ChatOpenAI(
                model="gpt-4.1-mini",
                timeout=180,
                stream_usage=True
            )
            logger.info("✅ 基础LLM模型初始化完成")
        return self._llm
    
    @property
    def math_llm(self) -> BaseLanguageModel:
        """获取数学能力更强的LLM模型（用于需要精确计算的场景，如场景生成、故事梗概生成）"""
        if self._math_llm is None:
            # 使用 GPT-5-nano，数学能力更强，适合需要精确时长计算的场景
            self._math_llm = ChatOpenAI(
                model="gpt-5-nano",
                timeout=180,
                stream_usage=True
            )
            logger.info("✅ 数学能力LLM模型初始化完成（使用gpt-5-nano）")
        return self._math_llm
    
    @property
    def gemini_llm(self) -> BaseLanguageModel:
        """获取 Google Gemini 模型（用于视频理解等多模态任务）"""
        if self._gemini_llm is None:
            self._gemini_llm = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                timeout=240
            )
            logger.info("✅ Google Gemini 模型初始化完成")
        return self._gemini_llm
    

    @property
    def gemini_llm_3(self) -> BaseLanguageModel:
        """获取 Google Gemini 3.1 Pro（多模态旗舰；原 gemini-3-pro-preview 已 404 下线）"""
        if getattr(self, "_gemini_llm_3", None) is None:
            self._gemini_llm_3 = ChatGoogleGenerativeAI(
                model="gemini-3.1-pro-preview",
                timeout=180
            )
            logger.info("✅ Google Gemini 3.1 Pro 模型初始化完成")
        return self._gemini_llm_3


# 单例模式
_model_service = None

def get_model_service() -> ModelService:
    """获取模型服务单例实例"""
    global _model_service
    if _model_service is None:
        _model_service = ModelService()
    return _model_service
