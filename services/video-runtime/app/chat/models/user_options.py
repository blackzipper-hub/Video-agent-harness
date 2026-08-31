"""
用户选项相关模型
"""
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# 从统一 enum 文件导入
from .tool_enums import Resolution, AspectRatio, ContentCategory, DefaultValues
from ..utils.i18n import get_i18n_message_async


class ImageGenerationTool(str, Enum):
    """图像生成工具枚举（用户选项）"""
    AUTO = "auto"  # 与 Cuti-VideoAgent 一致；前端可选 auto，后端展开为 DEFAULT_IMAGE_TOOL
    NANO_BANANA = "nano_banana"           # gemini-2.5-flash-image
    NANO_BANANA_2 = "nano_banana_2"       # gemini-3.1-flash-image-preview，便宜版 Pro
    NANO_BANANA_PRO = "nano_banana_pro"   # gemini-3-pro-image-preview，专业版
    SEEDREAM = "seedream"                 # WaveSpeed Seedream v4.5
    GPT_IMAGE_2 = "gpt_image_2"           # WaveSpeed openai/gpt-image-2（与 Cuti-VideoAgent UserOption 对齐）


class NanoBananaModel(str, Enum):
    """Nano Banana 模型枚举"""
    FLASH = "gemini-2.5-flash-image"  # Nano Banana (快速版)
    PRO = "gemini-3-pro-image-preview"  # Nano Banana Pro (专业版)


class VideoGenerationTool(str, Enum):
    """视频生成工具枚举（用户选项）"""
    AUTO = "auto"  # 自动选择，前端显示 auto，后端使用 DEFAULT_VIDEO_TOOL
    POLLO_SEEDANCE = "pollo_seedance"  # Seedance v1 Pro Fast（支持首尾帧 Lite）
    SEEDANCE_V1_5 = "pollo_seedance_v1_5"  # Seedance v1.5 Pro (image-to-video-fast)，720p/1080p，默认不生成语音
    SEEDANCE_2_I2V = "seedance_2_i2v"  # Seedance 2.0 Image-to-Video（WaveSpeed），480p/720p/1080p，4–15s
    SEEDANCE_2_I2V_TURBO = "seedance_2_i2v_turbo"  # Seedance 2.0 Image-to-Video Turbo（WaveSpeed），720p/1080p，4–15s
    SEEDANCE_2_FAST_I2V = "seedance_2_fast_i2v"  # Seedance 2.0 Fast Image-to-Video（WaveSpeed），480p/720p（无 1080p），4–15s
    SEEDANCE_2_FAST_I2V_TURBO = "seedance_2_fast_i2v_turbo"  # Seedance 2.0 Fast I2V Turbo（WaveSpeed legacy），与 Cuti-VideoAgent 对齐
    WAN_2_5 = "wan_2_5"  # Alibaba Wan 2.5 (WaveSpeed), I2V with optional audio, no end image
    WAN_2_6 = "wan_2_6_flash"  # Alibaba Wan 2.6 Flash (WaveSpeed), image-to-video-flash, 720p/1080p, 3-15s
    LTX_2_3 = "ltx_2_3"  # WaveSpeed LTX 2.3 Lipsync, audio+image→video, 480p/720p/1080p
    KLING_V3_STD = "kling_v3_std"  # Kling v3.0 Std (WaveSpeed), image-to-video, 3-15s, $0.90/5s
    HAPPYHORSE_1_0_I2V = "happyhorse_1_0_i2v"  # Alibaba HappyHorse 1.0 (WaveSpeed), image-to-video, 720p/1080p, 3-15s
    HAPPYHORSE_1_1_I2V = "happyhorse_1_1_i2v"  # Alibaba HappyHorse 1.1 (WaveSpeed), image-to-video, 720p/1080p, 3-15s
    OPENAI_SORA = "openai_sora"
    OPENAI_SORA_PRO = "openai_sora_pro"


# 用户选 auto 时实际使用的视频工具（仅此处修改即可切换默认）
DEFAULT_VIDEO_TOOL = VideoGenerationTool.POLLO_SEEDANCE  # Seedance v1.0 Pro Fast，比 v1.5 更快

# 用户选 image auto 时实际使用的图像工具（与 DefaultValues.DEFAULT_IMAGE_TOOL_NAME 一致）
DEFAULT_IMAGE_TOOL = ImageGenerationTool(DefaultValues.DEFAULT_IMAGE_TOOL_NAME)

# 支持口型同步的视频工具（仅此集合内的在 lipsync 镜头时使用）；新增口型模型时只改此处
LIPSYNC_CAPABLE_VIDEO_TOOLS = frozenset({
    VideoGenerationTool.LTX_2_3,
    VideoGenerationTool.WAN_2_5,
    VideoGenerationTool.WAN_2_6,
})

# 口型视频工具下拉选项（供 GET /options/capabilities 与前端使用），与 LIPSYNC_CAPABLE_VIDEO_TOOLS 一致
LIPSYNC_VIDEO_TOOL_OPTIONS = [
    {"value": VideoGenerationTool.LTX_2_3.value, "label": "WaveSpeed LTX 2.3 Lipsync"},
    {"value": VideoGenerationTool.WAN_2_5.value, "label": "WaveSpeed Wan 2.5"},
    {"value": VideoGenerationTool.WAN_2_6.value, "label": "WaveSpeed Wan 2.6 Flash"},
]


async def get_tool_capabilities(
    image_tool: Optional[ImageGenerationTool] = None,
    video_tool: Optional[VideoGenerationTool] = None,
    lipsync_tool: Optional[VideoGenerationTool] = None,
) -> Dict[str, Any]:
    """按当前选中的 image_tool / video_tool / lipsync_tool 返回支持的 aspect_ratio、resolution 及口型工具列表。warnings 仅写支持项。"""
    warnings: List[str] = []

    # 1) 视频工具支持的分辨率（见 docs/resolution_aspect_ratio_support_and_rules.md 1.2）
    vt = video_tool or DEFAULT_VIDEO_TOOL
    if vt == VideoGenerationTool.AUTO:
        vt = DEFAULT_VIDEO_TOOL
    if vt in (VideoGenerationTool.OPENAI_SORA, VideoGenerationTool.OPENAI_SORA_PRO):
        # Sora API 仅输出 720p；480p 由 720p 成片下采样实现，产品上视为支持 480p/720p
        allowed_res = [Resolution.P480, Resolution.P720]
        warnings.append(await get_i18n_message_async("capability_warnings.sora"))
    elif vt == VideoGenerationTool.KLING_V3_STD:
        allowed_res = [Resolution.P480, Resolution.P720]
        warnings.append(await get_i18n_message_async("capability_warnings.kling"))
    elif vt == VideoGenerationTool.SEEDANCE_2_FAST_I2V:
        allowed_res = [Resolution.P480, Resolution.P720]
        warnings.append(await get_i18n_message_async("capability_warnings.seedance_2_fast_i2v"))
    else:
        # Seedance v1、v1.5、2.0、2.0 Turbo、Wan 2.5、2.6 等：480p/720p/1080p（部分 480p 由 720p 缩小）
        allowed_res = [Resolution.P480, Resolution.P720, Resolution.P1080]

    # 2) 视频工具支持的宽高比（Sora 无 1:1）
    if vt in (VideoGenerationTool.OPENAI_SORA, VideoGenerationTool.OPENAI_SORA_PRO):
        allowed_ar = [AspectRatio.LANDSCAPE, AspectRatio.PORTRAIT]
    else:
        allowed_ar = [AspectRatio.LANDSCAPE, AspectRatio.PORTRAIT, AspectRatio.SQUARE]
    aspect_ratios = [{"value": ar.value, "label": ar.value} for ar in allowed_ar]

    # 3) 图像工具再收窄分辨率（见文档 1.1：仅 Nano Banana 2.5 Flash 无 1080p）
    img_eff = image_tool
    if img_eff == ImageGenerationTool.AUTO:
        img_eff = DEFAULT_IMAGE_TOOL
    if img_eff == ImageGenerationTool.NANO_BANANA:
        allowed_res = [r for r in allowed_res if r != Resolution.P1080]
        warnings.append(await get_i18n_message_async("capability_warnings.nano_banana"))

    # 4) 若传入 lipsync_tool，与 video 能力取交集（见 docs/tool-output-specs.md 1.1：LTX 2.3 / Wan 2.5 / Wan 2.6 均支持 480p/720p/1080p、16:9/1:1/9:16）
    if lipsync_tool and lipsync_tool in LIPSYNC_CAPABLE_VIDEO_TOOLS:
        lipsync_res = [Resolution.P480, Resolution.P720, Resolution.P1080]
        lipsync_ar = [AspectRatio.LANDSCAPE, AspectRatio.PORTRAIT, AspectRatio.SQUARE]
        allowed_res = [r for r in allowed_res if r in lipsync_res]
        allowed_ar = [a for a in allowed_ar if a in lipsync_ar]
        aspect_ratios = [{"value": ar.value, "label": ar.value} for ar in allowed_ar]

    resolutions = [{"value": r.value, "label": r.value} for r in allowed_res]

    return {
        "aspect_ratios": aspect_ratios,
        "resolutions": resolutions,
        "warnings": warnings,
        "lipsync_video_tools": list(LIPSYNC_VIDEO_TOOL_OPTIONS),
    }


class VideoMode(str, Enum):
    """视频生成模式"""
    INSTANT = "instant"  # 快速模式
    MASTER = "master"    # 大师模式


class UserOption(BaseModel):
    """用户选项配置"""
    image_generation_tool: ImageGenerationTool = Field(
        description="用户选择的图像生成工具；auto 时由下游 VideoAgent 展开为默认图像模型（与 VA UserOption 一致）",
        default=ImageGenerationTool(DefaultValues.DEFAULT_IMAGE_TOOL_NAME)
    )
    # 移除 nano_banana_model 字段，通过 image_generation_tool 即可判断：
    # NANO_BANANA -> gemini-2.5-flash-image
    # NANO_BANANA_2 -> gemini-3.1-flash-image-preview
    # NANO_BANANA_PRO -> gemini-3-pro-image-preview
    video_generation_tool: VideoGenerationTool = Field(
        description="用户选择的视频生成工具",
        default=VideoGenerationTool.AUTO
    )
    lipsync_video_tool: VideoGenerationTool = Field(
        description="口型视频模型：仅对口型同步镜头生效，可选 LTX 2.3 / Wan 2.5 / Wan 2.6 Flash",
        default=VideoGenerationTool(DefaultValues.DEFAULT_LIPSYNC_TOOL_VALUE)
    )
    mode: VideoMode = Field(
        description="视频生成模式 - instant快速模式 或 master大师模式",
        default=VideoMode.MASTER
    )
    aspect_ratio: AspectRatio = Field(
        description="视频宽高比：16:9 横屏、1:1 方屏、9:16 竖屏。部分视频工具不支持全部组合（如 Sora 无 1:1 会回退横屏）；由后端自动映射，前端可通过 GET /agent-router/options/capabilities 按当前工具获取说明。",
        default=AspectRatio.LANDSCAPE
    )
    resolution: Resolution = Field(
        description="视频分辨率：480p / 720p / 1080p。部分工具仅支持子集（如 Sora 仅 720p，Seedance v1.5 / Wan 2.6 仅 720p/1080p）；由后端自动映射或降档，前端可通过 GET /agent-router/options/capabilities 按当前工具获取可选列表与说明。",
        default=Resolution.P1080
    )
    duration: int = Field(
        description="视频时长（秒），范围5-600秒",
        default=30,
        ge=5,
        le=600
    )
    lipsync_coverage: int = Field(
        description="唇形同步覆盖率百分比：0=关闭, 10/20/30...=覆盖比例",
        default=0,
        ge=0,
        le=100
    )
    content_category: Optional[ContentCategory] = Field(
        default=ContentCategory.DEFAULT,
        description="内容类别：Default=故事性 MV，Lip-Sync MV=对嘴模版，Product Launch=产品发布 talking head（前端传字符串会被自动转成 enum）"
    )
    enable_continuity_mode: bool = Field(
        description="是否启用连续模式（所有帧从头到尾保持连续连接）",
        default=False
    )
    
    # Keyframe Reflection 配置
    enable_keyframe_reflection: bool = Field(
        description="是否启用关键帧反思（分析角色一致性并优化）",
        default=False
    )
    max_reflection_iterations: int = Field(
        description="关键帧反思最大迭代次数",
        default=2,
        ge=1,
        le=5
    )
    reflection_threshold: float = Field(
        description="关键帧反思停止阈值（一致性评分达到此值时停止）",
        default=0.8,
        ge=0.0,
        le=1.0
    )
    reflection_concurrency: int = Field(
        description="关键帧反思并发数",
        default=5,
        ge=1,
        le=10
    )
    full_auto: bool = Field(
        default=False,
        description="完全托管：与下游 VideoAgent full_auto 一致，须随 user_option 持久化到 conversation_runs",
    )

    def __init__(self, **data):
        super().__init__(**data)
    
    @classmethod
    def default(cls) -> "UserOption":
        """创建默认用户选项"""
        return cls(
            image_generation_tool=ImageGenerationTool(DefaultValues.DEFAULT_IMAGE_TOOL_NAME),
            video_generation_tool=VideoGenerationTool.AUTO,
            lipsync_video_tool=VideoGenerationTool(DefaultValues.DEFAULT_LIPSYNC_TOOL_VALUE),
            mode=VideoMode.MASTER,
            aspect_ratio=AspectRatio.LANDSCAPE,
            resolution=Resolution.P1080,
            duration=30,
            lipsync_coverage=0,
            content_category=ContentCategory.DEFAULT,
            enable_continuity_mode=False,
            enable_keyframe_reflection=False,
            max_reflection_iterations=2,
            reflection_threshold=0.8,
            reflection_concurrency=5,
            full_auto=False,
        )
