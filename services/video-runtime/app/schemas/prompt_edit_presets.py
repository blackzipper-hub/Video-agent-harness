"""融合编辑弹窗：AI 建议指令芯片的结构化输出（与 PROMPTS_CONFIG / API 共用）。"""
from typing import List

from pydantic import BaseModel, Field


class PromptEditPresetItem(BaseModel):
    """单条 AI 建议指令（芯片）"""
    label: str = Field(..., max_length=64, description="芯片短标题，须与请求语言一致")
    emoji: str = Field(default="✨", max_length=16)
    instruction: str = Field(
        ...,
        max_length=512,
        description="写入「编辑说明」的自然语言，简短可执行；须与请求语言一致",
    )


class PromptEditPresetsOutput(BaseModel):
    """建议指令列表"""
    presets: List[PromptEditPresetItem] = Field(default_factory=list)
