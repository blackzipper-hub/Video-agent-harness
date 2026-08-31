"""
主要角色设计功能模块
负责主要角色设计节点的实现和相关功能
"""
import logging
import asyncio
import json
import uuid
from typing import List, Optional, Dict, Any, Union, cast, Tuple
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from ....models.video_state import VideoAgentState, StoryOutline, CharacterProfile, CharacterProfiles, AudioTranscription, ImageUserInput
from ....models.image_result import ImageGenerationResult, CharacterImageAnalysis
from ....models.user_options import UserOption
from ....exceptions import BusinessException, BusinessExceptionCode
from ....crud.video.video_character import create_character, get_character_versions_batch, pick_selected_character_version
from ....services.agent.base_agent import MessageType
from .stage_failure import detect_stage_failure, emit_stage_failure_event
from ....utils.error_classification import classify_failure
from ....services.tool_service import ToolService
from ..schemas import VideoContextSchema
from .agent_video_constants import ENABLE_MULTIVIEW, ENABLE_DIRECT_UPLOAD_REUSE, ENABLE_CHARACTER_STUDIO_BACKGROUND
from ....services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages, attach_images_to_messages
from ..utils.message_utils import extract_ai_message_json, patch_tool_metrics_from_last_tool_message
from ..utils.cancellation import raise_if_cancelled
from prompts.prompt_config import PromptName, PROMPTS_CONFIG
from prompts.prompt_loader import create_llm_from_model_config
from app.orchestration.skills.prompt_context import (
    facts_human_message,
    skill_system_message,
)
from ..utils.llm_resilience import StructuredResilienceKind, ainvoke_structured_resilient
from ....services.agent.utils.database_utils import get_story_outline_from_db, get_audio_transcription_from_db, get_completed_character_uuids_by_name
from pydantic import BaseModel, Field
from typing import List as PydanticList, TYPE_CHECKING
from sqlalchemy.ext.asyncio import AsyncSession
from ....models.video_state import ImageUserInput
from ....models.image_result import CharacterImageAnalysis
from ....crud.video.video_character import create_character_version
from ....models.tool_enums import ToolProvider
from ....utils.time_format import format_sec_range

if TYPE_CHECKING:
    from ....services.tool_service import ToolsInfo

logger = logging.getLogger(__name__)

# 角色设计扩展常量（为后续可能的独立node准备）
CHARACTER_DESIGN_EXTENSION = """
🎭 **角色设计说明**：
在本系统中，"角色设计"包含故事中的所有重要视觉元素：人物角色、重要物品、核心场所。
这些元素都被视为"角色"，需要统一设计，确保风格一致性和故事服务性。
"""


class CharacterImageMatch(BaseModel):
    """角色与图片的匹配结果"""
    character_id: str = Field(description="角色ID（用于匹配）")
    character_name: str = Field(description="角色名称")
    matched_image_indices: PydanticList[int] = Field(
        default_factory=list, 
        description="匹配的图片编号列表（整数，如 [1, 2] 表示图片1和图片2，从1开始计数。一个角色可能在多张图片中出现）"
    )
    match_reason: str = Field(description="匹配理由或不匹配的原因")
    confidence: float = Field(description="匹配置信度（0-100），如果不匹配则为0")
    style_match: bool = Field(description="风格是否匹配（True表示图片风格与角色设计风格一致，False表示风格不一致需要重新生成）")


class CharacterImageMatchingResult(BaseModel):
    """所有角色的匹配结果"""
    matches: PydanticList[CharacterImageMatch] = Field(description="每个角色的匹配结果")

    def resilience_empty_reason(self) -> Optional[str]:
        # 被 llm_resilience._wrap_invoke_with_result_check 自动调用：返回非空字符串触发 EMPTY_RESPONSE 桶。
        # 调用方只在 images 非空时才进入此分支（match_characters_with_images 上游已短路 images=[] 的情况），
        # 所以 LLM 输入永远有效：每个 character 都应该返回一项 match（即使 matched_image_indices=[]）。
        # matches 空 = LLM 漏答全部角色，业务侧已 raise，这里提前重试避免直接失败。
        if not self.matches:
            return "matches is empty"
        return None


async def build_prompt_for_character_image_matching(characters: List[CharacterProfile], images: List) -> Tuple[List[BaseMessage], Any]:
    """构建角色与图片匹配的提示词
    
    Args:
        characters: 角色列表（每个角色的id字段包含匹配用的ID）
        images: 用户上传的图片列表
    
    Returns:
        Tuple[List[BaseMessage], Runnable]: (messages, llm)
            - messages: 格式化后的 prompt messages
            - llm: 从 Hub 拉取的 LLM（用于 create_agent）
    """
    
    # 提取图片 URL 列表
    image_urls = [image.url if hasattr(image, 'url') else image for image in images]

    system = skill_system_message(
        "character-matching-director",
        lead="Follow character-matching-director.",
    )
    facts = {
        "characters": [
            {
                "id": char.id,
                "name": char.name,
                "type": char.type,
                "description": char.description,
                "appearance": char.appearance,
                "style": char.style,
            }
            for char in characters
        ],
        "reference_image_count": len(image_urls),
        "images_attached": True,
    }
    messages: List[BaseMessage] = [
        SystemMessage(content=system),
        HumanMessage(content=facts_human_message(facts)),
    ]
    llm = create_llm_from_model_config(PROMPTS_CONFIG[PromptName.VIDEO_MAIN_CHARACTER_MATCHING]["model_config"])
    
    # 使用 attach_images_to_messages 添加图片（自动添加图片索引提示）
    messages = attach_images_to_messages(messages, image_urls, add_image_index_hint=True)
    
    return messages, llm


async def _match_characters_with_images(characters: List[CharacterProfile], images: List) -> Tuple[List[BaseMessage], Dict[str, Any]]:
    """将设计的角色与用户上传的图片进行匹配
    
    Args:
        characters: 角色列表（每个角色的id字段包含匹配用的ID）
        images: 用户上传的图片列表
        llm: LLM实例
        
    Returns:
        Tuple[List[BaseMessage], Dict]: (消息列表, 匹配结果字典)
        匹配结果包含：
            "matched_characters": [(character, image_url), ...],  # 匹配到图片的角色
            "unmatched_characters": [character, ...],  # 需要生成图片的角色
            "unused_images": [image_url, ...]  # 未使用的图片
    """
    # 创建角色ID到角色的映射字典
    character_dict = {char.id: char for char in characters}
    
    if not images:
        return [], {
            "matched_characters": [],
            "unmatched_characters": characters,
            "unused_images": []
        }
    
    try:
        logger.info(f"🔍 开始将 {len(characters)} 个角色与 {len(images)} 张图片进行匹配...")
        
        # 使用拆分的提示词构建方法并获取配置好的 LLM
        messages, _ = await build_prompt_for_character_image_matching(characters, images)
        
        inputs = {"messages": messages}
        agent_result = await ainvoke_structured_resilient(
            kind=StructuredResilienceKind.CREATE_AGENT,
            prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_MAIN_CHARACTER_MATCHING],
            agent_inputs=inputs,
            agent_tools=[],
            structured_schema=CharacterImageMatchingResult,
            log_context={"phase": "main_character_image_matching"},
        )
        
        # 从 agent 结果中提取 structured_response
        if not agent_result or "structured_response" not in agent_result:
            raise Exception("Agent 未返回有效的匹配结果")
        
        result = agent_result["structured_response"]
        
        # ✅ 提取完整的 agent messages
        # 返回本次LLM调用的 input messages + output messages
        agent_full_messages = agent_result.get("messages", [])
        input_message_count = len(messages)
        output_messages = agent_full_messages[input_message_count:] if len(agent_full_messages) > input_message_count else []
        
        # 返回本次调用的 input messages + output messages
        new_messages = messages + output_messages
        
        logger.info(f"🔗 角色图片匹配完成，返回 {len(new_messages)} 条消息（{len(messages)} 条input + {len(output_messages)} 条output）")
        
        # 提取图片 URL 列表（用于索引映射）
        image_urls = [image.url if hasattr(image, 'url') else image for image in images]
        
        # 处理匹配结果（使用ID匹配 + 索引转URL）
        matched_characters = []
        unmatched_characters = []
        style_regen_characters = []  # 内容匹配但风格不一致，需要保留外观、换风格
        isolate_characters = []  # 命中图被多角色共用（合照）或全局关闭直接复用：走 I2I 提取单人独立图
        used_image_urls = set()
        image_usage_count = {}  # 记录每张图片被使用的次数
        matched_character_ids = set()  # 记录已匹配的角色ID
        directly_reused_urls = set()  # 已被某角色直接复用的原图 URL（用于一图一角色去重）
        
        for match in result.matches:
            # 使用character_id来查找对应的角色
            if match.character_id not in character_dict:
                logger.warning(f"⚠️ 收到未知的角色ID: {match.character_id}，跳过")
                continue
            
            character = character_dict[match.character_id]
            matched_character_ids.add(match.character_id)
            
            # 将图片索引转换为 URL
            matched_urls = []
            for idx in match.matched_image_indices:
                if 1 <= idx <= len(image_urls):
                    url = image_urls[idx - 1]  # 1-based to 0-based
                    matched_urls.append(url)
                else:
                    logger.warning(f"⚠️ 角色 {character.name} 的图片索引 {idx} 超出范围（共 {len(image_urls)} 张图片），跳过")
            
            # 只有在置信度高且风格匹配时才使用用户图片
            if matched_urls and len(matched_urls) > 0 and match.confidence > 60 and match.style_match:
                # 暂时只使用第一张匹配的图片
                first_matched_image = matched_urls[0]
                # 记录图片使用次数（统计多角色共用情况）
                image_usage_count[first_matched_image] = image_usage_count.get(first_matched_image, 0) + 1
                # 记录所有匹配图片的使用情况
                for img_url in matched_urls:
                    used_image_urls.add(img_url)

                # 一图一角色去重：仅当允许直接复用、未启用 studio 背景归一化、且该图尚未被其他角色直接复用时，才直接使用原图；
                # 否则（合照被多角色命中 / 关闭直接复用 / studio 背景）走 I2I 提取单人独立图。
                allow_direct_reuse = (
                    ENABLE_DIRECT_UPLOAD_REUSE
                    and not ENABLE_CHARACTER_STUDIO_BACKGROUND
                    and first_matched_image not in directly_reused_urls
                )
                if allow_direct_reuse:
                    directly_reused_urls.add(first_matched_image)
                    matched_characters.append((character, first_matched_image))
                    logger.info(f"✅ 角色 {character.name} 匹配到 {len(matched_urls)} 张图片，直接复用第一张: {first_matched_image}（置信度: {match.confidence:.2f}，风格匹配: {match.style_match}）")
                    if len(matched_urls) > 1:
                        logger.info(f"   其他匹配图片: {', '.join(matched_urls[1:])}")
                    logger.info(f"   理由: {match.match_reason}")
                else:
                    isolate_characters.append((character, matched_urls))
                    if ENABLE_CHARACTER_STUDIO_BACKGROUND:
                        logger.info(f"🎬 角色 {character.name} 启用 studio 背景归一化，走 I2I 保留外观并替换为浅灰白渐变背景")
                    elif not ENABLE_DIRECT_UPLOAD_REUSE:
                        logger.info(f"🧩 角色 {character.name} 命中图片但已关闭直接复用，走 I2I 提取单人独立图")
                    else:
                        logger.info(f"🧩 角色 {character.name} 命中的图片已被其他角色直接复用（合照共用），走 I2I 提取单人独立图")
                    logger.info(f"   理由: {match.match_reason}")
            elif matched_urls and len(matched_urls) > 0 and match.confidence > 60 and not match.style_match:
                # 内容匹配但风格不一致：保留匹配图片信息，后续需要保留外观换风格
                style_regen_characters.append((character, matched_urls))
                for img_url in matched_urls:
                    used_image_urls.add(img_url)
                logger.info(f"🎨 角色 {character.name} 匹配到 {len(matched_urls)} 张图片但风格不一致，需保留外观换风格")
                logger.info(f"   理由: {match.match_reason}")
            else:
                unmatched_characters.append(character)
                logger.info(f"❌ 角色 {character.name} 未匹配到合适图片")
                logger.info(f"   理由: {match.match_reason}")
        
        # 检查是否有角色没有被返回（LLM可能遗漏了某些角色）
        all_character_ids = set(char.id for char in characters)
        missing_character_ids = all_character_ids - matched_character_ids
        if missing_character_ids:
            logger.warning(f"⚠️ LLM未返回以下角色的匹配结果，将视为未匹配: {missing_character_ids}")
            for char_id in missing_character_ids:
                unmatched_characters.append(character_dict[char_id])
        
        # 显示多角色图片信息
        multi_character_images = {url: count for url, count in image_usage_count.items() if count > 1}
        if multi_character_images:
            for url, count in multi_character_images.items():
                logger.info(f"📸 图片 {url} 包含 {count} 个角色")
        
        unused_images = [img for img in images if img.url not in used_image_urls]
        
        logger.info(f"🔍 匹配完成: {len(matched_characters)} 个角色直接复用原图, {len(isolate_characters)} 个角色需提取单人独立图, {len(style_regen_characters)} 个风格不匹配需换风格, {len(unmatched_characters)} 个角色需要全新生成")
        
        # ✅ 返回新消息
        return new_messages, {
            "matched_characters": matched_characters,
            "unmatched_characters": unmatched_characters,
            "style_regen_characters": style_regen_characters,
            "isolate_characters": isolate_characters,
            "unused_images": unused_images
        }
            
    except Exception as e:
        logger.error(f"❌ 角色图片匹配失败: {e}")
        # 失败时返回所有角色都未匹配
        return [], {
            "matched_characters": [],
            "unmatched_characters": characters,
            "style_regen_characters": [],
            "unused_images": images
        }


async def _generate_characters_from_story(
    story_outline: StoryOutline,
    images: List[ImageUserInput],
    user_input: Optional[str],
    video_analysis: Optional[Any],
    audio_transcription: Optional[AudioTranscription],
    detected_language: Optional[str],
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Tuple[List[CharacterProfile], List[BaseMessage]]:
    """根据故事生成角色列表
    
    Returns:
        Tuple[List[CharacterProfile], List[BaseMessage]]: (characters, messages)
    """
    logger.info("🎭 开始生成主要角色和配角...")

    if not (thread_id and run_id):
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "缺少 thread_id/run_id，无法走 character deep-agent",
        )

    from app.services.agent.video.character_stage import (
        export_character_inputs,
        generate_characters_via_deep_agent,
    )

    image_urls = [img.url for img in (images or []) if getattr(img, "url", None)]
    audio_info = ""
    if audio_transcription:
        audio_type = "纯音乐（无歌词）" if audio_transcription.is_instrumental else "有歌词音频"
        segs = []
        for i, segment in enumerate(audio_transcription.segments or []):
            segs.append(f"[{segment.start}-{segment.end}] {segment.text}")
        audio_info = (
            f"音频类型: {audio_type}; 时长: {audio_transcription.duration}s; "
            f"文本: {(audio_transcription.text or '')[:2000]}; "
            f"片段: {' | '.join(segs[:20])}"
        )
    input_paths = export_character_inputs(
        thread_id=thread_id,
        run_id=run_id,
        story_outline=story_outline,
        analysis=video_analysis,
        image_urls=image_urls,
        user_input=user_input or "",
        audio_info=audio_info,
        audio_transcription=audio_transcription,
    )
    profiles, msgs = await generate_characters_via_deep_agent(
        thread_id=thread_id,
        run_id=run_id,
        input_paths=input_paths,
        detected_language=detected_language,
    )
    characters = profiles.characters
    # Ensure matchable ids
    for char in characters:
        if not char.id or not str(char.id).startswith("char_"):
            char.id = f"char_{uuid.uuid4().hex[:8]}"
    logger.info(f"✅ character deep-agent 完成: {len(characters)} 个角色")
    return characters, list(msgs or [])


async def _match_characters_with_user_images(
    characters: List[CharacterProfile],
    images: List[ImageUserInput]
) -> Tuple[Dict[str, Any], List[BaseMessage]]:
    """将角色与用户上传的图片进行匹配
    
    Returns:
        Tuple[Dict[str, Any], List[BaseMessage]]: (matching_result, messages)
            matching_result 包含: matched_characters, unmatched_characters, unused_images
    """
    logger.info("🔗 开始角色与图片匹配...")
    matching_messages, matching_result = await _match_characters_with_images(characters, images)
    
    matched_characters = matching_result["matched_characters"]
    unmatched_characters = matching_result["unmatched_characters"]
    unused_images = matching_result["unused_images"]
    
    logger.info(f"🔗 图片匹配结果:")
    logger.info(f"   匹配成功的角色: {len(matched_characters)} 个")
    logger.info(f"   未匹配的角色: {len(unmatched_characters)} 个")
    logger.info(f"   未使用的图片: {len(unused_images)} 个")
    
    return matching_result, matching_messages


async def _process_matched_characters(
    matched_characters: List[Tuple[CharacterProfile, str]],
    story_outline: StoryOutline,
    user_option: Optional[UserOption],
    user_input: str,
    images: List[ImageUserInput],
    state: Dict[str, Any],
) -> Tuple[List[str], List[BaseMessage]]:
    """处理匹配到图片的角色（直接使用用户图片）
    
    Returns:
        Tuple[List[str], List[BaseMessage]]: (character_uuids, messages)
    """
    if not matched_characters:
        return [], []
    
    logger.info(f"✅ {len(matched_characters)} 个角色将使用用户上传的图片")
    new_character_uuids = []
    all_messages = []
    
    for i, (character, image_url) in enumerate(matched_characters):
        logger.info(f"💾 保存匹配角色 {i+1}/{len(matched_characters)}: {character.name}")
        
        # 保存到数据库
        character_uuid = await create_character(
            character_data={
                "type": character.type,
                "name": character.name,
                "description": character.description,
                "personality": character.personality,
                "appearance": character.appearance,
                "role": character.role,
                "style": character.style,
                "body_type": character.body_type,
                "image_url": image_url,
                "conversation_id": str(state["conversation_id"]),
                "thread_id": str(state["thread_id"]),
                "run_id": state["run_id"],
                "user_id": state["user_id"]
            }
        )
        
        # 创建角色版本记录（用户上传图：用 user_option 写入 aspect_ratio/resolution/model/image_generation_tool）
        _ar = user_option.aspect_ratio.value if user_option and getattr(user_option, "aspect_ratio", None) else None
        _res = user_option.resolution.value if user_option and getattr(user_option, "resolution", None) else None
        _tool = user_option.image_generation_tool.value if user_option and getattr(user_option, "image_generation_tool", None) else None
        version_uuid = await create_character_version(
            version_data={
                "video_character_id": character_uuid,
                "conversation_id": str(state["conversation_id"]),
                "thread_id": str(state["thread_id"]),
                "run_id": state["run_id"],
                "user_id": state["user_id"],
                "version_number": 1,
                "character_image_url": image_url,
                "t2i_prompt": "",
                "provider": ToolProvider.USER_UPLOAD.value,
                "reference_image_urls": [],
                "success": True,
                "error_msg": None,
                "raw_error_msg": None,
                "aspect_ratio": _ar,
                "resolution": _res,
                "model": _tool,
                "seed": None,
                "ai_messages": None,
                "image_generation_tool": _tool,
            }
        )
        
        # 设置角色选中的版本
        from ....crud.video.video_character import update_character_selected_version
        await update_character_selected_version(character_uuid, version_uuid)
        
        new_character_uuids.append(character_uuid)
        logger.info(f"✅ 角色 {character.name} 已保存，UUID: {character_uuid}，使用图片: {image_url}")
        
        # 为匹配的角色生成多视角图（可由 ENABLE_MULTIVIEW 关闭）
        if ENABLE_MULTIVIEW:
            _, multi_view_messages = await _generate_multi_view_for_character(
                character=character,
                character_uuid=character_uuid,
                version_uuid=version_uuid,
                character_image_url=image_url,
                story_outline=story_outline,
                user_option=user_option,
                state=state,
                reference_images=images,
                user_input=user_input
            )
            if multi_view_messages:
                all_messages.extend(multi_view_messages)
    
    return new_character_uuids, all_messages


async def _process_style_regen_characters(
    style_regen_characters: List[Tuple[CharacterProfile, List[str]]],
    all_images: List[ImageUserInput],
    user_option: Optional[UserOption],
    story_outline: StoryOutline,
    state: Dict[str, Any],
) -> Tuple[List[str], List[BaseMessage]]:
    """处理风格不匹配的角色：内容匹配但风格不一致，需要保留外观换风格生成新图片

    与 unmatched 的区别：匹配到的参考图就是该角色本人，需要保留五官/服装/配饰，
    仅调整艺术风格使其与故事统一。

    Returns:
        Tuple[List[str], List[BaseMessage]]: (character_uuids, messages)
    """
    if not style_regen_characters:
        return [], []

    logger.info(f"🎨 为 {len(style_regen_characters)} 个风格不匹配角色重新生成图片（保留外观换风格）...")

    # 将匹配到的参考图构造为 ImageUserInput 传入，同时标记 is_style_regeneration
    from ....models.video_state import ImageUserInput as ImgInput
    results_uuids = []
    results_messages = []

    for character, matched_urls in style_regen_characters:
        logger.info(f"🎨 风格换图: {character.name}，使用 {len(matched_urls)} 张匹配图")
        # 构造只包含匹配图片的参考列表
        ref_images = [ImgInput(url=url) for url in matched_urls]
        result = await _generate_single_character_image(
            character=character,
            images=ref_images,
            user_option=user_option,
            story_outline=story_outline,
            state=state,
            is_style_regeneration=True,
        )
        if result.get("uuid"):
            results_uuids.append(result["uuid"])
        if result.get("message"):
            results_messages.extend(result["message"])

    logger.info(f"✅ 风格换图完成，新增UUID: {len(results_uuids)} 个")
    return results_uuids, results_messages


async def _process_isolate_characters(
    isolate_characters: List[Tuple[CharacterProfile, List[str]]],
    user_option: Optional[UserOption],
    story_outline: StoryOutline,
    state: Dict[str, Any],
) -> Tuple[List[str], List[BaseMessage]]:
    """处理需要从原图中提取单人独立图的角色。

    场景：一张图被多个角色命中（合照）、全局关闭了直接复用、或启用了 studio 背景归一化。
    这些角色不直接复用原图，而是用命中的原图作为参考，I2I 生成单人独立图
    （只包含该角色；若 ENABLE_CHARACTER_STUDIO_BACKGROUND 则替换为浅灰白渐变 studio 背景）。

    Returns:
        Tuple[List[str], List[BaseMessage]]: (character_uuids, messages)
    """
    if not isolate_characters:
        return [], []

    logger.info(f"🧩 为 {len(isolate_characters)} 个角色从原图提取单人独立图（I2I）...")

    from ....models.video_state import ImageUserInput as ImgInput
    results_uuids = []
    results_messages = []

    for character, matched_urls in isolate_characters:
        logger.info(f"🧩 单人提取: {character.name}，使用 {len(matched_urls)} 张命中图")
        ref_images = [ImgInput(url=url) for url in matched_urls]
        result = await _generate_single_character_image(
            character=character,
            images=ref_images,
            user_option=user_option,
            story_outline=story_outline,
            state=state,
            is_isolate_extraction=True,
        )
        if result.get("uuid"):
            results_uuids.append(result["uuid"])
        if result.get("message"):
            results_messages.extend(result["message"])

    logger.info(f"✅ 单人提取完成，新增UUID: {len(results_uuids)} 个")
    return results_uuids, results_messages


async def _process_unmatched_characters(
    unmatched_characters: List[CharacterProfile],
    unused_images: List[ImageUserInput],
    all_images: List[ImageUserInput],
    user_option: Optional[UserOption],
    story_outline: StoryOutline,
    state: Dict[str, Any],
) -> Tuple[List[str], List[BaseMessage]]:
    """为未匹配的角色生成图片
    
    Returns:
        Tuple[List[str], List[BaseMessage]]: (character_uuids, messages)
    """
    if not unmatched_characters:
        return [], []
    
    logger.info(f"🎨 为 {len(unmatched_characters)} 个角色生成图片...")
    
    # 优先使用未使用的图片作为参考，然后添加原始图片中不重复的部分
    unused_urls = set(img.url for img in (unused_images or []))
    reference_images = list(unused_images or [])
    for img in all_images:
        if img.url not in unused_urls:
            reference_images.append(img)
    logger.info(f"🖼️  参考图片数量: {len(reference_images)} 个")
    
    # 批量生成角色图片
    logger.info("🚀 开始批量生成角色图片...")
    character_image_result = await batch_generate_character_images(
        unmatched_characters, reference_images, user_option, story_outline, state
    )
    generated_uuids = character_image_result["character_uuids"]
    character_image_messages = character_image_result["messages"]
    logger.info(f"✅ 角色图片生成完成，新增UUID: {len(generated_uuids)} 个")
    
    return generated_uuids, character_image_messages


async def main_character_design_node(state: VideoAgentState, runtime: Runtime[VideoContextSchema], send_event_func: Any) -> Union[VideoAgentState, Dict[str, Any]]:
    """主要角色设计节点 - 在故事大纲生成后设计主要角色和配角
    
    流程：
    1. 生成角色：根据故事生成角色列表
    2. 匹配：将角色与用户上传的图片进行匹配
    3. 处理匹配的角色：保存匹配的角色并使用用户图片
    4. 处理未匹配的角色：为未匹配的角色生成图片
    """        
    try:
        logger.info(f"🎭 开始主要角色设计节点，conversation_id: {state.get('conversation_id')}")
        
        # 获取基础信息
        story_outline_uuid = state.get("story_outline_uuid")
        existing_character_uuids = state.get("character_uuids", [])
        
        if not story_outline_uuid:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "缺少故事梗概UUID"
            )
        
        # ✅ 修复：按需创建连接，不依赖 context
        from ....models.database import AsyncSessionLocal
        user_input_data = state.get("user_input_data")
        images = user_input_data.images if user_input_data else []
        
        story_outline = await get_story_outline_from_db(story_outline_uuid)
        # 获取音频转录信息
        audio_transcription_uuids = state.get("audio_transcription_uuids", [])
        audio_transcription = await get_audio_transcription_from_db(audio_transcription_uuids)
        # 获取视频分析结果
        analysis_uuid = state.get("analysis_uuid")
        video_analysis = None
        if analysis_uuid:
            from ....services.agent.utils.database_utils import get_video_analysis_from_db
            video_analysis = await get_video_analysis_from_db(analysis_uuid)
        # ✅ 连接已释放，后续处理不占用连接
        
        # 第一步：生成角色（内部自行加载 prompt/llm）
        characters, generation_messages = await _generate_characters_from_story(
            story_outline=story_outline,
            images=images,
            user_input=user_input_data.user_input if user_input_data else None,
            video_analysis=video_analysis,
            audio_transcription=audio_transcription,
            detected_language=state.get("detected_language"),
            thread_id=str(state.get("thread_id") or ""),
            run_id=str(state.get("run_id") or ""),
        )
        all_messages = generation_messages
        
        # ♻️ 断点续跑幂等：跳过本 thread（=本视频）下「已成功」的角色，避免取消/恢复后重复生成图片
        already_done_character_uuids: List[str] = []
        try:
            _done_by_name = await get_completed_character_uuids_by_name(
                str(state.get("thread_id") or ""), str(state.get("user_id") or "")
            )
            if _done_by_name:
                pending_characters = [c for c in characters if c.name not in _done_by_name]
                _seen_done: set = set()
                for c in characters:
                    _uid = _done_by_name.get(c.name)
                    if _uid and _uid not in _seen_done:
                        _seen_done.add(_uid)
                        already_done_character_uuids.append(_uid)
                skipped_count = len(characters) - len(pending_characters)
                if skipped_count:
                    logger.info(f"♻️ 断点续跑：跳过 {skipped_count} 个已成功的角色")
                characters = pending_characters
        except Exception as _skip_err:
            logger.warning(f"⚠️ 角色断点续跑检查失败，按全量生成: {_skip_err}")
        
        # 第二步：匹配
        matching_result, matching_messages = await _match_characters_with_user_images(
            characters=characters,
            images=images
        )
        all_messages.extend(matching_messages)
        matched_characters = matching_result["matched_characters"]
        unmatched_characters = matching_result["unmatched_characters"]
        style_regen_characters = matching_result.get("style_regen_characters", [])
        isolate_characters = matching_result.get("isolate_characters", [])
        unused_images = matching_result["unused_images"]
        
        new_character_uuids = []
        
        # 第三步、第四步需要 db，使用新的 session（llm 由各函数内部 prompt loader 获取，不传入）
        # 第三步：处理匹配的角色（使用asyncpg CRUD）
        matched_uuids, matched_messages = await _process_matched_characters(
            matched_characters=matched_characters,
            story_outline=story_outline,
            user_option=user_input_data.user_option if user_input_data else None,
            user_input=user_input_data.user_input if user_input_data else "",
            images=images,
            state=state,
        )
        new_character_uuids.extend(matched_uuids)
        all_messages.extend(matched_messages)
        
        # 第三步半：处理风格不匹配的角色（保留外观换风格）
        style_regen_uuids, style_regen_messages = await _process_style_regen_characters(
            style_regen_characters=style_regen_characters,
            all_images=images,
            user_option=user_input_data.user_option if user_input_data else None,
            story_outline=story_outline,
            state=state,
        )
        new_character_uuids.extend(style_regen_uuids)
        all_messages.extend(style_regen_messages)
        
        # 第三步又半：处理需从原图提取单人独立图的角色（合照被多角色共用 / 关闭直接复用）
        isolate_uuids, isolate_messages = await _process_isolate_characters(
            isolate_characters=isolate_characters,
            user_option=user_input_data.user_option if user_input_data else None,
            story_outline=story_outline,
            state=state,
        )
        new_character_uuids.extend(isolate_uuids)
        all_messages.extend(isolate_messages)
        
        # 第四步：处理未匹配的角色（使用asyncpg CRUD）
        unmatched_uuids, unmatched_messages = await _process_unmatched_characters(
            unmatched_characters=unmatched_characters,
            unused_images=unused_images,
            all_images=images,
            user_option=user_input_data.user_option if user_input_data else None,
            story_outline=story_outline,
            state=state,
        )
        new_character_uuids.extend(unmatched_uuids)
        all_messages.extend(unmatched_messages)
        
        # 合并所有角色UUID（含断点续跑已完成的角色，去重保序）
        all_character_uuids = []
        for _uid in (existing_character_uuids + already_done_character_uuids + new_character_uuids):
            if _uid and _uid not in all_character_uuids:
                all_character_uuids.append(_uid)
        
        logger.info(f"📊 最终结果统计:")
        logger.info(f"   现有角色UUID: {len(existing_character_uuids)} 个")
        logger.info(f"   新增角色UUID: {len(new_character_uuids)} 个")
        logger.info(f"   总角色UUID: {len(all_character_uuids)} 个")
        logger.info(f"   消息总数: {len(all_messages)} 条")
        
        # 发送主要角色设计完成事件
        from ....services.agent.utils.prompt_utils import generate_completion_message_stream
        detected_language = state.get("detected_language")
        user_message, completion_message = await generate_completion_message_stream(
            event_type=MessageType.CHARACTERS_DESIGNED,
            messages=all_messages,
            send_event_func=send_event_func,
            conversation_id=state.get("conversation_id"),
            lang=detected_language
        )
        if completion_message:
            all_messages.append(completion_message)

        await send_event_func(
            conversation_id=state["conversation_id"],
            event_type=MessageType.CHARACTERS_DESIGNED,
            message=user_message,
            extra_data={
                "character_uuids": all_character_uuids,
                "new_character_uuids": new_character_uuids,
                "action": "main_character_generation",
                "run_id": state.get("run_id"),
                "thread_id": state.get("thread_id")
            }
        )
        
        logger.info(f"✅ 主要角色设计节点完成")

        # 失败兜底：任一新角色图片生成失败即暂停并在对话告知（官方原因，不泄密）。
        # 读新建角色的当前版本 success（resume 安全，节点只跑一次）。检测失败不应影响主流程。
        stage_failure = None
        try:
            failed_items = []
            if new_character_uuids:
                versions_map = await get_character_versions_batch(new_character_uuids, state["user_id"])
                for cuuid in new_character_uuids:
                    cv = pick_selected_character_version(versions_map.get(cuuid) or [], None)
                    if cv is not None and not getattr(cv, "success", True):
                        failed_items.append({
                            "index": cuuid,
                            "category": classify_failure(getattr(cv, "error_msg", None)).value,
                            "user_msg": getattr(cv, "error_msg", None),
                        })
            stage_failure = detect_stage_failure(
                "character",
                total=len(new_character_uuids),
                failed_items=failed_items,
                lang=detected_language,
            )
            if stage_failure:
                await emit_stage_failure_event(send_event_func, stage_failure, conversation_id=state.get("conversation_id"))
        except Exception as _e:
            logger.warning(f"角色失败检测跳过（不影响主流程）: {_e}")

        return {
            "character_uuids": all_character_uuids,
            "messages": all_messages,
            "stage_failure": stage_failure,
        }
        
    except BusinessException as e:
        logger.error(f"主要角色设计失败: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"主要角色设计失败: {e}")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"主要角色设计失败: {str(e)}"
        )


# ==================== 从 character_design_utils.py 移过来的函数 ====================

async def build_prompt_for_character_image_generation(
    character: CharacterProfile,
    images: List,
    user_option: Optional[UserOption],
    story_outline: Optional[StoryOutline] = None,
    user_input: str = "",
    tools_info: Optional['ToolsInfo'] = None,
    detected_language: Optional[str] = None,
    is_style_regeneration: bool = False,
    is_isolate_extraction: bool = False,
) -> List[BaseMessage]:
    """构建角色图片生成的 messages（纯 CPU 格式化，不做任何 I/O）"""
    
    has_reference_images = images and len(images) > 0
    from ....models.tool_enums import ToolMode
    
    # 确定工具模式
    mode = ToolMode.I2I if has_reference_images else ToolMode.T2I
    
    # 如果没有传入 tools_info，则获取（避免重复调用）
    if tools_info is None:
        tools_info = ToolService.get_image_generation_tools(user_option, mode=mode)
    
    tool_name = tools_info.primary_tool_name
    image_count = len(images) if has_reference_images else 0
    
    # 构建故事信息
    story_context = None
    if story_outline:
        story_context = f"{story_outline.title} - {story_outline.description}. 风格：{story_outline.style_guide}"
    
    # 获取图像生成指南
    tool_guide = ToolService.get_image_prompt_guide(user_option, mode) if user_option else ""
    
    # 判断角色类型
    character_type = getattr(character, 'type', 'character')
    is_character = character_type == 'character'
    is_object = character_type == 'object'
    is_location = character_type == 'location'
    
    # 提取图片 URL 列表（用于模板和附加）
    image_urls = []
    if has_reference_images:
        image_urls = [image.url if hasattr(image, 'url') else image for image in images]
    
    system = skill_system_message(
        "character-image-tool-director",
        lead="Follow character-image-tool-director.",
    )
    facts = {
        "tool_name": tool_name,
        "mode": "i2i" if has_reference_images else "t2i",
        "character": {
            "name": character.name,
            "type": character_type,
            "description": character.description or "",
            "appearance": character.appearance or "",
            "style": character.style or "",
            "role": character.role or "",
            "body_type": character.body_type or "",
            "personality": getattr(character, "personality", None) or "",
        },
        "is_character": is_character,
        "is_object": is_object,
        "is_location": is_location,
        "is_style_regeneration": bool(is_style_regeneration),
        "is_isolate_extraction": bool(is_isolate_extraction),
        "use_studio_background": bool(ENABLE_CHARACTER_STUDIO_BACKGROUND and is_character),
        "story_context": story_context or "",
        "tool_guide": tool_guide or "",
        "user_input": user_input or "",
        "detected_language": detected_language or "en",
        "reference_image_urls": image_urls,
        "reference_image_count": image_count,
    }
    messages: List[BaseMessage] = [
        SystemMessage(content=system),
        HumanMessage(content=facts_human_message(facts)),
    ]
    # 强制语言：只对 SystemMessage 追加语言要求，使用 detected_language
    apply_language_suffix_to_system_message_in_messages(messages, detected_language)

    # 添加参考图片（如果有的话），自动添加图片索引提示（程序传入，LLM 可以看到）
    if has_reference_images:
        messages = attach_images_to_messages(messages, image_urls, add_image_index_hint=True)
    
    return messages


async def _generate_character_image_with_llm(
    character: CharacterProfile, 
    images: List[ImageUserInput], 
    user_option: Optional[UserOption], 
    story_outline: StoryOutline, 
    user_input: str = "",
    detected_language: Optional[str] = None,
    is_style_regeneration: bool = False,
    is_isolate_extraction: bool = False,
) -> Tuple[ImageGenerationResult, List[BaseMessage]]:
    """使用LLM生成角色图片（参考video/keyframe tool execution模式）"""
    from ....tools.context_schemas import ImageGenerationContext
    from ....models.tool_enums import ToolMode, DefaultValues
    from ....utils.i18n import get_i18n_message_async

    # ⭐ 提前获取工具信息（在 try 块外，简化错误处理）
    logger.info(f"🎨 开始LLM生成角色 {character.name} 的图片...")
    
    # 根据是否有参考图片选择对应的工具模式
    if images:
        tools_info = ToolService.get_image_generation_tools(user_option, ToolMode.I2I)
        logger.info(f"🎨 使用I2I工具生成角色图片（有{len(images)}张参考图片）")
    else:
        tools_info = ToolService.get_image_generation_tools(user_option, ToolMode.T2I)
        logger.info(f"🎨 使用T2I工具生成角色图片（无参考图片）")
    
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
    reference_image_urls = []
    if images:
        reference_image_urls = [img.url if hasattr(img, 'url') else img for img in images if (hasattr(img, 'url') and img.url) or (isinstance(img, dict) and img.get("url"))]
    
    # 获取语言用于国际化
    lang = detected_language or "en"
    
    try:
        # ⭐ 使用预加载的 prompt 模板构建 messages
        image_generation_messages = await build_prompt_for_character_image_generation(
            character, images, user_option, story_outline, user_input,
            tools_info=tools_info,
            detected_language=detected_language,
            is_style_regeneration=is_style_regeneration,
            is_isolate_extraction=is_isolate_extraction,
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
        result = await ainvoke_structured_resilient(
            kind=StructuredResilienceKind.CREATE_AGENT,
            prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_MAIN_CHARACTER_IMAGE_GENERATION],
            agent_inputs=inputs,
            agent_tools=image_tools,
            context_schema=ImageGenerationContext,
            agent_invoke_context=context,
            wrap_agent_parse_fallback=True,
            log_context={"phase": "main_character_image", "character": character.name},
        )
        
        # [metrics 排查] agent 返回后立即打日志，确认 structured_response 里是否有 metrics
        _sr = result.get("structured_response")
        _msgs = result.get("messages", [])
        _last_tool = next((m for m in reversed(_msgs) if getattr(m, "type", None) == "tool"), None)
        _content_len = len(getattr(_last_tool, "content", "") or "") if _last_tool else 0
        logger.info(
            "[main_char_agent] ainvoke 后 structured_response: tool_duration_sec=%s, tool_cost=%s, has_sr=%s; 最后 ToolMessage content 长度=%s",
            getattr(_sr, "tool_duration_sec", None) if _sr else None,
            getattr(_sr, "tool_cost", None) if _sr else None,
            _sr is not None,
            _content_len,
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
                result.get("messages", []),
                structured_response,
            )
        # ⭐ 检查structured_response是否存在
        if not structured_response:
            logger.error(f"❌ LLM生成角色 {character.name} 失败: LLM未返回有效的结构化响应")
            
            # 使用国际化错误消息
            error_message = await get_i18n_message_async(
                "character.generation_failed",
                default="角色图像生成失败",
                lang=lang
            )
            
            # 返回错误结果，填充所有可用信息
            error_result = ImageGenerationResult.error_result(
                error_message=error_message,
                provider=provider_enum.value if provider_enum else None,
                generated_prompt=None,  # LLM未返回，无法获取
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
            logger.info(f"✅ LLM生成角色 {character.name} 完成，成功: {image_result.success}，返回 {len(all_messages)} 条消息")
        else:
            logger.warning(f"⚠️ LLM生成角色 {character.name} 失败: {image_result.error_msg if image_result else '未知错误'}")
        
        return image_result, all_messages
        
    except Exception as e:
        logger.error(f"❌ LLM生成角色 {character.name} 失败: {str(e)}")
        
        # 使用国际化错误消息（工具信息已在 try 块外获取）
        error_message = await get_i18n_message_async(
            "character.generation_failed",
            default="角色图像生成失败",
            lang=lang
        )
        
        # 返回错误结果，填充所有可用信息
        error_result = ImageGenerationResult.error_result(
            error_message=error_message,  # 用户友好的国际化错误消息
            provider=provider_enum.value if provider_enum else None,
            generated_prompt=None,  # 异常时无法获取
            reference_image_urls=reference_image_urls if reference_image_urls else None,
            aspect_ratio=aspect_ratio_enum.value if aspect_ratio_enum else None,
            resolution=resolution_enum.value if resolution_enum else None,
            model=tool_type_enum.value if tool_type_enum else None,
            raw_error_msg=str(e)  # 原始异常信息（仅用于日志）
        )
        return error_result, []


async def _save_character_to_database(
    character: CharacterProfile,
    image_result: ImageGenerationResult,
    new_messages: List[BaseMessage],
    user_option: Optional[UserOption],
    state: Dict[str, Any],
    story_outline: Optional[StoryOutline] = None,
    reference_images: Optional[List] = None,
    user_input: str = ""
) -> Tuple[str, str]:
    """保存角色到数据库（无论LLM成功失败都保存）
    
    Args:
        character: 角色信息
        image_result: 图像生成结果
        new_messages: LLM返回的新消息列表
        user_option: 用户选项
        state: 状态信息
        story_outline: 故事大纲（保留参数以保持兼容性，不再在此函数中使用）
        reference_images: 用户上传的参考图片（保留参数以保持兼容性，不再在此函数中使用）
        user_input: 用户原始输入（保留参数以保持兼容性，不再在此函数中使用）
        
    Returns:
        Tuple[str, str]: (character_uuid, version_uuid) 角色UUID和版本UUID
        
    Raises:
        Exception: 数据库操作失败时直接抛出异常
    """
    # 使用 asyncpg CRUD，不需要创建 session
    logger.info(f"💾 保存角色 {character.name} 到数据库...")
    
    # 处理图片URL和成功状态
    character_image_url = ""
    if image_result and image_result.success and image_result.image_url:
        character_image_url = image_result.image_url
        logger.info(f"✅ 角色 {character.name} 图片生成成功: {character_image_url}")
    else:
        logger.warning(f"⚠️ 角色 {character.name} 图片生成失败: {image_result.error_msg if image_result else '未知错误'}")
    
    # 保存角色基本信息到数据库（使用asyncpg CRUD）
    character_uuid = await create_character(
        character_data={
                "type": character.type,  # 视觉元素类型
                "name": character.name,
                "description": character.description,
                "personality": character.personality,
                "appearance": character.appearance,
                "role": character.role,
                "style": character.style,
                "body_type": character.body_type,
                "image_url": character_image_url,  # 兼容老数据，同时保存到主表
                "success": True,  # 角色创建本身是成功的
                "error_msg": None,
                "conversation_id": str(state["conversation_id"]),
                "thread_id": str(state["thread_id"]),
                "run_id": state["run_id"],
                "user_id": state["user_id"]
        }
    )
    logger.info(f"✅ 角色 {character.name} 基本信息保存成功: {character_uuid}")
        
    # 🔥 无论LLM成功失败都创建版本记录，确保数据完整性
    # 从 image_result 获取数据，缺失时用 user_option 补全（与 keyframe/video version 一致）
    t2i_prompt = image_result.generated_prompt or ""
    reference_image_urls = image_result.reference_image_urls or []
    provider = image_result.provider or (user_option.image_generation_tool.value if user_option else "default")
    success = image_result.success
    error_msg = image_result.error_msg or image_result.message
    raw_error_msg = image_result.raw_error_msg
    aspect_ratio = image_result.aspect_ratio or (user_option.aspect_ratio.value if user_option and getattr(user_option, "aspect_ratio", None) else None)
    resolution = image_result.resolution or (user_option.resolution.value if user_option and getattr(user_option, "resolution", None) else None)
    model = image_result.model or (user_option.image_generation_tool.value if user_option and getattr(user_option, "image_generation_tool", None) else None)
    seed = image_result.seed
    
    logger.info(f"✅ 从image_result获取到数据: provider={provider}, aspect_ratio={aspect_ratio}, resolution={resolution}, model={model}, 参考图片数量={len(reference_image_urls)}")
    
    # 使用专用方法提取ai_messages，与keyframe等保持一致
    ai_messages_json = None
    if new_messages:
        # 构造与其他服务一致的result格式
        result_for_extraction = {"messages": new_messages if isinstance(new_messages, list) else [new_messages]}
        ai_messages_json = extract_ai_message_json(result_for_extraction)
    
    version_uuid = await create_character_version(
        version_data={
            "video_character_id": character_uuid,
            "conversation_id": str(state["conversation_id"]),
            "thread_id": str(state["thread_id"]),
            "run_id": state["run_id"],
            "user_id": state["user_id"],
            "version_number": 1,  # 第一个版本
            "character_image_url": character_image_url,
            "t2i_prompt": t2i_prompt,
            "provider": provider,
            "reference_image_urls": reference_image_urls,
            "success": success,
            "error_msg": error_msg,
            "raw_error_msg": raw_error_msg,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "model": model,
            "seed": seed,
            "ai_messages": ai_messages_json,
            "image_generation_tool": user_option.image_generation_tool.value if user_option and getattr(user_option, "image_generation_tool", None) else None,
            "image_tool_metrics": image_result.image_tool_metrics,
            "tool_duration_sec": image_result.tool_duration_sec,
            "tool_cost": image_result.tool_cost,
            "additional_data": {
                "applied_skill_ids": image_result.applied_skill_ids,
                "constraint_coverage": image_result.constraint_coverage,
                "final_prompt": image_result.final_prompt or t2i_prompt,
            },
        }
    )
    logger.info(f"✅ 角色 {character.name} 版本记录创建成功: {version_uuid}")
    
    # ⭐ 设置角色选中的版本（使用asyncpg CRUD）
    from ....crud.video.video_character import update_character_selected_version
    await update_character_selected_version(character_uuid, version_uuid)
    logger.info(f"✅ 设置角色 {character.name} 选中版本: {version_uuid}")
    logger.info(f"✅ 角色 {character.name} 数据库操作完成")
    
    return character_uuid, version_uuid


async def _generate_multi_view_for_character(
    character: CharacterProfile,
    character_uuid: str,
    version_uuid: str,
    character_image_url: str,
    story_outline: Optional[StoryOutline],
    user_option: Optional[UserOption],
    state: Dict[str, Any],
    reference_images: Optional[List[ImageUserInput]] = None,
    user_input: str = "",
) -> Tuple[Optional[str], List[BaseMessage]]:
    """为角色生成多视角图（独立函数，从 _save_character_to_database 中拆分出来）"""
    if not character_image_url:
        logger.warning(f"⚠️ 角色 {character.name} 没有主图，跳过多视角图生成")
        return None, []
    
    try:
        logger.info(f"🎨 开始为{character.type} '{character.name}' 生成多视角参考图...")
        
        from .multi_view_generation_service import generate_multi_view_character_sheet
        from ....crud.video.video_character import (
            update_character_version_multi_view_image_full,
            update_character_version_selected_multi_view
        )
        
        # 从 state 获取 detected_language
        detected_language = state.get("detected_language") if isinstance(state, dict) else None
        
        multi_view_result, multi_view_messages = await generate_multi_view_character_sheet(
            element=character,
            main_image_url=character_image_url,
            story_outline=story_outline,
            user_option=user_option,
            reference_images=reference_images,
            user_input=user_input,
            detected_language=detected_language,
        )
        
        if multi_view_result and multi_view_result.success:
            # 提取 ai_messages
            ai_messages_json = None
            if multi_view_messages:
                result_for_extraction = {"messages": multi_view_messages if isinstance(multi_view_messages, list) else [multi_view_messages]}
                ai_messages_json = extract_ai_message_json(result_for_extraction)
            
            # 创建多视角图版本并关联到character version（使用asyncpg CRUD）
            multi_view_version_id = await update_character_version_multi_view_image_full(
                character_uuid=character_uuid,
                version_number=1,
                multi_view_image_url=multi_view_result.image_url,
                multi_view_prompt=multi_view_result.generated_prompt,
                user_id=state["user_id"],
                provider=multi_view_result.provider,
                aspect_ratio=multi_view_result.aspect_ratio,
                resolution=multi_view_result.resolution,
                model=multi_view_result.model,
                seed=multi_view_result.seed,
                success=multi_view_result.success,
                error_msg=multi_view_result.error_msg,
                raw_error_msg=multi_view_result.raw_error_msg,
                ai_messages=ai_messages_json,
                conversation_id=str(state.get("conversation_id")) if state.get("conversation_id") is not None else None,
                thread_id=state.get("thread_id"),
                run_id=state.get("run_id")
            )
            
            if multi_view_version_id:
                multi_view_url = multi_view_result.image_url
                logger.info(f"✅ 多视角图生成成功: {multi_view_url} (version_id: {multi_view_version_id})")
                
                # ⭐ 设置角色版本选中的多视角图版本（使用asyncpg CRUD）
                await update_character_version_selected_multi_view(version_uuid, multi_view_version_id)
                logger.info(f"✅ 设置角色版本选中多视角图版本: {multi_view_version_id}")
                
                return multi_view_url, multi_view_messages if multi_view_messages else []
            else:
                logger.warning(f"⚠️ 多视角图生成成功，但保存失败")
                return None, multi_view_messages if multi_view_messages else []
        else:
            error_detail = multi_view_result.error_msg if multi_view_result else "返回结果为空"
            logger.warning(f"⚠️ 多视角图生成失败: {error_detail}")
            return None, multi_view_messages if multi_view_messages else []
            
    except Exception as e:
        logger.warning(f"⚠️ 多视角图生成失败（不影响主流程）: {e}")
        return None, []


async def _generate_single_character_image(
    character: CharacterProfile, 
    images: List[ImageUserInput], 
    user_option: Optional[UserOption], 
    story_outline: StoryOutline, 
    state: Dict[str, Any],
    is_style_regeneration: bool = False,
    is_isolate_extraction: bool = False,
) -> Dict[str, Any]:
    """生成单个角色的图片并保存到数据库
    
    重构后的函数：分离LLM调用和数据库操作，简化异常处理
    
    Args:
        is_style_regeneration: 是否为风格换图模式（匹配到角色但风格不一致），
            True 时参考图就是该角色本人，需保留外观换风格
    """
    try:
        # 从state中获取user_input和detected_language（在LLM调用之前）
        user_input = state.get("user_input", "") if isinstance(state, dict) else ""
        detected_language = state.get("detected_language") if isinstance(state, dict) else None
        
        # 第一步：LLM生成（可能失败，但不影响数据库保存）
        image_result, new_messages = await _generate_character_image_with_llm(
            character, images, user_option, story_outline,
            user_input, detected_language=detected_language,
            is_style_regeneration=is_style_regeneration,
            is_isolate_extraction=is_isolate_extraction,
        )
        
        # 第二步：保存到数据库（无论LLM成功失败都保存，数据库失败直接抛异常）
        character_uuid, version_uuid = await _save_character_to_database(
            character, image_result, new_messages, user_option, state, story_outline,
            reference_images=images,  # ⭐ 传递用户上传的参考图
            user_input=user_input  # ⭐ 传递用户原始输入
        )
        
        # 第三步：生成多视角图（仅当主图生成成功且 ENABLE_MULTIVIEW 时）
        character_image_url = image_result.image_url if image_result and image_result.success else None
        if character_image_url and ENABLE_MULTIVIEW:
            _, multi_view_messages = await _generate_multi_view_for_character(
                character=character,
                character_uuid=character_uuid,
                version_uuid=version_uuid,
                character_image_url=character_image_url,
                story_outline=story_outline,
                user_option=user_option,
                state=state,
                reference_images=images,
                user_input=user_input,
            )
            if multi_view_messages:
                new_messages.extend(multi_view_messages)
        
        # 返回结果（保持与原函数相同的格式）
        return {
            "uuid": character_uuid,
            "message": new_messages
        }
        
    except Exception as e:
        # 数据库操作失败或其他未预期的错误，直接抛出
        logger.error(f"❌ 角色 {character.name} 处理失败: {str(e)}")
        raise


async def batch_generate_character_images(
    characters: List[CharacterProfile], 
    images: List[ImageUserInput], 
    user_option: Optional[UserOption],
    story_outline: StoryOutline, 
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """批量生成角色图片并保存到数据库（使用asyncpg CRUD）
    
    注意：使用 asyncpg CRUD，不需要数据库session；
    预加载 prompt 模板避免每个角色重复加载；LLM/agent 由 llm_resilience 按路由创建。
    """
    from ....models.tool_enums import ToolMode

    new_character_uuids = []
    all_messages = []
    batch_size = 10  # 每批处理10个角色

    total_characters = len(characters)
    logger.info(f"🎨 开始批量生成{total_characters}个角色的图片（独立session模式），每批{batch_size}个")

    _char_mode = ToolMode.I2I if images else ToolMode.T2I
    logger.info(f"✅ 角色图片生成使用 character-image-tool-director skill（模式: {_char_mode.value}）")

    if ENABLE_MULTIVIEW:
        logger.info("✅ 多视角图使用 character-multiview-tool-director skill")
    
    for i in range(0, total_characters, batch_size):
        # 协作式取消：开新批次前检查，取消后不再启动新一批生成
        await raise_if_cancelled()
        batch_characters = characters[i:i + batch_size]
        batch_num = i // batch_size + 1
        
        logger.info(f"🎨 处理第{batch_num}批角色图片生成，共{len(batch_characters)}个角色")
        
        # 并行生成当前批次的角色图片（传入预加载的资源）
        tasks = []
        for character in batch_characters:
            task = _generate_single_character_image(
                character, images, user_option, story_outline, state,
            )
            tasks.append(task)
        
        # 等待当前批次完成
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果（用 BaseException 兜底：取消时子任务返回的 CancelledError 不是 Exception，避免按成功结果取值崩溃）
        for j, result in enumerate(results):
            character = batch_characters[j]
            if isinstance(result, BaseException):
                logger.error(f"❌ 角色 {character.name} 图片生成异常: {result}")
            else:
                new_character_uuids.append(result["uuid"])
                if result["message"]:
                    all_messages.extend(result["message"])
                logger.info(f"✅ 角色 {character.name} 图片生成完成")
        
        # 协作式取消：本批若是因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
        await raise_if_cancelled()
    
    return {
        "character_uuids": new_character_uuids,
        "messages": all_messages
    }
