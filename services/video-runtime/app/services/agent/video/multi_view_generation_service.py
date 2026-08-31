"""
多视角参考图生成服务

功能：
- 为视觉元素（角色、物品、场所）生成多视角参考图（Character Sheet）
- 单张图片包含多个视角：正面、背面、侧面、3/4视角等
- 基于主图使用i2i模式生成，确保风格一致性
"""

import logging
from typing import Tuple, Optional, List, cast, TYPE_CHECKING, Any
from langchain_core.messages import BaseMessage

from prompts.prompt_config import PromptName, PROMPTS_CONFIG
from ..utils.llm_resilience import StructuredResilienceKind, ainvoke_structured_resilient
from ..utils.message_utils import patch_tool_metrics_from_last_tool_message
from langchain_core.messages import HumanMessage, SystemMessage
from app.orchestration.skills.prompt_context import (
    facts_human_message,
    skill_system_message,
)
from ....models.video_state import CharacterProfile, StoryOutline
from ....models.user_options import UserOption
from ....models.image_result import ImageGenerationResult
from ....services.tool_service import ToolService
from ....models.tool_enums import ToolMode, DefaultValues
from ....utils.i18n import get_i18n_message_async
from ...agent.utils.prompt_utils import attach_images_to_messages

if TYPE_CHECKING:
    from ....services.tool_service import ToolsInfo

logger = logging.getLogger(__name__)


async def build_prompt_for_multi_view_generation(
    element: CharacterProfile,
    main_image_url: str,
    reference_images: Optional[List],
    user_option: Optional[UserOption],
    story_outline: Optional[StoryOutline] = None,
    user_input: str = "",
    tools_info: Optional['ToolsInfo'] = None,
    detected_language: Optional[str] = None,
) -> List[BaseMessage]:
    """构建多视角图生成的 messages（纯 CPU 格式化，不做任何 I/O）"""
    from ....models.video_state import VisualElementType
    reference_image_urls = [main_image_url]
    if reference_images:
        for img in reference_images:
            img_url = img.get("url") if isinstance(img, dict) else (img.url if hasattr(img, 'url') else (img if isinstance(img, str) else None))
            if img_url and img_url != main_image_url:  # 避免重复添加主图
                reference_image_urls.append(img_url)
    
    has_reference_images = len(reference_image_urls) > 0
    
    # 如果没有传入 tools_info，则获取（多视角图总是有主图，所以总是 I2I）
    if tools_info is None:
        tools_info = ToolService.get_image_generation_tools(user_option, mode=ToolMode.I2I)
    
    tool_name = tools_info.primary_tool_name
    
    # 准备story_style（模板期望的变量名）
    story_style = None
    if story_outline:
        story_style = story_outline.style_guide if hasattr(story_outline, 'style_guide') else ""
    
    element_type_str = element.type.value if isinstance(element.type, VisualElementType) else str(element.type)

    system = skill_system_message(
        "character-multiview-tool-director",
        lead="Follow character-multiview-tool-director.",
    )
    facts = {
        "tool_name": tool_name,
        "element_name": element.name,
        "element_type": element_type_str,
        "description": element.description or "",
        "appearance": element.appearance or "",
        "style": element.style or "",
        "story_style": story_style or "",
        "user_input": user_input or "",
        "detected_language": detected_language or "en",
        "reference_image_urls": reference_image_urls,
        "reference_image_count": len(reference_image_urls),
        "has_reference_images": has_reference_images,
    }
    messages: List[BaseMessage] = [
        SystemMessage(content=system),
        HumanMessage(content=facts_human_message(facts)),
    ]
    # 强制语言：只对 SystemMessage 追加语言要求，使用 detected_language
    from ..utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
    apply_language_suffix_to_system_message_in_messages(messages, detected_language)
    
    # 附加图片作为参考（主图 + 用户上传的参考图）
    messages = attach_images_to_messages(
        messages=messages,
        image_urls=reference_image_urls,
        prepend_text=False,
        add_image_index_hint=True
    )
    
    return messages


async def generate_multi_view_character_sheet(
    element: CharacterProfile,
    main_image_url: str,
    story_outline: Optional[StoryOutline],
    user_option: Optional[UserOption],
    reference_images: Optional[List] = None,
    user_input: str = "",
    detected_language: Optional[str] = None,
) -> Tuple[ImageGenerationResult, List[BaseMessage]]:
    """生成多视角character sheet（一张图包含多个视角）
    
    Args:
        element: 角色/物品/场所信息
        main_image_url: 主图URL（作为i2i参考）
        story_outline: 故事大纲（用于风格一致性）
        user_option: 用户选项
        reference_images: 用户上传的原始参考图片（可选）
        user_input: 用户原始输入（可选）
        detected_language: 检测到的语言代码（ISO 639-1），用于国际化错误消息
        cached_prompt_template: 预加载的 prompt 模板
        cached_agent: 已废弃，保留仅为兼容调用签名
        cached_llm: 已废弃，保留仅为兼容调用签名
    
    Returns:
        Tuple[ImageGenerationResult, List[BaseMessage]]: (image_result, new_messages)
    """
    from ....tools.context_schemas import ImageGenerationContext

    # ⭐ 提前获取工具信息（在 try 块外，简化错误处理）
    logger.info(f"🎨 开始生成多视角参考图: {element.name} ({element.type})")
    
    # 收集所有参考图片URL：主图 + 用户上传的参考图
    reference_image_urls = [main_image_url]
    if reference_images:
        for img in reference_images:
            img_url = img.get("url") if isinstance(img, dict) else (img.url if hasattr(img, 'url') else (img if isinstance(img, str) else None))
            if img_url and img_url != main_image_url:
                reference_image_urls.append(img_url)
    
    # 多视角图总是有主图，所以总是使用 I2I 模式
    tools_info = ToolService.get_image_generation_tools(user_option, ToolMode.I2I)
    image_tools = tools_info.tool_objects
    tool_info = tools_info.tools[0] if tools_info.tools else None
    provider_enum = tool_info.provider if tool_info else None
    tool_type_enum = tool_info.tool_type if tool_info else None
    
    # 准备 context 信息（用于错误处理）
    aspect_ratio_enum = (
        user_option.aspect_ratio
        if user_option and user_option.aspect_ratio 
        else DefaultValues.IMAGE_ASPECT_RATIO
    )
    resolution_enum = (
        user_option.resolution
        if user_option and user_option.resolution 
        else DefaultValues.IMAGE_RESOLUTION
    )
    
    # 获取语言用于国际化
    lang = detected_language or "en"
    
    try:
        image_generation_messages = await build_prompt_for_multi_view_generation(
            element, main_image_url, reference_images,
            user_option, story_outline, user_input, tools_info=tools_info,
            detected_language=detected_language,
        )
        
        # 创建 context
        context = ImageGenerationContext(
            aspect_ratio=aspect_ratio_enum,
            resolution=resolution_enum,
            reference_image_urls=reference_image_urls if reference_image_urls else None,
            model=tool_type_enum,
            language=detected_language,  # 穿透到 tool runtime 供 i18n 使用
            skip_consistency_check=True,  # 角色图片生成：prompt 会主动改变角色形象，跳过一致性校验
        )

        inputs = {"messages": image_generation_messages}
        logger.info(f"🚀 开始调用agent生成多视角图...")
        result = await ainvoke_structured_resilient(
            kind=StructuredResilienceKind.CREATE_AGENT,
            prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_MAIN_CHARACTER_MULTI_VIEW_GENERATION],
            agent_inputs=inputs,
            agent_tools=image_tools,
            context_schema=ImageGenerationContext,
            agent_invoke_context=context,
            wrap_agent_parse_fallback=True,
            log_context={"phase": "multi_view_sheet", "element": element.name},
        )
        
        # ✅ 获取完整的 agent messages
        # 返回本次LLM调用的 input messages + output messages
        agent_full_messages = result.get("messages", [])
        input_message_count = len(image_generation_messages)
        output_messages = agent_full_messages[input_message_count:] if len(agent_full_messages) > input_message_count else []
        
        # 返回本次调用的 input messages + output messages
        all_messages = image_generation_messages + output_messages
        
        # 从structured_response获取结构化结果
        structured_response = result.get("structured_response")
        if structured_response:
            structured_response = patch_tool_metrics_from_last_tool_message(
                result.get("messages", []), structured_response
            )

        # ⭐ 检查structured_response是否存在
        if not structured_response:
            logger.error(f"❌ LLM生成多视角图 {element.name} 失败: LLM未返回有效的结构化响应")
            
            # 使用国际化错误消息
            error_message = await get_i18n_message_async(
                "character.multi_view_generation_failed",
                default="多视角图生成失败",
                lang=lang
            )
            
            # 返回错误结果，填充所有可用信息
            error_result = ImageGenerationResult.error_result(
                error_message=error_message,
                provider=provider_enum.value if provider_enum else None,
                generated_prompt=None,
                reference_image_urls=reference_image_urls if reference_image_urls else None,
                aspect_ratio=aspect_ratio_enum.value if aspect_ratio_enum else None,
                resolution=resolution_enum.value if resolution_enum else None,
                model=tool_type_enum.value if tool_type_enum else None,
                raw_error_msg="LLM未返回有效的结构化响应"
            )
            return error_result, all_messages
        
        image_result: ImageGenerationResult = cast(ImageGenerationResult, structured_response)
        
        # ⭐ 检查生成结果
        if image_result and image_result.success and image_result.image_url:
            logger.info(f"✅ LLM生成多视角图 {element.name} 完成，成功: {image_result.success}，返回 {len(all_messages)} 条消息")
        else:
            logger.warning(f"⚠️ LLM生成多视角图 {element.name} 失败: {image_result.error_msg if image_result else '未知错误'}")
        
        return image_result, all_messages
        
    except Exception as e:
        logger.error(f"❌ LLM生成多视角图 {element.name} 失败: {str(e)}")
        
        # 使用国际化错误消息（工具信息已在 try 块外获取）
        error_message = await get_i18n_message_async(
            "character.multi_view_generation_failed",
            default="多视角图生成失败",
            lang=lang
        )
        
        # 返回错误结果，填充所有可用信息
        error_result = ImageGenerationResult.error_result(
            error_message=error_message,
            provider=provider_enum.value if provider_enum else None,
            generated_prompt=None,
            reference_image_urls=reference_image_urls if reference_image_urls else None,
            aspect_ratio=aspect_ratio_enum.value if aspect_ratio_enum else None,
            resolution=resolution_enum.value if resolution_enum else None,
            model=tool_type_enum.value if tool_type_enum else None,
            raw_error_msg=str(e)
        )
        return error_result, []
