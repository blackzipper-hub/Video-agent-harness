"""
ByteDance Seedream 图像生成工具（通过 WaveSpeed API）
"""
import logging
from typing import List, Annotated, Any, Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from ....models.tool_enums import ToolMode, ToolType, ToolProvider
    from ....services.tool_service import ToolInfo
    from ....models.user_options import UserOption
from pydantic import BaseModel, Field, SkipValidation
from pydantic.json_schema import SkipJsonSchema
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langsmith import traceable

from ...llm.wavespeed_service import WaveSpeedService
from ...models.image_result import ImageGenerationResult
from ..context_schemas import ImageGenerationContext
from ...services.tool_service import ToolService
from ...models.tool_enums import ToolProvider, ToolType, ToolName, Resolution, AspectRatio, TARGET_PIXELS
from ...services.account.account_router import get_account_router

logger = logging.getLogger(__name__)


# Seedream API 要求最小像素数；用户要的 resolution+aspect_ratio 通过「本函数返回 API size + 下采样到 TARGET_PIXELS」两步满足
SEEDREAM_MIN_PIXELS = 3_686_400  # 2560*1440, 1440*2560, 1920*1920


def convert_to_seedream_size(
    aspect_ratio: Union["AspectRatio", str],
    resolution: Union["Resolution", str],
) -> str:
    """将用户要求的 aspect_ratio + resolution 转为 Seedream API 的 size。用户要求必须满足，在 API 最小像素限制下的做法如下。

    用户要的是：resolution（如 480p/720p/1080p）+ aspect_ratio（如 16:9/9:16/1:1）。
    API 限制：输出 ≥ 3,686,400 像素，且不能按「分辨率档位」指定，只能按宽高传 W*H。

    在满足 API 的前提下满足用户的做法：
    1. 按用户的 aspect_ratio 向 API 请求「该比例下满足最小 3,686,400 像素」的一档（如 16:9 → 2560*1440）。
    2. 拿到图后按 TARGET_PIXELS[(resolution, aspect_ratio)] 下采样（如 480p 16:9 → 854*480，1080p 16:9 → 1920*1080）。
    这样最终给用户的图 = 用户要的 resolution + aspect_ratio，两者都满足。

    Args:
        aspect_ratio: 用户宽高比，AspectRatio 或 "16:9"/"9:16"/"1:1"
        resolution: 用户分辨率，Resolution 或 "480p"/"720p"/"1080p"（与 aspect_ratio 一起决定下采样目标 TARGET_PIXELS）

    Returns:
        str: 本步传给 API 的 size，如 "2560*1440", "1440*2560", "1920*1920"
    """
    if isinstance(aspect_ratio, AspectRatio):
        ar = aspect_ratio
    else:
        try:
            ar = AspectRatio((aspect_ratio or "").strip())
        except (ValueError, TypeError):
            ar = AspectRatio.LANDSCAPE
    if ar == AspectRatio.PORTRAIT:
        return "1440*2560"   # 9:16, 3,686,400
    if ar == AspectRatio.SQUARE:
        return "1920*1920"   # 1:1, 3,686,400
    # LANDSCAPE (16:9) 或默认
    return "2560*1440"       # 16:9, 3,686,400


class SeedreamInput(BaseModel):
    """Input schema for ByteDance Seedream image editing."""
    prompt: str = Field(
        description="Detailed editing instruction prompt. Describe what changes you want to make to the image. For example: 'Keep the model's pose and the flowing shape of the liquid clothing unchanged. Change the clothing material from silver metal to completely transparent clear water (or glass).'"
    )
    images: List[str] = Field(
        description="List of input image URLs to be edited. Currently supports single image editing."
    )
    runtime: Annotated[Any, SkipValidation, SkipJsonSchema()] = Field(
        default=None,
        description="ToolRuntime injected by LangGraph (internal use only)"
    )


@tool(ToolName.SEEDREAM_I2I, args_schema=SeedreamInput)
@traceable(run_type='llm')
async def edit_image_with_wavespeed_seedream(
    prompt: str,
    images: List[str],
    runtime: ToolRuntime[ImageGenerationContext]
) -> ImageGenerationResult:
    """Professional ByteDance Seedream v4.5 image editing tool (via WaveSpeed API).
    
    This tool provides high-quality image editing capabilities using ByteDance's Seedream v4.5 model.
    Perfect for complex image transformations and creative edits while maintaining composition and structure.
    
    🎯 Key Features:
    1. Advanced image editing with natural language instructions
    2. High-quality output with preserved image composition
    3. Support for complex transformations (material changes, lighting effects, etc.)
    4. Maintains pose and structure while transforming details
    
    Parameters:
    - prompt: Detailed editing instruction describing the desired changes
      Examples: 
        - "Keep the model's pose unchanged. Change the clothing material from silver to glass."
        - "Transform the liquid flowing effect from metallic to water with transparency"
    - images: List of input image URLs (currently supports single image)
    
    Editing Examples:
    - Material transformation: "Change metal clothing to transparent water or glass"
    - Lighting effects: "Change light and shadow from reflection to refraction"
    - Texture modifications: "Transform solid material to liquid flowing effect"
    - Transparency effects: "Make objects semi-transparent with visible details underneath"
    - Surface properties: "Change from opaque to transparent, showing skin details through liquid"
    
    Returns: ImageGenerationResult object containing the editing results"""
    try:
        # 从 runtime.context 获取 aspect_ratio 和 resolution
        # 从 runtime.context 获取 aspect_ratio 和 resolution（使用 enum）
        from ...models.tool_enums import DefaultValues
        
        aspect_ratio = runtime.context.aspect_ratio.value if runtime.context.aspect_ratio else DefaultValues.IMAGE_ASPECT_RATIO.value
        resolution = runtime.context.resolution.value if runtime.context.resolution else DefaultValues.IMAGE_RESOLUTION.value
        
        # 🎯 优先使用 runtime.context 中的 reference_image_urls（代码层面传入更准确）
        # 如果 runtime.context 中没有，则使用 LLM 传入的参数
        if runtime.context.reference_image_urls:
            logger.info(f"🎯 使用 runtime.context 中的 reference_image_urls（代码层面传入）: {len(runtime.context.reference_image_urls)} 张")
            final_images = runtime.context.reference_image_urls
        else:
            logger.info(f"📝 使用 LLM 传入的 images: {len(images)} 张")
            final_images = images

        # 按模型能力截断参考图（上游 keyframe 已人物优先排序）
        from .ref_utils import truncate_reference_urls_for_model
        truncated = truncate_reference_urls_for_model(final_images, ToolType.SEEDREAM_V4_5)
        final_images = truncated if truncated is not None else final_images
        
        # 用户 resolution + aspect_ratio：API 请求用 convert_to_seedream_size，下采样目标用 TARGET_PIXELS，最终输出即用户所要
        size = convert_to_seedream_size(aspect_ratio, resolution)
        try:
            res_enum = Resolution(resolution) if isinstance(resolution, str) else resolution
            ar_enum = AspectRatio(aspect_ratio) if isinstance(aspect_ratio, str) else aspect_ratio
            target = TARGET_PIXELS.get((res_enum, ar_enum))
        except (ValueError, TypeError):
            target = None
        target_w, target_h = (target[0], target[1]) if target else (None, None)

        logger.info(f"🖼️ ByteDance Seedream 图像编辑开始")
        logger.info(f"🖼️ 编辑指令: {prompt[:100]}...")
        logger.info(f"🖼️ 输入图片数量: {len(final_images)}")
        logger.info(f"🖼️ 宽高比: {aspect_ratio}, 分辨率: {resolution} -> API size: {size}, 下采样目标: {f'{target_w}x{target_h}' if target else '无'}")

        if not final_images:
            raise ValueError("至少需要提供一张输入图片")

        # 通过账号路由器调用（走 Redis 限流）；下采样在 WaveSpeed 内完成
        async def _make_request(api_key: str):
            svc = WaveSpeedService()
            svc.api_key = api_key
            return await svc.edit_image_seedream(
                prompt=prompt,
                images=final_images,
                size=size,
                target_width=target_w,
                target_height=target_h,
            )
        router = await get_account_router()
        result = await router.route_tool_request(
            provider=ToolProvider.WAVESPEED,
            tool_type=ToolType.SEEDREAM_V4_5,
            request_func=_make_request
        )
        
        if result.success:
            logger.info(f"✅ ByteDance Seedream 图像编辑成功: {result.image_url}")
            # I2I 人物一致性校验与重试由 wrapper (_run_i2i_one_model) 统一处理，此处不再重复
            # ⭐ 计算成本：$0.04 per generated image（简单，无关参数）
            from ...services.tool_service import ToolService
            cost = ToolService.calculate_cost(
                cost_type=ToolType.SEEDREAM_V4_5,
                output_image_count=1
            )
            if cost > 0:
                ToolService.log_cost(cost)
                logger.info(f"💰 ByteDance Seedream 成本: ${cost:.6f}")
                cb = ToolService.get_credit_callback(getattr(runtime, "config", None) if runtime else None)
                if cb:
                    cb.add_tool_cost(cost, tool_name=ToolName.SEEDREAM_I2I.value, tool_type=ToolType.SEEDREAM_V4_5)
            
            # 与 nano_banana 一致：成功时也带上 aspect_ratio、resolution、model，供 character version 等落库
            data = result.model_dump()
            data.update(aspect_ratio=aspect_ratio, resolution=resolution, model=ToolType.SEEDREAM_V4_5.value, billing_cost=cost)
            return ImageGenerationResult(**data)
        else:
            logger.error(f"❌ ByteDance Seedream 图像编辑失败: {result.message}")
            return result
            
    except Exception as e:
        logger.error(f"❌ ByteDance Seedream 图像编辑异常: {str(e)}")
        result = ImageGenerationResult.error_result(
            error_message=f"ByteDance Seedream 图像编辑异常: {str(e)}",
            provider=ToolProvider.WAVESPEED.value,
            generated_prompt=prompt,
            reference_image_urls=final_images if 'final_images' in locals() else images,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            model=ToolType.SEEDREAM_V4_5.value,
            raw_error_msg=str(e)
        )
        return result


class SeedreamT2IInput(BaseModel):
    """Input schema for ByteDance Seedream text-to-image generation."""
    prompt: str = Field(
        description="Detailed image description prompt. Describe the image content, style, composition, scene environment, etc. For example: 'Nighttime outdoor photoshoot: A young man stands inside a public phone booth, holding a blue phone receiver to his ear.'"
    )
    runtime: Annotated[Any, SkipValidation, SkipJsonSchema()] = Field(
        default=None,
        description="ToolRuntime injected by LangGraph (internal use only)"
    )


@tool(ToolName.SEEDREAM_T2I, args_schema=SeedreamT2IInput)
@traceable(run_type='llm')
async def generate_image_with_wavespeed_seedream_t2i(
    prompt: str,
    runtime: ToolRuntime[ImageGenerationContext]
) -> ImageGenerationResult:
    """Professional ByteDance Seedream v4.5 image generation tool (via WaveSpeed API) - T2I text-to-image.
    
    This tool provides high-quality text-to-image generation using ByteDance's Seedream v4.5 model.
    Perfect for creating images from detailed text descriptions without requiring reference images.
    
    🎯 Key Features:
    1. Advanced text-to-image generation with natural language prompts
    2. High-quality output with detailed image generation
    3. Support for various artistic styles and compositions
    4. Flexible output size control
    
    Parameters:
    - prompt: Detailed image description prompt describing the desired image content, style, composition, etc.
    
    Prompt Examples:
    - Character scenes: "Nighttime outdoor photoshoot: A young man stands inside a public phone booth"
    - Landscapes: "A serene mountain lake at sunset with reflection of snow-capped peaks"
    - Artistic styles: "A cat sitting on a windowsill, watercolor painting style"
    - Fantasy scenes: "A magical forest with glowing mushrooms and fairy lights"
    
    Returns: ImageGenerationResult object containing the generation results"""
    try:
        # 从 runtime.context 获取 aspect_ratio 和 resolution
        # 从 runtime.context 获取 aspect_ratio 和 resolution（使用 enum）
        from ...models.tool_enums import DefaultValues
        
        aspect_ratio = runtime.context.aspect_ratio.value if runtime.context.aspect_ratio else DefaultValues.IMAGE_ASPECT_RATIO.value
        resolution = runtime.context.resolution.value if runtime.context.resolution else DefaultValues.IMAGE_RESOLUTION.value
        
        # 用户 resolution + aspect_ratio：API 请求用 convert_to_seedream_size，下采样目标用 TARGET_PIXELS，最终输出即用户所要
        size = convert_to_seedream_size(aspect_ratio, resolution)
        try:
            res_enum = Resolution(resolution) if isinstance(resolution, str) else resolution
            ar_enum = AspectRatio(aspect_ratio) if isinstance(aspect_ratio, str) else aspect_ratio
            target = TARGET_PIXELS.get((res_enum, ar_enum))
        except (ValueError, TypeError):
            target = None
        target_w, target_h = (target[0], target[1]) if target else (None, None)

        logger.info(f"🎨 ByteDance Seedream T2I 图像生成开始")
        logger.info(f"🎨 生成提示词: {prompt[:100]}...")
        logger.info(f"🎨 宽高比: {aspect_ratio}, 分辨率: {resolution} -> API size: {size}, 下采样目标: {f'{target_w}x{target_h}' if target else '无'}")

        # 通过账号路由器调用（走 Redis 限流）；下采样在 WaveSpeed 内完成
        async def _make_request(api_key: str):
            svc = WaveSpeedService()
            svc.api_key = api_key
            return await svc.generate_image_seedream_t2i(
                prompt=prompt,
                size=size,
                target_width=target_w,
                target_height=target_h,
            )
        router = await get_account_router()
        result = await router.route_tool_request(
            provider=ToolProvider.WAVESPEED,
            tool_type=ToolType.SEEDREAM_V4_5,
            request_func=_make_request
        )
        
        if result.success:
            logger.info(f"✅ ByteDance Seedream T2I 图像生成成功: {result.image_url}")
            
            # ⭐ 计算成本：$0.04 per generated image（简单，无关参数）
            cost = ToolService.calculate_cost(
                cost_type=ToolType.SEEDREAM_V4_5,
                output_image_count=1
            )
            if cost > 0:
                ToolService.log_cost(cost)
                logger.info(f"💰 ByteDance Seedream T2I 成本: ${cost:.6f}")
                cb = ToolService.get_credit_callback(getattr(runtime, "config", None) if runtime else None)
                if cb:
                    cb.add_tool_cost(cost, tool_name=ToolName.SEEDREAM_T2I.value, tool_type=ToolType.SEEDREAM_V4_5)
            
            # 与 nano_banana 一致：成功时也带上 aspect_ratio、resolution、model，供 character version 等落库
            data = result.model_dump()
            data.update(aspect_ratio=aspect_ratio, resolution=resolution, model=ToolType.SEEDREAM_V4_5.value, billing_cost=cost)
            return ImageGenerationResult(**data)
        else:
            logger.error(f"❌ ByteDance Seedream T2I 图像生成失败: {result.message}")
            return result
            
    except Exception as e:
        logger.error(f"❌ ByteDance Seedream T2I 图像生成异常: {str(e)}")
        result = ImageGenerationResult.error_result(
            error_message=f"ByteDance Seedream T2I 图像生成异常: {str(e)}",
            provider=ToolProvider.WAVESPEED.value,
            generated_prompt=prompt,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            model=ToolType.SEEDREAM_V4_5.value,
            raw_error_msg=str(e)
        )
        return result
