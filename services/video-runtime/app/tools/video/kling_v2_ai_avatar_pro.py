"""
WaveSpeed Kwaivgi Kling V2 AI Avatar Pro — 口型镜头：audio + image → talking-head video。

API：POST kwaivgi/kling-v2-ai-avatar-pro；仅 image、audio、prompt（可选）。成片时长随音频；计费按音频秒数，不足 5s 按 5s（与 WaveSpeed 文档一致）。
输出分辨率随宽高比约为 16:9→1920×1072、1:1→1440×1440、9:16→1072×1920（与 TARGET 偏差由 ensure_on_s3 归一）。
"""
import logging
from typing import Optional, Annotated, Any

from pydantic import BaseModel, Field, SkipValidation
from pydantic.json_schema import SkipJsonSchema
from langchain_core.tools import tool
from langchain.tools import ToolRuntime

from ...llm.wavespeed_service import get_wavespeed_service
from ...models.image_result import VideoGenerationResult, VideoProvider
from ...models.tool_enums import ToolName, ToolType, ToolProvider, DefaultValues, Resolution, TARGET_PIXELS
from ...services.account.account_router import get_account_router
from ..context_schemas import VideoGenerationContext

logger = logging.getLogger(__name__)


class KlingV2AiAvatarProInput(BaseModel):
    """Kling V2 AI Avatar Pro：与 LTX lipsync 一致，audio_url 从 context 读取。"""
    i2v_prompt: str = Field(
        description="Style/motion hint for logging; optional prompt sent to API when non-empty."
    )
    start_image_url: str = Field(
        description="Portrait image URL (required for avatar)."
    )
    duration: int = Field(
        default=5,
        description="Segment duration in seconds (for billing). Actual video length follows audio.",
    )
    audio_url: Optional[str] = Field(
        default=None,
        description="Audio URL; when provided via context, overrides this.",
    )
    runtime: Annotated[Any, SkipValidation, SkipJsonSchema()] = Field(
        default=None,
        description="ToolRuntime injected by LangGraph (internal use only)",
    )


@tool(ToolName.LIPSYNC_KLING_V2_AI_AVATAR_PRO, args_schema=KlingV2AiAvatarProInput)
async def generate_video_with_wavespeed_kling_v2_ai_avatar_pro(
    i2v_prompt: str,
    start_image_url: str,
    runtime: ToolRuntime[VideoGenerationContext],
    duration: int = 5,
    audio_url: Optional[str] = None,
) -> VideoGenerationResult:
    """Generate lipsync avatar video using Kling V2 AI Avatar Pro (image + audio).

    Billing uses audio duration (min 5s billable). Resolution from user option is applied downstream via ensure_on_s3.
    """
    try:
        resolution = runtime.context.resolution.value if runtime.context.resolution else DefaultValues.VIDEO_RESOLUTION.value
        ar_ctx = runtime.context.aspect_ratio if runtime.context.aspect_ratio else DefaultValues.VIDEO_ASPECT_RATIO
        try:
            res_enum = Resolution(resolution)
        except ValueError:
            res_enum = DefaultValues.VIDEO_RESOLUTION
        tw, th = TARGET_PIXELS.get((res_enum, ar_ctx), (1920, 1080))

        final_start_image_url = runtime.context.start_image_url if runtime.context.start_image_url else start_image_url
        final_audio_url = runtime.context.audio_url if runtime.context.audio_url else audio_url
        duration_sec = runtime.context.duration if runtime.context.duration else duration

        if not final_audio_url:
            logger.error("❌ Kling V2 AI Avatar Pro 需要 audio_url（context 或参数）")
            return VideoGenerationResult.error_result(
                error_message="Kling V2 AI Avatar Pro requires audio_url.",
                provider=VideoProvider.WAVESPEED.value,
            )
        if not final_start_image_url:
            logger.error("❌ Kling V2 AI Avatar Pro 需要 start_image_url")
            return VideoGenerationResult.error_result(
                error_message="Kling V2 AI Avatar Pro requires start_image_url.",
                provider=VideoProvider.WAVESPEED.value,
            )

        prompt = (i2v_prompt or "").strip() or None
        logger.info(
            "🎭 Kling V2 AI Avatar Pro 开始: target_res=%s, image=%s, prompt=%s",
            resolution,
            bool(final_start_image_url),
            bool(prompt),
        )

        async def _make_request(api_key: str):
            svc = get_wavespeed_service()
            svc.api_key = api_key
            request_id = await svc.create_kling_v2_ai_avatar_pro_task(
                audio_url=final_audio_url,
                image_url=final_start_image_url,
                prompt=prompt,
            )
            return await svc.poll_kling_v2_ai_avatar_pro_until_complete(
                request_id, final_audio_url, target_width=tw, target_height=th
            )

        tool_type = ToolType.KLING_V2_AI_AVATAR_PRO
        router = await get_account_router()
        result = await router.route_tool_request(
            provider=ToolProvider.WAVESPEED,
            tool_type=tool_type,
            request_func=_make_request,
        )

        if result.success:
            result = result.model_copy(update={
                "model": tool_type.value,
                "resolution": resolution,
            })
            from ...services.tool_service import ToolService
            cost = ToolService.calculate_cost(
                cost_type=tool_type,
                duration=duration_sec,
            )
            if cost > 0:
                ToolService.log_cost(cost)
                logger.info(f"💰 Kling V2 AI Avatar Pro 成本: ${cost:.6f} (billable from {duration_sec}s)")
                cb = ToolService.get_credit_callback(getattr(runtime, "config", None) if runtime else None)
                if cb:
                    cb.add_tool_cost(cost, tool_name=ToolName.LIPSYNC_KLING_V2_AI_AVATAR_PRO.value, tool_type=tool_type)
            result = result.model_copy(update={"billing_cost": cost})
            logger.info(f"✅ Kling V2 AI Avatar Pro 成功: {result.video_url}")
            return result
        logger.error(f"❌ Kling V2 AI Avatar Pro 失败: {result.message}")
        return result
    except Exception as e:
        logger.error(f"❌ Kling V2 AI Avatar Pro 异常: {str(e)}")
        return VideoGenerationResult.error_result(
            error_message=f"Exception occurred: {str(e)}",
            provider=VideoProvider.WAVESPEED.value,
        )
