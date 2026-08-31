"""
Regenerate 时从版本记录重建 UserOption，只读版本表列（aspect_ratio / resolution / image_generation_tool / video_generation_tool 等），缺的用 fallback / default。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Union

from ....models.user_options import (
    UserOption,
    ImageGenerationTool,
    VideoGenerationTool,
    LIPSYNC_CAPABLE_VIDEO_TOOLS,
)
from ....models.tool_enums import AspectRatio, Resolution, DefaultValues

if TYPE_CHECKING:
    from ....schemas.video.video_keyframe import VideoKeyframeVersionDB
    from ....schemas.video.video_generation import VideoGenerationVersionDB
    from ....schemas.video.video_character import VideoCharacterGenerationVersionDB


def _parse_aspect_ratio(value: Optional[str]) -> Optional[AspectRatio]:
    if not value:
        return None
    v = (value or "").strip().lower()
    if v in ("16:9", "landscape"):
        return AspectRatio.LANDSCAPE
    if v in ("9:16", "portrait"):
        return AspectRatio.PORTRAIT
    if v in ("1:1", "square"):
        return AspectRatio.SQUARE
    try:
        return AspectRatio(v)
    except ValueError:
        return None


def _parse_resolution(value: Optional[str]) -> Optional[Resolution]:
    if not value:
        return None
    v = (value or "").strip().lower()
    if "480" in v or v == "480p":
        return Resolution.P480
    if "720" in v or v == "720p":
        return Resolution.P720
    if "1080" in v or v == "1080p":
        return Resolution.P1080
    try:
        return Resolution(v)
    except ValueError:
        return None


def _parse_image_tool(value: Optional[str], default: ImageGenerationTool) -> ImageGenerationTool:
    """只按列 image_generation_tool 解析，缺或无效用 default。"""
    if not value:
        return default
    try:
        return ImageGenerationTool((value or "").strip().lower())
    except ValueError:
        return default


def _parse_video_tool(value: Optional[str], default: VideoGenerationTool) -> VideoGenerationTool:
    """只按列 video_generation_tool 解析，缺或无效用 default。"""
    if not value:
        return default
    try:
        return VideoGenerationTool((value or "").strip().lower())
    except ValueError:
        return default


def rebuild_user_option_from_keyframe_version(
    version_db: "VideoKeyframeVersionDB",
    fallback: Optional[UserOption] = None,
) -> UserOption:
    """只读版本表列，不按 provider 推导。"""
    default = fallback or UserOption.default()
    image_tool = _parse_image_tool(
        getattr(version_db, "image_generation_tool", None),
        default.image_generation_tool,
    )
    aspect_ratio = _parse_aspect_ratio(getattr(version_db, "aspect_ratio", None)) or default.aspect_ratio
    resolution = _parse_resolution(getattr(version_db, "resolution", None)) or default.resolution
    return UserOption(
        image_generation_tool=image_tool,
        video_generation_tool=default.video_generation_tool,
        lipsync_video_tool=default.lipsync_video_tool,
        mode=default.mode,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration=default.duration,
        lipsync_coverage=default.lipsync_coverage,
        content_category=default.content_category,
        enable_continuity_mode=default.enable_continuity_mode,
        enable_keyframe_reflection=default.enable_keyframe_reflection,
        max_reflection_iterations=default.max_reflection_iterations,
        reflection_threshold=default.reflection_threshold,
        reflection_concurrency=default.reflection_concurrency,
        full_auto=default.full_auto,
    )


def rebuild_user_option_from_video_version(
    version_db: "VideoGenerationVersionDB",
    fallback: Optional[UserOption] = None,
) -> UserOption:
    """只读版本表列，不按 provider 推导。"""
    default = fallback or UserOption.default()
    video_tool = _parse_video_tool(
        getattr(version_db, "video_generation_tool", None),
        default.video_generation_tool,
    )
    aspect_ratio = _parse_aspect_ratio(getattr(version_db, "aspect_ratio", None)) or default.aspect_ratio
    resolution = _parse_resolution(getattr(version_db, "resolution", None)) or default.resolution
    # UserOption.duration = 用户设定的整片目标时长（如 30 秒）；version_db.duration = 单段生成视频的时长，二者语义不同，不从这里读
    # 若该版本为 lipsync 镜头，用版本记录的 video_generation_tool 作为口型模型偏好（仅当属于口型支持列表时）
    lipsync_tool = default.lipsync_video_tool
    if getattr(version_db, "generation_mode", None) == "lipsync":
        _vt = _parse_video_tool(getattr(version_db, "video_generation_tool", None), default.lipsync_video_tool)
        if _vt in LIPSYNC_CAPABLE_VIDEO_TOOLS:
            lipsync_tool = _vt
    return UserOption(
        image_generation_tool=default.image_generation_tool,
        video_generation_tool=video_tool,
        lipsync_video_tool=lipsync_tool,
        mode=default.mode,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration=default.duration,
        lipsync_coverage=default.lipsync_coverage,
        content_category=default.content_category,
        enable_continuity_mode=default.enable_continuity_mode,
        enable_keyframe_reflection=default.enable_keyframe_reflection,
        max_reflection_iterations=default.max_reflection_iterations,
        reflection_threshold=default.reflection_threshold,
        reflection_concurrency=default.reflection_concurrency,
        full_auto=default.full_auto,
    )


def rebuild_user_option_from_character_version(
    version_db: "VideoCharacterGenerationVersionDB",
    fallback: Optional[UserOption] = None,
) -> UserOption:
    """只读版本表列，不按 provider 推导。"""
    default = fallback or UserOption.default()
    image_tool = _parse_image_tool(
        getattr(version_db, "image_generation_tool", None),
        default.image_generation_tool,
    )
    aspect_ratio = _parse_aspect_ratio(getattr(version_db, "aspect_ratio", None)) or default.aspect_ratio
    resolution = _parse_resolution(getattr(version_db, "resolution", None)) or default.resolution
    return UserOption(
        image_generation_tool=image_tool,
        video_generation_tool=default.video_generation_tool,
        lipsync_video_tool=default.lipsync_video_tool,
        mode=default.mode,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration=default.duration,
        lipsync_coverage=default.lipsync_coverage,
        content_category=default.content_category,
        enable_continuity_mode=default.enable_continuity_mode,
        enable_keyframe_reflection=default.enable_keyframe_reflection,
        max_reflection_iterations=default.max_reflection_iterations,
        reflection_threshold=default.reflection_threshold,
        reflection_concurrency=default.reflection_concurrency,
        full_auto=default.full_auto,
    )
