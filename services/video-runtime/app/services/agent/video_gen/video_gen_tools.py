"""
video_gen 的工具装配。

- 纯文本（无上传图）→ T2V：把对应 SD2 T2V leaf 工具包装成一个返回
  `content_and_artifact` 的 agent 工具（artifact = VideoGenerationResult），
  React Agent 多次调用即生成多个视频，service 端从 ToolMessage.artifact 收集成 videos[]。
- 有上传图 → I2V：复用现有 `ToolService.get_video_generation_tools` 的 wrapper 工具。

仅支持 SD2 全家桶（4 个变体）。i2v 枚举 → t2v leaf 的映射在 `I2V_TO_T2V`。
"""
import logging
from typing import Annotated, Any, List, Optional

from pydantic import BaseModel, Field, SkipValidation
from pydantic.json_schema import SkipJsonSchema
from langchain_core.tools import tool, BaseTool
from langchain.tools import ToolRuntime

from ....models.image_result import VideoGenerationResult
from ....models.tool_enums import ToolType, ToolCategory, ToolProvider
from ....models.user_options import UserOption, VideoGenerationTool
from ....tools.context_schemas import VideoGenerationContext
from ....tools.video.seedance_2_t2v import generate_video_with_wavespeed_seedance_2_t2v
from ....tools.video.seedance_2_fast_t2v import generate_video_with_wavespeed_seedance_2_fast_t2v
from ....tools.video.seedance_2_t2v_turbo import generate_video_with_wavespeed_seedance_2_t2v_turbo
from ....tools.video.seedance_2_fast_t2v_turbo import generate_video_with_wavespeed_seedance_2_fast_t2v_turbo

logger = logging.getLogger(__name__)


# i2v 枚举（用户可见）→ (T2V ToolType, T2V leaf 工具)；T2V 是内部实现，用户无感
I2V_TO_T2V = {
    VideoGenerationTool.SEEDANCE_2_I2V: (ToolType.SEEDANCE_2_T2V, generate_video_with_wavespeed_seedance_2_t2v),
    VideoGenerationTool.SEEDANCE_2_FAST_I2V: (ToolType.SEEDANCE_2_FAST_T2V, generate_video_with_wavespeed_seedance_2_fast_t2v),
    VideoGenerationTool.SEEDANCE_2_I2V_TURBO: (ToolType.SEEDANCE_2_T2V_TURBO, generate_video_with_wavespeed_seedance_2_t2v_turbo),
    VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO: (ToolType.SEEDANCE_2_FAST_T2V_TURBO, generate_video_with_wavespeed_seedance_2_fast_t2v_turbo),
}

# 默认（兜底）：Fast T2V（最快最便宜的非 turbo 标准变体之一）
_DEFAULT_T2V = (ToolType.SEEDANCE_2_FAST_T2V, generate_video_with_wavespeed_seedance_2_fast_t2v)

# video_gen 支持的 SD2 全家桶
SD2_VIDEO_TOOLS = frozenset(I2V_TO_T2V.keys())


class VideoGenT2VInput(BaseModel):
    """Input schema for the video_gen text-to-video tool."""
    t2v_prompt: str = Field(
        description="Detailed cinematic description: scene, subject, action, camera movement, lighting, mood. "
        "Write like a film director. Each call generates ONE video."
    )
    duration: int = Field(
        default=5,
        description="Video duration in seconds. Supported range: 4-15.",
    )
    reference_images: Optional[List[str]] = Field(
        default=None,
        description="Optional reference image URLs for style/character/scene guidance (only if user provided references).",
    )
    reference_videos: Optional[List[str]] = Field(
        default=None,
        description="Optional reference video URLs (total length must not exceed 15 seconds).",
    )
    reference_audios: Optional[List[str]] = Field(
        default=None,
        description="Optional reference audio URLs (total length must not exceed 15 seconds).",
    )
    runtime: Annotated[Any, SkipValidation, SkipJsonSchema()] = Field(
        default=None,
        description="ToolRuntime injected by LangGraph (internal use only)",
    )


def _resolve_t2v(user_option: Optional[UserOption]):
    """解析 user_option.video_generation_tool → (ToolType, leaf 工具)。非 SD2 走默认 Fast T2V。"""
    vgt = getattr(user_option, "video_generation_tool", None) if user_option else None
    return I2V_TO_T2V.get(vgt, _DEFAULT_T2V)


def _make_t2v_agent_tool(tool_type: ToolType, leaf_tool: BaseTool) -> BaseTool:
    """把 T2V leaf 工具包装成返回 content_and_artifact 的 agent 工具。

    artifact = VideoGenerationResult，便于 service 从 ToolMessage.artifact 直接收集 videos[]。
    分辨率/比例/参考素材均由 VideoGenerationContext 下发，leaf 工具内部优先读 context。
    """

    @tool("generate_video", args_schema=VideoGenT2VInput, response_format="content_and_artifact")
    async def generate_video(
        t2v_prompt: str,
        runtime: ToolRuntime[VideoGenerationContext],
        duration: int = 5,
        reference_images: Optional[List[str]] = None,
        reference_videos: Optional[List[str]] = None,
        reference_audios: Optional[List[str]] = None,
    ) -> tuple[str, VideoGenerationResult]:
        """Generate ONE video from a text prompt (SD2 text-to-video). Call multiple times to generate multiple videos. Resolution/aspect_ratio/duration/reference assets come from runtime.context when set."""
        ctx_duration = getattr(runtime.context, "duration", None) if (runtime and runtime.context) else None
        effective_duration = ctx_duration if ctx_duration else duration
        result = await leaf_tool.ainvoke({
            "t2v_prompt": t2v_prompt,
            "duration": effective_duration,
            "reference_images": reference_images,
            "reference_videos": reference_videos,
            "reference_audios": reference_audios,
            "runtime": runtime,
        })
        return (result.model_dump_json(), result)

    if hasattr(generate_video, "metadata"):
        if generate_video.metadata is None:
            generate_video.metadata = {}
        generate_video.metadata.update(
            tool_type=tool_type.value,
            provider=ToolProvider.WAVESPEED.value,
            category=ToolCategory.VIDEO_GENERATION.value,
        )
    return generate_video


def get_video_gen_tools(user_option: Optional[UserOption], has_input_image: bool) -> List[BaseTool]:
    """返回 video_gen 注入 React Agent 的工具列表。

    - has_input_image=True → I2V：复用现有 video wrapper（带 fallback/一致性/metrics）。
    - 否则 → T2V：单个 content_and_artifact 包装工具（对应用户选定的 SD2 变体）。
    """
    if has_input_image:
        from ....services.tool_service import ToolService
        tools_info = ToolService.get_video_generation_tools(user_option)
        return tools_info.tool_objects

    tool_type, leaf_tool = _resolve_t2v(user_option)
    return [_make_t2v_agent_tool(tool_type, leaf_tool)]
