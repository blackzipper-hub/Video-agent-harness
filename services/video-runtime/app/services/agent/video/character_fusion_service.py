"""
角色融合图生成服务模块
根据模型能力和角色组合，生成多角色融合图（主图和multiview分开）
"""
import logging
import asyncio
from typing import List, Optional, Dict, Any, Union, Tuple
from langgraph.runtime import Runtime

from ....models.video_state import VideoAgentState, StoryboardScene, CharacterProfile
from ....models.user_options import UserOption
from ....exceptions import BusinessException, BusinessExceptionCode
from ..schemas import VideoContextSchema
from ....services.agent.utils.database_utils import get_scenes_from_db, get_characters_from_db
from ..utils.cancellation import raise_if_cancelled
from ....services.agent.base_agent import MessageType
from ....models.video_state import CharacterImageInfo
from ....models.image_result import ImageGenerationResult
from ....models.tool_enums import DefaultValues, ToolMode, ToolType
from ....services.tool_service import ToolService
from .keyframe_generation_service import ImageType
from .agent_video_constants import ENABLE_FUSION
from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)


def get_model_limit(user_option: Optional[UserOption]) -> int:
    """获取模型能力限制（与 get_image_generation_tools 选链一致，从 tools_info.tool_type 判断）"""
    tools_info = ToolService.get_image_generation_tools(user_option, mode=ToolMode.T2I)
    model = tools_info.tool_type if tools_info and tools_info.tools else None
    # Pro: 5 张；3.1 Flash Image 文档 4 张；其余 3 张
    if model == ToolType.GEMINI_3_PRO_IMAGE_PREVIEW:
        return 5
    if model == ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW:
        return 4
    return 3


def split_characters_by_model_limit(
    character_ids: List[str],
    model_limit: int
) -> List[List[str]]:
    """
    按模型能力直接分组
    
    策略：每model_limit个角色为一组
    例如：6个角色，model_limit=3 → [[1,2,3], [4,5,6]]
         5个角色，model_limit=3 → [[1,2,3], [4,5]]
         4个角色，model_limit=3 → [[1,2,3], [4]]
    """
    groups = []
    for i in range(0, len(character_ids), model_limit):
        groups.append(character_ids[i:i+model_limit])
    return groups


def generate_fusion_key(character_ids: List[str], image_type: ImageType) -> str:
    """生成融合图的唯一key（按排序后的ID）。character_ids 需为字符串列表。"""
    sorted_ids = sorted(str(c) for c in character_ids)
    return f"{image_type.value}_fusion_{'_'.join(sorted_ids)}"


def decide_fusion_combinations_for_scenes(
    scenes: List[StoryboardScene],
    model_limit: int,
    character_images: Optional[Dict[str, "CharacterImageInfo"]] = None,
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """
    为所有scenes决定需要生成哪些融合图（主图和multiview分开）
    
    策略：
    1. 找出超过模型限制的scenes
    2. 对每个scene的角色按顺序分组，每组model_limit个角色
    3. 主图和multiview分别融合（分开生成）
    4. 去重：相同的角色组合只生成一次
    5. multiview 组合仅在组内 ≥2 个角色拥有 multiview 图时才生成
    
    Returns:
        Tuple[main_fusion_combinations, multiview_fusion_combinations]:
        - main_fusion_combinations: 主图融合组合 {fusion_key: character_ids}
        - multiview_fusion_combinations: multiview融合组合 {fusion_key: character_ids}
    """
    main_fusion_combinations = {}
    multiview_fusion_combinations = {}
    
    for scene in scenes:
        if not scene.character_ids:
            continue
        
        character_count = len(scene.character_ids)
        
        # 如果超过模型限制，需要融合
        if character_count > model_limit:
            # 按顺序分组，每组model_limit个角色
            groups = split_characters_by_model_limit(scene.character_ids, model_limit)
            
            # 为每组生成主图融合图和multiview融合图
            for group in groups:
                if len(group) >= 2:  # 至少2个角色才融合
                    # 主图融合
                    main_fusion_key = generate_fusion_key(group, ImageType.MAIN)
                    main_fusion_combinations[main_fusion_key] = group
                    
                    # multiview融合：仅在组内 ≥2 个角色有 multiview 图时才生成
                    if character_images:
                        mv_count = sum(
                            1 for cid in group
                            if cid in character_images and getattr(character_images[cid], "multiview_url", None)
                        )
                        if mv_count < 2:
                            logger.debug(f"跳过 multiview 融合（组内仅 {mv_count} 个角色有 multiview 图）: {group}")
                            continue
                    multiview_fusion_key = generate_fusion_key(group, ImageType.MULTIVIEW)
                    multiview_fusion_combinations[multiview_fusion_key] = group
    
    return main_fusion_combinations, multiview_fusion_combinations


def _scene_to_fusion_brief_row(scene: StoryboardScene) -> Dict[str, Any]:
    return {
        "scene_id": getattr(scene, "id", None) or getattr(scene, "uuid", None),
        "title": getattr(scene, "title", "") or "",
        "character_ids": list(scene.character_ids or []),
        "character_count": len(scene.character_ids or []),
    }


def _character_to_fusion_brief_row(character: CharacterProfile) -> Dict[str, Any]:
    return {
        "id": character.id,
        "name": character.name,
        "type": getattr(character, "type", None),
    }


def _fusion_groups_to_combination_maps(
    art: Any,
    *,
    character_images: Optional[Dict[str, CharacterImageInfo]] = None,
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Convert fusion.json artifact to fusion_key → character_ids maps."""
    main: Dict[str, List[str]] = {}
    multiview: Dict[str, List[str]] = {}
    for group in getattr(art, "main_fusions", None) or []:
        ids = [str(c) for c in (group.character_ids or [])]
        if len(ids) < 2:
            continue
        key = (group.fusion_key or "").strip() or generate_fusion_key(ids, ImageType.MAIN)
        main[key] = ids
    for group in getattr(art, "multiview_fusions", None) or []:
        ids = [str(c) for c in (group.character_ids or [])]
        if len(ids) < 2:
            continue
        if character_images:
            mv_count = sum(
                1 for cid in ids
                if cid in character_images and getattr(character_images[cid], "multiview_url", None)
            )
            if mv_count < 2:
                continue
        key = (group.fusion_key or "").strip() or generate_fusion_key(ids, ImageType.MULTIVIEW)
        multiview[key] = ids
    return main, multiview


async def decide_fusion_combinations_via_deep_agent(
    *,
    scenes: List[StoryboardScene],
    characters_data: List[CharacterProfile],
    model_limit: int,
    character_images: Optional[Dict[str, CharacterImageInfo]] = None,
    thread_id: str = "",
    run_id: str = "",
    detected_language: Optional[str] = None,
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], List[BaseMessage]]:
    """Plan fusion groups via character_fusion deep-agent; falls back to Program rules."""
    try:
        import uuid as _uuid
        from app.services.agent.video.character_fusion_stage import (
            export_fusion_plan_inputs,
            generate_fusion_plan_via_deep_agent,
        )

        tid = thread_id or f"fusion_{_uuid.uuid4().hex[:8]}"
        rid = run_id or f"run_{_uuid.uuid4().hex[:8]}"
        scene_rows = [_scene_to_fusion_brief_row(s) for s in scenes if s.character_ids]
        char_rows = [_character_to_fusion_brief_row(c) for c in characters_data]
        paths = export_fusion_plan_inputs(
            thread_id=tid,
            run_id=rid,
            scenes=scene_rows,
            characters=char_rows,
            model_limit=model_limit,
        )
        art, msgs = await generate_fusion_plan_via_deep_agent(
            thread_id=tid,
            run_id=rid,
            input_paths=paths,
            detected_language=detected_language,
        )
        main, multiview = _fusion_groups_to_combination_maps(art, character_images=character_images)
        if not main and not multiview:
            logger.warning("🎨 fusion plan deep-agent returned empty groups; falling back to Program decide")
            main, multiview = decide_fusion_combinations_for_scenes(
                scenes, model_limit, character_images=character_images,
            )
        else:
            logger.info(
                "🎨 fusion plan (deep-agent): %d main, %d multiview groups",
                len(main),
                len(multiview),
            )
        return main, multiview, list(msgs or [])
    except Exception as e:
        logger.warning("🎨 fusion plan deep-agent failed, fallback to Program decide: %s", e)
        main, multiview = decide_fusion_combinations_for_scenes(
            scenes, model_limit, character_images=character_images,
        )
        return main, multiview, []


async def generate_character_fusion_image(
    character_ids: List[str],
    character_images: Dict[str, CharacterImageInfo],
    image_type: ImageType,  # ImageType.MAIN 或 ImageType.MULTIVIEW
    user_option: Optional[UserOption],
    story_outline: Optional[Any],
    characters_data: Optional[List[CharacterProfile]] = None,
    detected_language: Optional[str] = None
) -> Tuple[ImageGenerationResult, List[BaseMessage]]:
    """生成多角色融合图
    
    Args:
        character_ids: 需要融合的角色ID列表
        character_images: 角色图片信息字典
        image_type: ImageType.MAIN 或 ImageType.MULTIVIEW，决定使用主图还是multiview融合
        user_option: 用户选项
        story_outline: 故事大纲（可选）
        cached_prompt_template: 预加载的 prompt 模板（由调用方统一加载一次）
        characters_data: 角色数据列表（可选，用于获取角色名称）
    
    Returns:
        Tuple[ImageGenerationResult, List[BaseMessage]]: (融合图生成结果, messages)
    """
    from ....tools.context_schemas import ImageGenerationContext
    from ....models.tool_enums import ToolMode, DefaultValues
    from ....services.tool_service import ToolService
    from ..utils.prompt_utils import attach_images_to_messages
    from prompts.prompt_config import PROMPTS_CONFIG, PromptName
    from ..utils.llm_resilience import StructuredResilienceKind, ainvoke_structured_resilient
    from ..utils.message_utils import patch_tool_metrics_from_last_tool_message

    # ⭐ 提前获取工具信息（在 try 块外，简化错误处理）
    logger.info(f"🎨 开始生成融合图（{image_type.value}），角色数量: {len(character_ids)}")
    
    # 根据image_type收集图片
    reference_images = []
    character_names = []
    character_list = []
    
    for char_id in character_ids:
        char_info = character_images.get(char_id)
        if char_info:
            if image_type == ImageType.MAIN:
                # 使用主图
                image_url = char_info.main_image_url
                if not image_url:
                    logger.warning(f"角色{char_id}没有主图，跳过主图融合")
                    continue
            elif image_type == ImageType.MULTIVIEW:
                # 使用multiview
                image_url = char_info.multiview_url
                if not image_url:
                    logger.warning(f"角色{char_id}没有multiview，跳过multiview融合")
                    continue
            else:
                raise ValueError(f"不支持的image_type: {image_type}")
            
            if image_url:
                reference_images.append(image_url)
                # 获取角色名称
                char_name = char_id
                if characters_data:
                    for char in characters_data:
                        if char.id == char_id:
                            char_name = char.name
                            break
                character_names.append(char_name)
                character_list.append(f"- 角色{len(character_names)}: {char_name} (ID: {char_id})")
    
    if len(reference_images) < 2:
        raise ValueError(f"需要至少2张图片才能融合，当前只有{len(reference_images)}张")
    
    # 获取工具信息
    tools_info = ToolService.get_image_generation_tools(user_option, mode=ToolMode.I2I)
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
    
    # ⭐ 使用预加载的 prompt 模板构建 messages
    image_generation_messages = await build_prompt_for_character_fusion(
        character_ids=character_ids,
        character_names=character_names,
        character_list_str="\n".join(character_list),
        reference_images=reference_images,
        image_type=image_type,
        user_option=user_option,
        story_outline=story_outline,
        detected_language=detected_language
    )
    
    try:
        # 创建 context
        context = ImageGenerationContext(
            aspect_ratio=aspect_ratio_enum,
            resolution=resolution_enum,
            reference_image_urls=reference_images,
            model=tool_type_enum,
            language=detected_language,  # 穿透到 tool runtime 供 i18n 使用
            skip_consistency_check=True,  # 角色图片生成：prompt 会主动改变角色形象，跳过一致性校验
        )

        inputs = {"messages": image_generation_messages}
        result = await ainvoke_structured_resilient(
            kind=StructuredResilienceKind.CREATE_AGENT,
            prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_CHARACTER_FUSION_IMAGE_GENERATION],
            agent_inputs=inputs,
            agent_tools=image_tools,
            context_schema=ImageGenerationContext,
            agent_invoke_context=context,
            wrap_agent_parse_fallback=True,
            log_context={"phase": "character_fusion", "image_type": image_type.value},
        )
        
        # ✅ 获取完整的 agent messages
        agent_full_messages = result.get("messages", [])
        input_message_count = len(image_generation_messages)
        output_messages = agent_full_messages[input_message_count:] if len(agent_full_messages) > input_message_count else []
        all_messages = image_generation_messages + output_messages
        
        # 从structured_response获取结构化结果
        structured_response = result.get("structured_response")
        if structured_response:
            structured_response = patch_tool_metrics_from_last_tool_message(
                result.get("messages", []), structured_response
            )
        if not structured_response:
            logger.error(f"❌ 融合图生成失败: LLM未返回有效的结构化响应")
            error_result = ImageGenerationResult.error_result(
                error_message="融合图生成失败",
                provider=provider_enum.value if provider_enum else None,
                generated_prompt=None,
                reference_image_urls=reference_images if reference_images else None,
                aspect_ratio=aspect_ratio_enum.value if aspect_ratio_enum else None,
                resolution=resolution_enum.value if resolution_enum else None,
                model=tool_type_enum.value if tool_type_enum else None,
                raw_error_msg="LLM未返回有效的结构化响应"
            )
            return error_result, all_messages
        
        image_result: ImageGenerationResult = structured_response
        return image_result, all_messages
        
    except Exception as e:
        logger.error(f"❌ 融合图生成失败: {str(e)}")
        error_result = ImageGenerationResult.error_result(
            error_message="融合图生成失败",
            provider=provider_enum.value if provider_enum else None,
            generated_prompt=None,
            reference_image_urls=reference_images if reference_images else None,
            aspect_ratio=aspect_ratio_enum.value if aspect_ratio_enum else None,
            resolution=resolution_enum.value if resolution_enum else None,
            model=tool_type_enum.value if tool_type_enum else None,
            raw_error_msg=str(e)
        )
        return error_result, []


async def build_prompt_for_character_fusion(
    character_ids: List[str],
    character_names: List[str],
    character_list_str: str,
    reference_images: List[str],
    image_type: ImageType,
    user_option: Optional[UserOption],
    story_outline: Optional[Any] = None,
    detected_language: Optional[str] = None
) -> List:
    """构建角色融合图生成的 messages（纯 CPU 格式化，不做任何 I/O）"""
    from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
    from app.orchestration.skills.prompt_context import (
        facts_human_message,
        skill_system_message,
    )
    from ....services.tool_service import ToolService
    from ....models.tool_enums import ToolMode
    from ..utils.prompt_utils import attach_images_to_messages, apply_language_suffix_to_system_message_in_messages
    
    # 获取工具信息
    tools_info = ToolService.get_image_generation_tools(user_option, mode=ToolMode.I2I)
    tool_name = tools_info.primary_tool_name
    
    # 构建故事信息
    story_context = None
    if story_outline:
        story_context = f"{story_outline.title} - {story_outline.description}. 风格：{story_outline.style_guide}"
    
    # 获取图像生成指南
    tool_guide = ToolService.get_image_prompt_guide(user_option, ToolMode.I2I) if user_option else ""
    
    image_type_name = "main" if image_type == ImageType.MAIN else "multiview"

    system = skill_system_message(
        "character-fusion-image-tool-director",
        lead="Follow character-fusion-image-tool-director.",
    )
    facts = {
        "tool_name": tool_name,
        "image_type": image_type_name,
        "character_ids": character_ids,
        "character_names": character_names,
        "character_list": character_list_str,
        "character_count": len(character_ids),
        "reference_image_urls": reference_images or [],
        "reference_image_count": len(reference_images or []),
        "story_context": story_context or "",
        "tool_guide": tool_guide or "",
        "detected_language": detected_language or "en",
    }
    messages: List[BaseMessage] = [
        SystemMessage(content=system),
        HumanMessage(content=facts_human_message(facts)),
    ]
    
    # 强制语言：只对 SystemMessage 追加语言要求
    apply_language_suffix_to_system_message_in_messages(messages, detected_language)
    
    # 添加参考图片，自动添加图片索引提示
    if reference_images:
        messages = attach_images_to_messages(messages, reference_images, add_image_index_hint=True)
    
    return messages


async def _load_fusion_data_from_db(
    state: VideoAgentState,
    scene_uuids: List[str]
) -> Tuple[List[StoryboardScene], List[CharacterProfile], Dict[str, CharacterImageInfo], Optional[Any]]:
    """阶段1：从数据库加载融合图生成所需的数据（使用asyncpg CRUD）"""
    from ....services.agent.utils.database_utils import get_scenes_from_db, get_characters_from_db, get_story_outline_from_db
    from ....crud.video.video_character import get_character_multi_view_images_batch

    # 加载数据（CRUD内部使用asyncpg）
    scenes_data = await get_scenes_from_db(scene_uuids)
    if not scenes_data:
        return [], [], {}, None

    logger.info(f"📊 获取到 {len(scenes_data)} 个scenes，开始分析融合图需求")

    # 获取所有角色信息
    character_uuids = state.get("character_uuids", [])
    if not character_uuids:
        return scenes_data, [], {}, None

    user_id = state["user_id"]
    characters_data = await get_characters_from_db(character_uuids)
    logger.info(f"👥 获取到 {len(characters_data)} 个角色")

    # 批量获取多视角图最新版本（每个角色一条，带 multi_view_image_url）
    multiview_by_char = await get_character_multi_view_images_batch(character_uuids, user_id)

    # 构建CharacterImageInfo字典
    character_images: Dict[str, CharacterImageInfo] = {}
    for character in characters_data:
        char_info = CharacterImageInfo(
            character_id=character.id,
            main_image_url=character.character_image_url or None
        )
        version = multiview_by_char.get(character.id)
        if version and getattr(version, "multi_view_image_url", None):
            char_info.multiview_url = version.multi_view_image_url
        character_images[character.id] = char_info
    
    # 获取故事大纲（用于生成prompt）
    story_outline_uuid = state.get("story_outline_uuid")
    story_outline = None
    if story_outline_uuid:
        story_outline = await get_story_outline_from_db(story_outline_uuid)
    
    return scenes_data, characters_data, character_images, story_outline


async def _save_fusion_results_to_db(
    results: List[Any],
    fusion_tasks: List[Tuple[ImageType, str, List[str], Any]],
    state: VideoAgentState,
    user_option: Optional[UserOption],
    character_images: Dict[str, CharacterImageInfo]
) -> Tuple[int, int, List[BaseMessage]]:
    """阶段4：保存融合图结果到数据库并收集messages（使用asyncpg CRUD）"""
    from ....crud.video.video_character import create_character_fusion_image
    from ..utils.message_utils import extract_ai_message_json
    from ....models.tool_enums import DefaultValues, ToolMode
    
    saved_count = 0
    failed_count = 0
    all_messages = []
    
    # 准备默认值（从user_option获取，用于失败情况）
    default_aspect_ratio = user_option.aspect_ratio.value if user_option and user_option.aspect_ratio else DefaultValues.IMAGE_ASPECT_RATIO.value
    default_resolution = user_option.resolution.value if user_option and user_option.resolution else DefaultValues.IMAGE_RESOLUTION.value
    default_model = None
    default_provider = None
    if user_option:
        tools_info = ToolService.get_image_generation_tools(user_option, mode=ToolMode.I2I)
        tool_info = tools_info.tools[0] if tools_info.tools else None
        if tool_info:
            default_model = tool_info.tool_type.value if tool_info.tool_type else None
            default_provider = tool_info.provider.value if tool_info.provider else None
    
    # 保存每个结果到数据库（使用asyncpg CRUD）
    for i, result in enumerate(results):
        image_type, fusion_key, char_ids_raw, _ = fusion_tasks[i]
        # 保证 character_ids 为字符串列表（避免 DB 对象或混合类型导致 jsonb/INSERT 报错）
        char_ids = [str(c) for c in char_ids_raw] if char_ids_raw else []

        if isinstance(result, BaseException):
            logger.error(f"❌ 融合图生成失败 ({fusion_key}): {result}")
            failed_count += 1
            
            # 保存失败记录到数据库（包含所有可用信息）
            try:
                await create_character_fusion_image(
                    fusion_data={
                            "fusion_key": fusion_key,
                            "image_type": image_type.value,
                            "character_ids": char_ids,
                            "fusion_image_url": "",
                            "user_id": state["user_id"],
                            "conversation_id": str(state["conversation_id"]),
                            "thread_id": str(state["thread_id"]),
                            "run_id": state["run_id"],
                            "aspect_ratio": default_aspect_ratio,
                            "resolution": default_resolution,
                            "model": default_model,
                            "provider": default_provider,
                            "success": False,
                            "error_msg": str(result),
                            "raw_error_msg": str(result)
                        }
                )
            except Exception as e:
                logger.error(f"保存融合图失败记录到数据库失败: {e}")
        else:
            fusion_result, messages = result  # 解包 (ImageGenerationResult, List[BaseMessage])
            all_messages.extend(messages)
            
            if fusion_result and fusion_result.success and fusion_result.image_url:
                logger.info(f"✅ 融合图生成成功 ({fusion_key}): {fusion_result.image_url}")
                
                # 提取ai_messages（需要包装成字典格式）
                ai_messages_json = None
                if messages:
                    result_for_extraction = {"messages": messages if isinstance(messages, list) else [messages]}
                    ai_messages_json = extract_ai_message_json(result_for_extraction)
                
                # 保存到数据库（使用asyncpg CRUD）
                try:
                    await create_character_fusion_image(
                        fusion_data={
                            "fusion_key": fusion_key,
                            "image_type": image_type.value,
                            "character_ids": char_ids,
                            "fusion_image_url": fusion_result.image_url,
                            "user_id": state["user_id"],
                            "conversation_id": str(state["conversation_id"]),
                            "thread_id": str(state["thread_id"]),
                            "run_id": state["run_id"],
                            "aspect_ratio": fusion_result.aspect_ratio or default_aspect_ratio,
                            "resolution": fusion_result.resolution or default_resolution,
                            "model": fusion_result.model or default_model,
                            "seed": fusion_result.seed,
                            "provider": fusion_result.provider or default_provider,
                            "fusion_prompt": fusion_result.generated_prompt,
                            "success": True,
                            "ai_messages": ai_messages_json
                        }
                    )
                    saved_count += 1
                    
                    # 更新character_images中的融合图URL（仅用于后续逻辑，不涉及数据库）
                    for char_id in char_ids:
                        if char_id in character_images:
                            if image_type == ImageType.MAIN:
                                character_images[char_id].main_fusion_url = fusion_result.image_url
                            elif image_type == ImageType.MULTIVIEW:
                                character_images[char_id].multiview_fusion_url = fusion_result.image_url
                except Exception as e:
                    logger.error(f"保存融合图到数据库失败 ({fusion_key}): {e}")
                    failed_count += 1
            else:
                error_msg = fusion_result.error_msg if fusion_result else "未知错误"
                logger.error(f"❌ 融合图生成失败 ({fusion_key}): {error_msg}")
                failed_count += 1
                
                try:
                    await create_character_fusion_image(
                        fusion_data={
                            "fusion_key": fusion_key,
                            "image_type": image_type.value,
                            "character_ids": char_ids,
                            "fusion_image_url": "",
                            "user_id": state["user_id"],
                            "conversation_id": str(state["conversation_id"]),
                            "thread_id": str(state["thread_id"]),
                            "run_id": state["run_id"],
                            "aspect_ratio": fusion_result.aspect_ratio if fusion_result else default_aspect_ratio,
                            "resolution": fusion_result.resolution if fusion_result else default_resolution,
                            "model": fusion_result.model if fusion_result else default_model,
                            "provider": fusion_result.provider if fusion_result else default_provider,
                            "success": False,
                            "error_msg": error_msg,
                            "raw_error_msg": fusion_result.raw_error_msg if fusion_result else error_msg
                        }
                    )
                except Exception as e:
                    logger.error(f"保存融合图失败记录到数据库失败: {e}")
    
    return saved_count, failed_count, all_messages


async def generate_fusion_images_for_scenes(
    scenes_data: List[StoryboardScene],
    characters_data: List[CharacterProfile],
    character_images: Dict[str, CharacterImageInfo],
    story_outline: Optional[Any],
    user_option: Optional[UserOption],
    state: Dict[str, Any],
    llm: Any = None
) -> Tuple[int, int, List[BaseMessage]]:
    """为指定的 scenes 生成融合图（可重用的核心逻辑）
    
    预加载 prompt/LLM/agent 避免每个融合任务重复加载。
    
    Args:
        scenes_data: 场景数据列表
        characters_data: 角色数据列表
        character_images: 角色图片信息字典
        story_outline: 故事大纲（可选）
        user_option: 用户选项
        llm: 未使用，保留以兼容调用方
        state: 状态字典（包含 user_id, conversation_id, thread_id, run_id）
    
    Returns:
        Tuple[saved_count, failed_count, all_messages]:
        - saved_count: 成功保存的融合图数量
        - failed_count: 失败的融合图数量
        - all_messages: 所有消息列表
    """
    if not scenes_data:
        logger.warning("⚠️ 未找到scenes数据，跳过融合图生成")
        return 0, 0, []
    
    if not characters_data:
        logger.warning("⚠️ 没有角色数据，跳过融合图生成")
        return 0, 0, []
    
    # 阶段1：决定需要生成哪些融合图（不需要数据库）
    model_limit = get_model_limit(user_option)
    logger.info(f"⚙️ 模型能力限制: {model_limit}张参考图")
    
    main_fusion_combinations, multiview_fusion_combinations, fusion_plan_messages = (
        await decide_fusion_combinations_via_deep_agent(
            scenes=scenes_data,
            characters_data=characters_data,
            model_limit=model_limit,
            character_images=character_images,
            thread_id=str(state.get("thread_id") or "") if isinstance(state, dict) else "",
            run_id=str(state.get("run_id") or "") if isinstance(state, dict) else "",
            detected_language=state.get("detected_language") if isinstance(state, dict) else None,
        )
    )
    
    logger.info(f"🎯 需要生成 {len(main_fusion_combinations)} 个主图融合图，{len(multiview_fusion_combinations)} 个multiview融合图")
    
    if not main_fusion_combinations and not multiview_fusion_combinations:
        logger.info(f"ℹ️ 没有需要生成的融合图")
        return 0, 0, list(fusion_plan_messages)
    
    # ⭐ 复用已有成功融合图：查询 DB 中已有的成功记录，跳过不需要重新生成的组合
    from ....crud.video.video_character import get_character_fusion_images_by_character_ids
    all_candidate_char_ids = list({cid for ids in list(main_fusion_combinations.values()) + list(multiview_fusion_combinations.values()) for cid in ids})
    existing_fusions = await get_character_fusion_images_by_character_ids(all_candidate_char_ids) if all_candidate_char_ids else []
    existing_success_keys = {f.fusion_key for f in existing_fusions if getattr(f, "success", False) and getattr(f, "fusion_image_url", "")}
    
    skipped_main = {k for k in main_fusion_combinations if k in existing_success_keys}
    skipped_mv = {k for k in multiview_fusion_combinations if k in existing_success_keys}
    for k in skipped_main:
        del main_fusion_combinations[k]
    for k in skipped_mv:
        del multiview_fusion_combinations[k]
    
    if skipped_main or skipped_mv:
        logger.info(f"♻️ 复用已有融合图: 跳过 {len(skipped_main)} 个主图 + {len(skipped_mv)} 个multiview（已有成功记录）")
    
    if not main_fusion_combinations and not multiview_fusion_combinations:
        logger.info(f"ℹ️ 所有融合图均已存在，无需重新生成")
        return 0, 0, []
    
    logger.info(f"🎯 实际需生成: {len(main_fusion_combinations)} 个主图融合图，{len(multiview_fusion_combinations)} 个multiview融合图")
    
    # 阶段2：并发生成融合图（不需要数据库，调用外部API）
    detected_language = state.get("detected_language") if isinstance(state, dict) else None
    fusion_tasks = []
    
    # 主图融合任务
    for fusion_key, char_ids in main_fusion_combinations.items():
        task = generate_character_fusion_image(
            character_ids=char_ids,
            character_images=character_images,
            image_type=ImageType.MAIN,
            user_option=user_option,
            story_outline=story_outline,
            characters_data=characters_data,
            detected_language=detected_language
        )
        fusion_tasks.append((ImageType.MAIN, fusion_key, char_ids, task))
    
    # multiview融合任务
    for fusion_key, char_ids in multiview_fusion_combinations.items():
        task = generate_character_fusion_image(
            character_ids=char_ids,
            character_images=character_images,
            image_type=ImageType.MULTIVIEW,
            user_option=user_option,
            story_outline=story_outline,
            characters_data=characters_data,
            detected_language=detected_language
        )
        fusion_tasks.append((ImageType.MULTIVIEW, fusion_key, char_ids, task))
    
    # 并发执行所有融合图生成（不需要数据库事务）
    logger.info(f"🚀 开始并发生成 {len(fusion_tasks)} 个融合图...")
    results = await asyncio.gather(*[task for _, _, _, task in fusion_tasks], return_exceptions=True)
    
    # 协作式取消：若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
    await raise_if_cancelled()
    
    # 阶段3：保存结果到数据库并收集messages
    saved_count, failed_count, all_messages = await _save_fusion_results_to_db(
        results=results,
        fusion_tasks=fusion_tasks,
        state=state,  # type: ignore
        user_option=user_option,
        character_images=character_images
    )
    all_messages = list(fusion_plan_messages) + all_messages
    
    logger.info(f"✅ 融合图生成完成: 成功 {saved_count} 个，失败 {failed_count} 个")
    
    return saved_count, failed_count, all_messages


async def character_fusion_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any
) -> Union[VideoAgentState, Dict[str, Any]]:
    """角色融合图生成节点
    
    功能：
    1. 获取所有scenes的角色组合
    2. 根据模型能力，决定生成哪些融合图
    3. 并发生成融合图（主图和multiview分开）
    4. 存储融合图到数据库
    可由 agent_video_constants.ENABLE_FUSION 关闭。
    """
    if not ENABLE_FUSION:
        logger.info("⏭️ ENABLE_FUSION=False，跳过融合图生成")
        return {}
    try:
        logger.info(f"🎨 开始角色融合图生成节点，conversation_id: {state.get('conversation_id')}")
        
        # 获取所有scenes
        scene_uuids = state.get("scene_uuids", [])
        if not scene_uuids:
            logger.warning("⚠️ 没有scene_uuids，跳过融合图生成")
            return {"messages": []}
        
        # 阶段1：获取数据（使用独立的事务）
        scenes_data, characters_data, character_images, story_outline = await _load_fusion_data_from_db(
            state, scene_uuids
        )
        
        if not scenes_data:
            logger.warning("⚠️ 未找到scenes数据，跳过融合图生成")
            return {"messages": []}
        
        if not characters_data:
            logger.warning("⚠️ 没有character_uuids，跳过融合图生成")
            return {"messages": []}
        
        # 获取用户选项
        user_input_data = state.get("user_input_data")
        user_option = user_input_data.user_option if user_input_data else None
        
        # ✅ 重用核心逻辑函数（内部按需 load_prompt 获取 prompt+llm）
        saved_count, failed_count, all_messages = await generate_fusion_images_for_scenes(
            scenes_data=scenes_data,
            characters_data=characters_data,
            character_images=character_images,
            story_outline=story_outline,
            user_option=user_option,
            state=state,  # type: ignore
            llm=None
        )
        
        return {"messages": all_messages}
        
    except BusinessException as e:
        logger.error(f"融合图生成失败: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"融合图生成失败: {e}")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"融合图生成失败: {str(e)}"
        )
