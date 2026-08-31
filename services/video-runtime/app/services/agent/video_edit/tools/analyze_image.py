"""AnalyzeImageTool — 通用图片理解：传入图片 URL + 问题，调用 vision model 返回分析结果。"""
import logging
from typing import Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AnalyzeImageInput(BaseModel):
    """analyze_image 的输入参数。"""
    image_url: str = Field(
        ..., description="要分析的图片 URL（http/https）",
    )
    question: str = Field(
        "请详细描述这张图片的内容。",
        description="针对图片的问题，如 '图片里有几个人？' '背景是什么？'",
    )


class AnalyzeImageTool(BaseTool):
    name: str = "analyze_image"
    description: str = (
        "分析一张图片的内容。传入图片 URL 和问题，返回 vision model 的回答。"
        "适合用于确认角色形象、关键帧画面内容、检查画面元素数量等。"
        "图片 URL 可通过 get_artifact_detail 获取（角色的 character_image_url、关键帧的 keyframe_url）。"
    )
    args_schema: Type[BaseModel] = AnalyzeImageInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(self, image_url: str, question: str = "请详细描述这张图片的内容。") -> str:
        from prompts.prompt_config import PromptName, PROMPTS_CONFIG, create_llm
        from langchain_core.messages import HumanMessage

        _mc = PROMPTS_CONFIG[PromptName.VISION_ANALYZE].get("model_config", {})
        llm = create_llm(_mc)

        msg = HumanMessage(
            content=[
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
            ]
        )

        try:
            resp = await llm.ainvoke([msg])
            return resp.content if resp.content else "(vision model 未返回内容)"
        except Exception as e:
            logger.error(f"analyze_image failed: {e}", exc_info=True)
            return f"图片分析失败: {e}"

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")
