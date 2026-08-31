"""
Per-shot `generation_routing` 与用户全局 `UserOption` 合并（keyframe / 普通视频 / 口型视频执行前）。

仅依赖 `user_options` 枚举，不依赖 prompts / LLM schema，便于单测与复用。
"""
import logging
from typing import Any, Literal, Optional

from ....models.user_options import ImageGenerationTool, UserOption, VideoGenerationTool

logger = logging.getLogger(__name__)

RoutingAspect = Literal["image", "lipsync_video", "normal_video", "all"]


def _routing_dict_from_shot(shot: Any) -> Optional[dict]:
    gr = getattr(shot, "generation_routing", None)
    if isinstance(gr, dict) and gr:
        return gr
    return None


def resolve_user_option_for_shot(
    user_option: Optional[UserOption],
    shot: Any,
    routing_aspect: RoutingAspect = "all",
) -> Optional[UserOption]:
    """有 `shot.generation_routing` 则把其中工具覆盖合并进 `UserOption` 副本；无路由或空 → 返回原对象。

    某工具键缺省或空串 → 不覆盖该模态；值为 auto → 设为对应枚举 AUTO，由 Wrapper 展开。

    **仅当用户在该模态上选的是 `auto` 时**才采纳 per-shot 路由里的对应字段；全局已指定具体模型时不合并（避免路由 LLM 覆盖用户选择）。
    """
    if not user_option or not shot:
        return user_option
    routing = _routing_dict_from_shot(shot)
    if not isinstance(routing, dict):
        return user_option
    updates = {}
    if routing_aspect in ("all", "lipsync_video"):
        if user_option.lipsync_video_tool == VideoGenerationTool.AUTO:
            lt = routing.get("lipsync_video_tool")
            if lt is not None and str(lt).strip() != "":
                s = str(lt).strip().lower()
                if s == VideoGenerationTool.AUTO.value:
                    updates["lipsync_video_tool"] = VideoGenerationTool.AUTO
                else:
                    try:
                        updates["lipsync_video_tool"] = VideoGenerationTool(s)
                    except ValueError:
                        logger.warning(
                            "[resolve_user_option_for_shot] lipsync_diag routing value not enum shot_uuid=%s raw=%r parsed_skip=True",
                            getattr(shot, "uuid", None),
                            lt,
                        )
        elif routing_aspect == "lipsync_video":
            logger.info(
                "[resolve_user_option_for_shot] lipsync_diag merge_skip shot_uuid=%s "
                "global_lipsync=%s (not auto, per-shot routing ignored) routing_lipsync=%r gm=%s",
                getattr(shot, "uuid", None),
                getattr(user_option.lipsync_video_tool, "value", user_option.lipsync_video_tool),
                routing.get("lipsync_video_tool"),
                getattr(shot, "generation_mode", None),
            )
    if routing_aspect in ("all", "image"):
        if user_option.image_generation_tool == ImageGenerationTool.AUTO:
            it = routing.get("image_generation_tool")
            if it is not None and str(it).strip() != "":
                s = str(it).strip().lower()
                if s == ImageGenerationTool.AUTO.value:
                    updates["image_generation_tool"] = ImageGenerationTool.AUTO
                else:
                    try:
                        updates["image_generation_tool"] = ImageGenerationTool(s)
                    except ValueError:
                        pass
    if routing_aspect in ("all", "normal_video"):
        if user_option.video_generation_tool == VideoGenerationTool.AUTO:
            vt = routing.get("normal_video_tool")
            if vt is not None and str(vt).strip() != "":
                s = str(vt).strip().lower()
                if s == VideoGenerationTool.AUTO.value:
                    updates["video_generation_tool"] = VideoGenerationTool.AUTO
                else:
                    try:
                        updates["video_generation_tool"] = VideoGenerationTool(s)
                    except ValueError:
                        pass
    if not updates:
        if routing_aspect == "lipsync_video" and user_option.lipsync_video_tool == VideoGenerationTool.AUTO:
            logger.info(
                "[resolve_user_option_for_shot] lipsync_diag merge_skip shot_uuid=%s "
                "global_lipsync=auto routing_lipsync=%r generation_routing_keys=%s gm=%s (no_update)",
                getattr(shot, "uuid", None),
                (routing or {}).get("lipsync_video_tool"),
                list((routing or {}).keys()),
                getattr(shot, "generation_mode", None),
            )
        return user_option
    if "lipsync_video_tool" in updates:
        logger.info(
            "[resolve_user_option_for_shot] lipsync_diag merged shot_uuid=%s "
            "global_lipsync=auto routing_lipsync=%r -> eff_lipsync=%s generation_mode=%s",
            getattr(shot, "uuid", None),
            (routing or {}).get("lipsync_video_tool"),
            getattr(updates["lipsync_video_tool"], "value", updates["lipsync_video_tool"]),
            getattr(shot, "generation_mode", None),
        )
    return user_option.model_copy(update=updates, deep=True)
