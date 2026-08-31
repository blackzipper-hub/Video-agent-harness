"""AnalyzeVideoTool — 通用视频理解：传入视频 URL + 问题，调用 vision model 返回分析结果。

使用 Gemini 模型（原生支持视频输入），通过 LangChain 的 HumanMessage 传入视频 URL。
"""
import logging
from typing import Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AnalyzeVideoInput(BaseModel):
    """analyze_video 的输入参数。"""
    video_url: str = Field(
        ..., description="要分析的视频 URL（http/https）",
    )
    question: str = Field(
        "请详细描述这段视频的内容。",
        description="针对视频的问题，如 '视频里有几个人？' '动作是什么？' '画面质量如何？'",
    )


class AnalyzeVideoTool(BaseTool):
    name: str = "analyze_video"
    description: str = (
        "分析一段视频的内容。传入视频 URL 和问题，返回 vision model 的回答。"
        "适合用于确认视频片段的画面、检查动作连贯性、评估画面质量等。"
        "视频 URL 可通过 get_artifact_detail 获取。"
    )
    args_schema: Type[BaseModel] = AnalyzeVideoInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(self, video_url: str, question: str = "请详细描述这段视频的内容。") -> str:
        from prompts.llm_model_profiles import resolve_model_config
        from prompts.prompt_config import PromptName, PROMPTS_CONFIG, create_llm
        from langchain_core.messages import HumanMessage

        _mc = resolve_model_config(
            PROMPTS_CONFIG[PromptName.VISION_ANALYZE].get("model_config", {}) or {}
        )

        video_mc = {**_mc}
        if "gemini" not in str(video_mc.get("model", "")).lower():
            video_mc["model"] = "gemini-2.5-flash"

        llm = create_llm(video_mc)

        msg = HumanMessage(
            content=[
                {"type": "text", "text": question},
                {
                    "type": "media",
                    "mime_type": "video/mp4",
                    "url": video_url,
                },
            ]
        )

        try:
            resp = await llm.ainvoke([msg])
            return resp.content if resp.content else "(vision model 未返回内容)"
        except Exception as e:
            logger.error(f"analyze_video failed: {e}", exc_info=True)
            return f"视频分析失败: {e}"

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")
