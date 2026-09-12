"""
Lipsync 视频生成工具 Wrapper — 统一入口（LIPSYNC_CAPABLE_VIDEO_TOOLS：LTX 2.3、Kling V2 AI Avatar Pro、WAN 2.2 S2V、Wan 2.5、Wan 2.6 Flash）。

与 video_tool_wrapper 一致：
- 调用链：create_lipsync_wrapper_tools 返回 (tool_infos, primary_tool)，ToolService 组装 ToolsInfo(user_option_tool=primary_tool.value)。
- 执行：复用 video_tool_wrapper._run_video_loop，同一套 fallback/重试逻辑，返回 VideoGenerationResult（含 model/provider）。
- 落库：由 video_generation_service 统一处理，model=实际尝试/成功的 ToolType.value，video_generation_tool=用户选择（ltx_2_3 / kling_v2_ai_avatar_pro / wan_2_2_speech_to_video / wan_2_5 / wan_2_6）。

流程：
  主模型（用户选项）→ 可重试错误同模型重试一次 → 按链降级 → 同模型最多重试一次。

关键参数流：与 video_tool_wrapper 一致，audio_url / duration 由 runtime.context 传递。
"""
import logging
from typing import List, Optional, Annotated, Any, Tuple, FrozenSet, TYPE_CHECKING

from pydantic import BaseModel, Field, SkipValidation
from pydantic.json_schema import SkipJsonSchema
from app.tools.runtime import tool
from app.tools.runtime import ToolRuntime

from app.models.image_result import VideoGenerationResult
from app.models.tool_enums import ToolType, ToolMode, ToolName
from app.models.user_options import VideoGenerationTool, LIPSYNC_CAPABLE_VIDEO_TOOLS
from app.tools.context_schemas import VideoGenerationContext

from .video_tool_wrapper import _run_video_loop

if TYPE_CHECKING:
    from app.services.tool_service import ToolInfo

logger = logging.getLogger(__name__)


# ==================== Chain 配置 ====================


def _get_lipsync_chain(tool: Optional[VideoGenerationTool]) -> List["ToolInfo"]:
    """Lipsync 模式：按用户选择返回尝试顺序（主 + fallback）。"""
    from app.services.tool_service import ToolInfo
    from app.models.tool_enums import ToolProvider, ToolCategory
    from app.models.tool_enums import DefaultValues
    from app.tools.video.ltx23_lipsync import generate_video_with_wavespeed_ltx23_lipsync
    from app.tools.video.kling_v2_ai_avatar_pro import generate_video_with_wavespeed_kling_v2_ai_avatar_pro
    from app.tools.video.wan22_speech_to_video import generate_video_with_wavespeed_wan22_speech_to_video
    from app.tools.video.wan25 import generate_video_with_wavespeed_wan25
    from app.tools.video.wan26_flash import generate_video_with_wavespeed_wan26

    def _i2v(
        tool_fn,
        tt: ToolType,
        prov: ToolProvider,
        supported_duration_seconds: FrozenSet[int],
    ) -> ToolInfo:
        if hasattr(tool_fn, "metadata"):
            if tool_fn.metadata is None:
                tool_fn.metadata = {}
            tool_fn.metadata.update(
                tool_type=tt.value,
                provider=prov.value,
                category=ToolCategory.VIDEO_GENERATION.value,
            )
        return ToolInfo(
            tool=tool_fn,
            tool_name=tool_fn.name,
            tool_type=tt,
            provider=prov,
            category=ToolCategory.VIDEO_GENERATION,
            mode=ToolMode.I2V,
            supports_lipsync=True,
            supported_duration_seconds=supported_duration_seconds,
        )

    _dur_3_10 = frozenset(range(3, 11))
    _dur_3_15 = frozenset(range(3, 16))
    _dur_5_10 = frozenset(range(5, 11))

    ltx23 = _i2v(generate_video_with_wavespeed_ltx23_lipsync, ToolType.LTX_2_3_LIPSYNC, ToolProvider.WAVESPEED, _dur_3_10)
    kling_avatar = _i2v(generate_video_with_wavespeed_kling_v2_ai_avatar_pro, ToolType.KLING_V2_AI_AVATAR_PRO, ToolProvider.WAVESPEED, _dur_5_10)
    wan22_s2v = _i2v(generate_video_with_wavespeed_wan22_speech_to_video, ToolType.WAN_2_2_SPEECH_TO_VIDEO, ToolProvider.WAVESPEED, _dur_5_10)
    w25 = _i2v(generate_video_with_wavespeed_wan25, ToolType.WAN_2_5_I2V, ToolProvider.WAVESPEED, _dur_3_10)
    w26 = _i2v(generate_video_with_wavespeed_wan26, ToolType.WAN_2_6_FLASH_I2V, ToolProvider.WAVESPEED, _dur_3_15)

    if tool == VideoGenerationTool.WAN_2_2_SPEECH_TO_VIDEO:
        return [wan22_s2v, kling_avatar, ltx23, w26, w25]
    if tool == VideoGenerationTool.KLING_V2_AI_AVATAR_PRO:
        return [kling_avatar, ltx23, w26, w25]
    if tool == VideoGenerationTool.LTX_2_3:
        return [ltx23, w26, w25]
    if tool == VideoGenerationTool.WAN_2_5:
        return [w25, w26]
    if tool == VideoGenerationTool.WAN_2_6:
        return [w26, w25]
    # AUTO / 其他（非口型支持列表）→ 默认 lipsync 工具
    default = VideoGenerationTool(DefaultValues.DEFAULT_LIPSYNC_TOOL_VALUE)
    if default == VideoGenerationTool.LTX_2_3:
        return [ltx23, w26, w25]
    if default == VideoGenerationTool.WAN_2_5:
        return [w25, w26]
    return [w26, w25]


# ==================== Wrapper Input ====================


class LipsyncWrapperI2VInput(BaseModel):
    """Lipsync 视频生成 Wrapper 的输入（与 I2V 一致，无 end_image_url；audio_url 从 context 读取）。"""

    i2v_prompt: str = Field(
        description="Motion description prompt for image-to-video generation. "
        "Describe actions, changes, and camera movements in detail. Must be under 800 characters."
    )
    start_image_url: str = Field(
        description="Starting image URL that serves as the first frame of the video. "
        "Supports HTTPS URLs or local file paths."
    )
    duration: int = Field(
        default=5,
        ge=3,
        le=10,
        description="Video duration in seconds. Supported range: 3-10 seconds (unified across lipsync models).",
    )
    runtime: Annotated[Any, SkipValidation, SkipJsonSchema()] = Field(
        default=None,
        description="Provider runtime context (internal use only)",
    )


def _make_lipsync_i2v_tool(chain: List["ToolInfo"]):
    """根据已选定的 lipsync chain 创建 I2V 工具（LIPSYNC_CAPABLE_VIDEO_TOOLS 内模型，各最多 2 次尝试）。"""
    assert chain, "lipsync chain must be non-empty"
    chain_desc = " → ".join(info.tool_type.value for info in chain)

    @tool(ToolName.LIPSYNC_WRAPPER_I2V, args_schema=LipsyncWrapperI2VInput, response_format="content_and_artifact")
    async def generate_lipsync_video_with_fallback_i2v(
        i2v_prompt: str,
        start_image_url: str,
        runtime: ToolRuntime[VideoGenerationContext],
        duration: int = 5,
    ) -> tuple[str, VideoGenerationResult]:
        f"""Lipsync video generation with automatic fallback - I2V with audio sync.
        Chain: {chain_desc}. Retry once per model on retryable errors, then fallback to the other model.
        Lipsync-capable chain per user option; audio_url and context parameters are read from runtime.context.
        """
        result = await _run_video_loop(
            i2v_prompt, start_image_url, duration, runtime, chain
        )
        return (result.model_dump_json(), result)

    return generate_lipsync_video_with_fallback_i2v


# ==================== 工具创建函数 ====================


def create_lipsync_wrapper_tools(
    mode: Optional[ToolMode] = None,
    user_option: Optional[Any] = None,
    resolution: Optional[Any] = None,
) -> Tuple[List["ToolInfo"], VideoGenerationTool]:
    """创建带 fallback 的 Lipsync 视频生成工具 Wrapper。

    仅支持 LIPSYNC_CAPABLE_VIDEO_TOOLS；用户选其他工具时 fallback 到默认 lipsync 工具。
    Chain：主模型 + 另一模型为 fallback，每个模型最多 2 次尝试。

    Returns:
        (tool_infos, primary_tool): 工具列表与主选枚举（供 caller 用 primary_tool.value 写 user_option_tool，与 video/image 一致）
    """
    from app.models.tool_enums import ToolCategory, DefaultValues
    from app.services.tool_service import ToolInfo

    from app.models.user_options import resolve_effective_lipsync_tool
    _lip = getattr(user_option, "lipsync_video_tool", None)
    _vgt = getattr(user_option, "video_generation_tool", None)
    logger.info(
        "[LipsyncWrapper] lipsync_tool_diag raw_lipsync_video_tool=%s raw_video_generation_tool=%s resolution=%s",
        getattr(_lip, "value", _lip),
        getattr(_vgt, "value", _vgt),
        getattr(resolution, "value", resolution) if resolution else None,
    )
    tool = resolve_effective_lipsync_tool(user_option) if user_option else VideoGenerationTool(DefaultValues.DEFAULT_LIPSYNC_TOOL_VALUE)
    logger.info(
        "[LipsyncWrapper] lipsync_tool_diag resolved=%s",
        tool.value,
    )

    chain = _get_lipsync_chain(tool)
    primary = chain[0]

    tool_obj = _make_lipsync_i2v_tool(chain)
    tool_info = ToolInfo(
        tool=tool_obj,
        tool_name=tool_obj.name,
        tool_type=primary.tool_type,
        provider=primary.provider,
        category=ToolCategory.VIDEO_GENERATION,
        mode=ToolMode.I2V,
        supports_lipsync=True,
        supported_duration_seconds=primary.supported_duration_seconds,
    )

    if hasattr(tool_info.tool, "metadata"):
        if tool_info.tool.metadata is None:
            tool_info.tool.metadata = {}
        tool_info.tool.metadata.update(
            tool_type=primary.tool_type.value,
            provider=primary.provider.value,
            category=ToolCategory.VIDEO_GENERATION.value,
        )

    logger.info(
        "🎭 [LipsyncWrapper] 创建完成: chain=%s, provider=%s primary_enum=%s",
        [info.tool_type.value for info in chain],
        primary.provider.value,
        tool.value,
    )
    return ([tool_info], tool)
