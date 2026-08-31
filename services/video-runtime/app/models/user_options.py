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
    AUTO = "auto"  # 自动选择，与视频 auto 一致；入队/计费/Wrapper 展开为 DEFAULT_IMAGE_TOOL
    NANO_BANANA = "nano_banana"           # gemini-2.5-flash-image
    NANO_BANANA_2 = "nano_banana_2"       # gemini-3.1-flash-image-preview，便宜版 Pro
    NANO_BANANA_PRO = "nano_banana_pro"   # gemini-3-pro-image-preview，专业版
    SEEDREAM = "seedream"                 # WaveSpeed Seedream v4.5
    GPT_IMAGE_2 = "gpt_image_2"           # WaveSpeed openai/gpt-image-2（T2I + Edit）


class NanoBananaModel(str, Enum):
    """Nano Banana 模型枚举"""
    FLASH = "gemini-2.5-flash-image"  # Nano Banana (快速版)
    PRO = "gemini-3-pro-image-preview"  # Nano Banana Pro (专业版)


class VideoGenerationTool(str, Enum):
    """视频生成工具枚举（用户选项）"""
    AUTO = "auto"  # 自动选择，前端显示 auto，后端使用 DEFAULT_VIDEO_TOOL
    POLLO_SEEDANCE = "pollo_seedance"  # Seedance v1 Pro Fast（支持首尾帧 Lite）
    SEEDANCE_V1_5 = "pollo_seedance_v1_5"  # Seedance v1.5 Pro (image-to-video-fast)，720p/1080p，默认不生成语音
    SEEDANCE_2_I2V = "seedance_2_i2v"  # Seedance 2.0 Image-to-Video（WaveSpeed），480p/720p/1080p，4–15s，可选 last_image，generate_audio=False
    SEEDANCE_2_I2V_TURBO = "seedance_2_i2v_turbo"  # Seedance 2.0 Image-to-Video Turbo（WaveSpeed），720p/1080p，4–15s，可选 last_image，generate_audio=False
    SEEDANCE_2_FAST_I2V = "seedance_2_fast_i2v"  # Seedance 2.0 Fast Image-to-Video（WaveSpeed），480p/720p（无 1080p），4–15s，可选 last_image，generate_audio=False
    SEEDANCE_2_FAST_I2V_TURBO = "seedance_2_fast_i2v_turbo"  # Seedance 2.0 Fast Image-to-Video Turbo（WaveSpeed legacy fast endpoint），720p/1080p，4–15s
    WAN_2_5 = "wan_2_5"  # Alibaba Wan 2.5 (WaveSpeed), I2V with optional audio, no end image
    WAN_2_6 = "wan_2_6_flash"  # Alibaba Wan 2.6 Flash (WaveSpeed), image-to-video-flash, 720p/1080p, 3-15s
    LTX_2_3 = "ltx_2_3"  # WaveSpeed LTX 2.3 Lipsync, audio+image→video, 480p/720p/1080p
    KLING_V2_AI_AVATAR_PRO = "kling_v2_ai_avatar_pro"  # Kwaivgi Kling V2 AI Avatar Pro, audio+image→talking-head；产品分辨率由 ensure_on_s3 对齐
    WAN_2_2_SPEECH_TO_VIDEO = "wan_2_2_speech_to_video"  # WAN 2.2 Speech-to-Video (WaveSpeed)，官方 API 仅 480p/720p；capabilities 不提供 1080p（避免对 720p 成片做放大对齐）
    KLING_V3_STD = "kling_v3_std"  # Kling v3.0 Std (WaveSpeed), image-to-video, 3-15s, $0.90/5s
    HAPPYHORSE_1_0_I2V = "happyhorse_1_0_i2v"  # Alibaba HappyHorse 1.0 (WaveSpeed), image-to-video, 720p/1080p, 3-15s
    HAPPYHORSE_1_1_I2V = "happyhorse_1_1_i2v"  # Alibaba HappyHorse 1.1 (WaveSpeed), image-to-video, 720p/1080p, 3-15s
    OPENAI_SORA = "openai_sora"
    OPENAI_SORA_PRO = "openai_sora_pro"


# 用户选 auto 时实际使用的视频工具（仅此处修改即可切换默认）
DEFAULT_VIDEO_TOOL = VideoGenerationTool.POLLO_SEEDANCE  # Seedance v1.0 Pro Fast，比 v1.5 更快

# 支持 reference-to-video（T2V + reference_images 等）的用户视频工具；新模型接入时扩展此集合
REFERENCE_TO_VIDEO_VIDEO_TOOLS = frozenset({
    VideoGenerationTool.SEEDANCE_2_I2V,
    VideoGenerationTool.SEEDANCE_2_I2V_TURBO,
    VideoGenerationTool.SEEDANCE_2_FAST_I2V,
    VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO,
})

# 用户选 image auto 时实际使用的图像工具（与 DefaultValues.DEFAULT_IMAGE_TOOL_NAME 一致）
DEFAULT_IMAGE_TOOL = ImageGenerationTool(DefaultValues.DEFAULT_IMAGE_TOOL_NAME)

# 支持口型同步的视频工具（仅此集合内的在 lipsync 镜头时使用）；新增口型模型时只改此处
LIPSYNC_CAPABLE_VIDEO_TOOLS = frozenset({
    VideoGenerationTool.LTX_2_3,
    VideoGenerationTool.KLING_V2_AI_AVATAR_PRO,
    VideoGenerationTool.WAN_2_2_SPEECH_TO_VIDEO,
    VideoGenerationTool.WAN_2_5,
    VideoGenerationTool.WAN_2_6,
})

# 口型视频工具下拉选项（供 GET /options/capabilities 与前端使用）；含 auto，执行时由 lipsync_tool_wrapper 展开为默认口型模型
LIPSYNC_VIDEO_TOOL_OPTIONS = [
    {"value": VideoGenerationTool.AUTO.value, "label": "Auto"},
    {"value": VideoGenerationTool.LTX_2_3.value, "label": "WaveSpeed LTX 2.3 Lipsync"},
    {"value": VideoGenerationTool.KLING_V2_AI_AVATAR_PRO.value, "label": "Kling V2 AI Avatar Pro"},
    {"value": VideoGenerationTool.WAN_2_2_SPEECH_TO_VIDEO.value, "label": "WAN 2.2 Speech-to-Video"},
    {"value": VideoGenerationTool.WAN_2_5.value, "label": "WaveSpeed Wan 2.5"},
    {"value": VideoGenerationTool.WAN_2_6.value, "label": "WaveSpeed Wan 2.6 Flash"},
]


def _is_short_drama_user_option(user_option: Optional["UserOption"]) -> bool:
    if not user_option or not getattr(user_option, "content_category", None):
        return False
    return user_option.content_category == ContentCategory.SHORT_DRAMA


def resolve_effective_video_tool(user_option: "UserOption") -> "VideoGenerationTool":
    """解析普通视频实际使用的 VideoGenerationTool（与 cost_estimation / wrapper chain 一致）。

    Short Drama + AUTO → Seedance 2 Fast I2V Turbo（对白/片内声）；用户已显式选择其它工具则不覆盖。
    """
    vgt = getattr(user_option, "video_generation_tool", None)
    if vgt is None or vgt == VideoGenerationTool.AUTO:
        if _is_short_drama_user_option(user_option):
            return VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO
        return DEFAULT_VIDEO_TOOL
    return vgt


def apply_short_drama_default_video_tool(user_option: Optional["UserOption"]) -> Optional["UserOption"]:
    """Short Drama 且视频工具为 AUTO 时，落盘为 Seedance2 Fast Turbo；已选手动工具则不变。"""
    if not user_option or not _is_short_drama_user_option(user_option):
        return user_option
    vgt = getattr(user_option, "video_generation_tool", None)
    if vgt is not None and vgt != VideoGenerationTool.AUTO:
        return user_option
    return user_option.model_copy(
        update={"video_generation_tool": VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO}
    )


def materialize_video_generation_tool(user_option: Optional["UserOption"]) -> Optional["UserOption"]:
    """入队前将 AUTO 展开为实际工具（Short Drama→SD2 Fast Turbo，否则 DEFAULT_VIDEO_TOOL）。

    禁止先无脑写成 DEFAULT_VIDEO_TOOL 再跑短剧默认——那样会把 Short Drama+AUTO 锁死成 pollo_seedance，
    导致仍走关键帧管线。
    """
    if not user_option:
        return user_option
    effective = resolve_effective_video_tool(user_option)
    current = getattr(user_option, "video_generation_tool", None)
    if current == effective:
        return user_option
    return user_option.model_copy(update={"video_generation_tool": effective})


def video_tool_supports_reference_to_video(tool: Optional[VideoGenerationTool]) -> bool:
    """当前视频工具是否支持 reference-to-video（T2V + reference 素材）。"""
    return tool is not None and tool in REFERENCE_TO_VIDEO_VIDEO_TOOLS


def should_skip_keyframe_pipeline(user_option: Optional["UserOption"]) -> bool:
    """Seedance2 系列：跳过 first_frame_revision / keyframe / reflection，直接 reference-to-video。"""
    if not user_option:
        return False
    return video_tool_supports_reference_to_video(resolve_effective_video_tool(user_option))


def should_use_reference_to_video(user_option: Optional["UserOption"]) -> bool:
    """是否对普通视频镜头使用 reference-to-video（T2V + reference_images）而非 I2V。

    Seedance2（跳过 keyframe 管线）时强制开启；其它工具仍受 USE_REFERENCE_TO_VIDEO 开关约束。
    """
    if not user_option:
        return False
    if should_skip_keyframe_pipeline(user_option):
        return True
    from app.services.agent.video.agent_video_constants import USE_REFERENCE_TO_VIDEO

    if not USE_REFERENCE_TO_VIDEO:
        return False
    return video_tool_supports_reference_to_video(resolve_effective_video_tool(user_option))


def build_reference_to_video_images(
    start_image_url: Optional[str],
    end_image_url: Optional[str],
    character_ref_urls: Optional[List[str]],
    max_count: int,
) -> List[str]:
    """组装 reference-to-video 的 reference_images：首帧 → 尾帧 → 角色图，去重并截断。"""
    refs: List[str] = []
    for url in (start_image_url, end_image_url):
        if url and url not in refs:
            refs.append(url)
    for url in character_ref_urls or []:
        if url and url not in refs:
            refs.append(url)
    return refs[:max(1, max_count)]


def should_apply_lipsync_for_planning(user_option: Optional["UserOption"]) -> bool:
    """是否将口型链并入 AUDIO 规划（duration 交集、scene split 显式阈值）。"""
    return should_enable_lipsync_for_run(user_option)


def should_enable_lipsync_for_run(
    user_option: Optional["UserOption"] = None,
    *,
    music_intent: Optional[str] = None,
    music_workflow_mode: Optional[str] = None,
    audio_transcription: Optional[Any] = None,
) -> bool:
    """本 run 是否允许口型（场景/镜头 generation_mode、转录切分、视频生成）。

    纯 BGM / 并行 BGM / 无歌词转录 / 全段无人声 → 禁止口型（与有无 lipsync_coverage 无关）。
    """
    if (music_intent or "").strip() == "instrumental_bgm":
        return False
    if (music_workflow_mode or "").strip() == "bgm_parallel":
        return False
    if audio_transcription is not None:
        if getattr(audio_transcription, "is_instrumental", False):
            return False
        segments = getattr(audio_transcription, "segments", None) or []
        if segments:
            has_vocal = False
            for seg in segments:
                vp = getattr(seg, "vocal_presence", None)
                if vp is True:
                    has_vocal = True
                    break
                if vp is None and (getattr(seg, "text", None) or "").strip():
                    has_vocal = True
                    break
            if not has_vocal:
                return False
    if not user_option:
        return False
    from app.models.tool_enums import ContentCategory

    if user_option.content_category == ContentCategory.LIP_SYNC_MV:
        return True
    if user_option.content_category == ContentCategory.PRODUCT_LAUNCH:
        return True
    # 短剧：角色对白走 Seedance 片内声，不走独立 lipsync/TTS 口型链
    # （前端仍可能带 lipsync_coverage，不得因此打开口型）
    if user_option.content_category == ContentCategory.SHORT_DRAMA:
        return False
    if getattr(user_option, "lipsync_coverage", 0) > 0:
        return True
    return False


def resolve_effective_lipsync_tool(
    user_option: "UserOption",
) -> "VideoGenerationTool":
    """统一解析 lipsync 实际使用的 VideoGenerationTool（单一来源，所有 caller 共用）。

    解析优先级：
      1. user_option.lipsync_video_tool（用户显式选择的口型工具，非 auto）
      2. user_option.video_generation_tool（非 auto 且在 LIPSYNC_CAPABLE 内）
      3. auto → 按 resolution 分流：1080p → kling_v2_ai_avatar_pro，其他 → wan_2_2_speech_to_video

    调用方：cost_estimation._cost_per_lipsync_shot_dollar / lipsync_tool_wrapper / per_shot_routing
    """
    lip = getattr(user_option, "lipsync_video_tool", None)
    if lip is not None and lip != VideoGenerationTool.AUTO and lip in LIPSYNC_CAPABLE_VIDEO_TOOLS:
        return lip

    vgt = getattr(user_option, "video_generation_tool", None)
    if vgt is not None and vgt != VideoGenerationTool.AUTO and vgt in LIPSYNC_CAPABLE_VIDEO_TOOLS:
        return vgt

    res = getattr(user_option, "resolution", None)
    rv = getattr(res, "value", None) if res is not None else None
    if rv == Resolution.P1080.value:
        return VideoGenerationTool.KLING_V2_AI_AVATAR_PRO
    return VideoGenerationTool.WAN_2_2_SPEECH_TO_VIDEO


async def get_tool_capabilities(
    image_tool: Optional[ImageGenerationTool] = None,
    video_tool: Optional[VideoGenerationTool] = None,
    lipsync_tool: Optional[VideoGenerationTool] = None,
) -> Dict[str, Any]:
    """按当前选中的 image_tool / video_tool / lipsync_tool 返回支持的 aspect_ratio、resolution 及口型工具列表。
    warnings 仅在可选范围被实际能力收窄时追加（如 Sora 无 1080p）；不因「正常工作流」追加说明文案。"""
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

    # 4) 若传入 lipsync_tool，与 video 能力取交集（LTX / Kling / Wan 2.5/2.6：480p/720p/1080p；WAN 2.2 S2V 仅 480p/720p，见 docs）
    lt_eff = lipsync_tool
    if lt_eff == VideoGenerationTool.AUTO:
        lt_eff = VideoGenerationTool(DefaultValues.DEFAULT_LIPSYNC_TOOL_VALUE)
    if lt_eff and lt_eff in LIPSYNC_CAPABLE_VIDEO_TOOLS:
        lipsync_res = [Resolution.P480, Resolution.P720, Resolution.P1080]
        if lt_eff == VideoGenerationTool.WAN_2_2_SPEECH_TO_VIDEO:
            lipsync_res = [Resolution.P480, Resolution.P720]
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
        description="用户选择的图像生成工具；auto 时与视频 auto 一致由后端展开为 DEFAULT_IMAGE_TOOL（入队时与 Wrapper 内均解析）",
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
        description="口型视频模型：仅对口型同步镜头生效；可选 auto 或 LTX 2.3 / Kling V2 AI Avatar Pro / Wan 2.5 / Wan 2.6 Flash。默认 auto：由 per_shot_generation_routing 按分辨率写入 wan_2_2_speech_to_video / kling_v2_ai_avatar_pro，执行前经 resolve_user_option_for_shot 合并；未合并时再在 lipsync_tool_wrapper 内展开为 DEFAULT_LIPSYNC。",
        default=VideoGenerationTool.AUTO,
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
        description="内容类别：Default / Lip-Sync MV / Product Launch / Short Drama（前端传字符串会被自动转成 enum）"
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
        description="完全托管：视频管线门闩可跳过 interrupt，或 interrupt 后由后端延迟自动 continue；与前端 Full Auto / autoContinueOnInterrupt 一致，须持久化在 conversation_runs.user_option",
    )

    def __init__(self, **data):
        super().__init__(**data)
    
    @classmethod
    def default(cls) -> "UserOption":
        """创建默认用户选项"""
        return cls(
            image_generation_tool=ImageGenerationTool(DefaultValues.DEFAULT_IMAGE_TOOL_NAME),
            video_generation_tool=VideoGenerationTool.AUTO,
            lipsync_video_tool=VideoGenerationTool.AUTO,
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
