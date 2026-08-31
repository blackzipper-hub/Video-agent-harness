"""instruction + 当前完整提示词 → 融合后的新提示词（仅 LLM 文本，不生图）。"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from prompts.prompt_config import PROMPTS_CONFIG, PromptName
from prompts.prompt_loader import create_llm_from_model_config
from app.orchestration.skills.prompt_context import (
    facts_human_message,
    skill_system_message,
)

logger = logging.getLogger(__name__)


async def instruction_merge_to_full_prompt(
    *,
    base_prompt: str,
    instruction: str,
    asset_kind: str,
    detected_language: Optional[str] = None,
) -> str:
    """调用配置的 LLM，将 instruction 融入 base_prompt，返回单段正文。"""
    base = (base_prompt or "").strip()
    ins = (instruction or "").strip()
    if not ins:
        raise ValueError("instruction 不能为空")
    if not base:
        raise ValueError("base_prompt 不能为空")

    system = skill_system_message(
        "instruction-merge-director",
        lead="Follow instruction-merge-director.",
    )
    facts = {
        "asset_kind": asset_kind,
        "detected_language": (detected_language or "zh").strip(),
        "base_prompt": base,
        "instruction": ins,
    }
    messages = [
        SystemMessage(content=system),
        HumanMessage(content=facts_human_message(facts)),
    ]
    llm = create_llm_from_model_config(
        PROMPTS_CONFIG[PromptName.VIDEO_INSTRUCTION_MERGE_TO_FULL_PROMPT]["model_config"]
    )
    out = await llm.ainvoke(messages)
    text = ""
    if isinstance(out, AIMessage):
        raw = out.content
        text = raw if isinstance(raw, str) else str(raw or "")
    elif hasattr(out, "content"):
        c = getattr(out, "content", "")
        text = c if isinstance(c, str) else str(c or "")
    text = (text or "").strip()
    if not text:
        logger.warning("instruction_merge_to_full_prompt: empty LLM output, fallback to base+instruction append")
        return f"{base}\n\n（按修改说明调整）{ins}"
    return text
