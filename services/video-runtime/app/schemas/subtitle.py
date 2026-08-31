"""字幕相关 Pydantic 模型（LLM 结构化输出）"""
from typing import List
from pydantic import BaseModel, Field


class SubtitleLineGroup(BaseModel):
    """字幕行分组模型 - LLM 只负责分组，不计算时间"""
    text: str = Field(description="字幕文本内容")
    word_ids: List[int] = Field(description="包含的词汇 ID 列表")


class SubtitleGroupsResponse(BaseModel):
    """LLM 字幕分组响应模型"""
    subtitle_groups: List[SubtitleLineGroup] = Field(description="字幕分组列表")
