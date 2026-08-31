"""
视觉元素匹配服务模块
独立节点：为所有scenes匹配visual elements并更新character_ids
"""
import logging
import asyncio
from typing import List, Optional, Dict, Any, Union, Tuple
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from ....models.video_state import VideoAgentState, StoryboardScene, CharacterProfile
from ....models.user_options import UserOption
from ....exceptions import BusinessException, BusinessExceptionCode
from ..schemas import VideoContextSchema
from ....services.agent.utils.database_utils import get_scenes_from_db, get_characters_from_db
from ....services.agent.utils.prompt_utils import attach_images_to_messages, apply_language_suffix_to_system_message_in_messages
from ..utils.cancellation import raise_if_cancelled
from prompts.prompt_config import PromptName

logger = logging.getLogger(__name__)

# 配置常量：是否跳过视觉元素匹配（默认不跳过）
SKIP_VISUAL_ELEMENTS_MATCHING = False


class ShotInfo(BaseModel):
    """Shot信息，用于批量匹配visual elements"""
    shot_description: str = Field(description="场景描述")
    scene_context: Optional[StoryboardScene] = Field(default=None, description="场景上下文信息（可选）")
    existing_character_ids: List[str] = Field(default_factory=list, description="已有的character_ids（用于prompt提示）")


class MatchedCharacter(BaseModel):
    """单个匹配的visual element"""
    character_index: int = Field(description="在all_characters列表中的编号（从1开始）")
    character_type: str = Field(description="类型：character（人物角色）、object（重要物品）、location（核心场所）")
    confidence: float = Field(description="匹配置信度（0.0-1.0）")
    match_reason: str = Field(description="匹配理由，说明为什么这个元素出现在场景中")


class VisualElementsMatchingResult(BaseModel):
    """视觉元素匹配结果"""
    matched_characters: List[MatchedCharacter] = Field(default_factory=list, description="匹配的visual element详情列表")
    
    def get_character_ids(self, all_characters: List[CharacterProfile]) -> List[str]:
        """
        将character_index转换为character_ids（UUID）
        
        Args:
            all_characters: 所有可用的visual elements列表
        
        Returns:
            character_ids列表（UUID）
        """
        character_ids = []
        for match in self.matched_characters:
            # character_index从1开始，转换为0-based索引
            idx = match.character_index - 1
            if 0 <= idx < len(all_characters):
                char = all_characters[idx]
                # CharacterProfile使用id字段（不是uuid）
                char_id = char.id if hasattr(char, 'id') else None
                if char_id:
                    character_ids.append(char_id)
            else:
                logger.warning(f"⚠️ character_index {match.character_index} 超出范围（共{len(all_characters)}个元素）")
        
        return character_ids


async def match_visual_elements_for_shot(
    shot_description: str,
    scene_context: Optional[StoryboardScene] = None,
    all_characters: List[CharacterProfile] = None,
    existing_character_ids: List[str] = None,
    user_option: Optional[UserOption] = None,
    user_input: str = "",
    detected_language: Optional[str] = None
) -> VisualElementsMatchingResult:
    """
    为单个shot匹配visual elements（单数版本，供video_agent_service使用）
    
    Args:
        shot_description: 场景描述
        scene_context: 场景上下文信息（可选）
        all_characters: 所有可用的visual elements列表
        existing_character_ids: 已有的character_ids（用于prompt提示）
        user_option: 用户选项
        user_input: 用户输入
    
    Returns:
        匹配结果，包含匹配的character_ids
    """
    if not all_characters:
        logger.warning("⚠️ 没有可用的visual elements，返回空结果")
        return VisualElementsMatchingResult()
    
    # 使用批量函数处理单个shot
    shot_info = ShotInfo(
        shot_description=shot_description,
        scene_context=scene_context,
        existing_character_ids=existing_character_ids or []
    )
    
    # 注意：batch_match_visual_elements_for_shots 内部会自己获取 LLM，不需要传入 llm 参数
    results, _ = await batch_match_visual_elements_for_shots(
        shots_batch=[shot_info],
        all_characters=all_characters,
        user_option=user_option,
        user_input=user_input,
        detected_language=detected_language
    )
    
    return results[0] if results else VisualElementsMatchingResult()


async def batch_match_visual_elements_for_shots(
    shots_batch: List[ShotInfo],
    all_characters: List[CharacterProfile],
    user_option: Optional[UserOption] = None,
    user_input: str = "",
    detected_language: Optional[str] = None
) -> Tuple[List[VisualElementsMatchingResult], List[BaseMessage]]:
    """
    批量匹配visual elements
    
    Args:
        shots_batch: Shot信息列表
        all_characters: 所有可用的visual elements列表
        user_option: 用户选项
        user_input: 用户输入
    
    Returns:
        Tuple[List[VisualElementsMatchingResult], List[BaseMessage]]: (匹配结果列表, 所有 messages)
    """
    if not shots_batch:
        return [], []
    
    if not all_characters:
        logger.warning("⚠️ 没有可用的visual elements，返回空结果")
        return [VisualElementsMatchingResult() for _ in shots_batch], []
    
    results = []
    from app.services.agent.video.visual_match_stage import (
        export_visual_match_inputs,
        generate_visual_match_via_deep_agent,
    )
    import uuid as _uuid
    tid = f"vm_{_uuid.uuid4().hex[:8]}"
    rid = f"run_{_uuid.uuid4().hex[:8]}"
    scenes_payload = []
    for shot_info in shots_batch:
        sc = shot_info.scene_context
        scenes_payload.append({
            "scene_number": getattr(sc, "scene_number", 0) if sc else 0,
            "title": getattr(sc, "title", "") if sc else "",
            "description": shot_info.shot_description or (getattr(sc, "description", "") if sc else ""),
            "existing_character_ids": list(shot_info.existing_character_ids or []),
        })
    paths = export_visual_match_inputs(
        thread_id=tid, run_id=rid, batch_id="batch0",
        scenes=scenes_payload, characters=all_characters, user_input=user_input,
    )
    art, msgs = await generate_visual_match_via_deep_agent(
        thread_id=tid, run_id=rid, input_paths=paths, detected_language=detected_language,
    )
    by_sn = {s.scene_number: s for s in art.scenes}
    for shot_info in shots_batch:
        sc = shot_info.scene_context
        sn = getattr(sc, "scene_number", 0) if sc else 0
        match = by_sn.get(sn)
        if match:
            results.append(VisualElementsMatchingResult(
                matched_characters=[
                    MatchedCharacter(
                        character_index=m.character_index,
                        character_type=m.character_type,
                        confidence=m.confidence,
                        match_reason=m.match_reason,
                    )
                    for m in match.matched_characters
                ]
            ))
        else:
            results.append(VisualElementsMatchingResult())
    return results, list(msgs or [])

    return messages, loaded_llm


async def match_and_update_visual_elements_for_batch(
    current_batch: List[StoryboardScene],
    characters_data: List[CharacterProfile],
    user_option: Optional[UserOption],
    user_input: str,
    detected_language: Optional[str] = None
) -> List[BaseMessage]:
    """
    为批次中的scenes匹配visual elements并更新character_ids
    
    Args:
        current_batch: 当前批次的scenes
        characters_data: 所有可用的角色列表
        user_option: 用户选项
        user_input: 用户输入
    
    Returns:
        List[BaseMessage]: 本次调用的所有 messages
    """
    from ....crud.video.video_story import update_scene
    from ....models.database import AsyncSessionLocal
    
    # 准备批量匹配的数据
    shots_batch = []
    for scene in current_batch:
        shots_batch.append(ShotInfo(
            shot_description=scene.description,
            scene_context=scene,
            existing_character_ids=scene.character_ids or []  # scene已有的character_ids（用于prompt提示）
        ))
    
    # 批量匹配visual elements（返回 messages）
    matching_results, all_messages = await batch_match_visual_elements_for_shots(
        shots_batch=shots_batch,
        all_characters=characters_data,
        user_option=user_option,
        user_input=user_input,
        detected_language=detected_language,
    )
    
    # 更新每个scene的character_ids（merge scene已有的 + 新匹配的）
    for i, scene in enumerate(current_batch):
        matching_result = matching_results[i]
        
        # 将character_index转换为character_ids（UUID）
        new_character_ids = matching_result.get_character_ids(characters_data)
        
        # Merge: scene已有的character_ids + 新匹配的character_ids
        scene_character_ids = scene.character_ids or []
        
        # 找出新增的character_ids（之前没有的）
        scene_character_ids_set = set(scene_character_ids)
        new_character_ids_set = set(new_character_ids)
        added_character_ids = list(new_character_ids_set - scene_character_ids_set)
        
        # 有序合并去重，然后按类型排序：角色(character) > 物品(object) > 场所(location)
        seen = set()
        merged_unordered = []
        for cid in scene_character_ids + new_character_ids:
            if cid not in seen:
                seen.add(cid)
                merged_unordered.append(cid)
        
        char_type_map = {c.id: getattr(c.type, 'value', str(c.type)) for c in characters_data}
        _type_priority = {"character": 0, "object": 1, "location": 2}
        merged_character_ids = sorted(
            merged_unordered,
            key=lambda cid: _type_priority.get(char_type_map.get(cid, "character"), 3)
        )
        
        # 更新scene的character_ids（内存中）
        scene.character_ids = merged_character_ids
        
        # 记录新增的character_ids
        if added_character_ids:
            logger.info(f"✨ Scene {scene.scene_number} 新增匹配: {len(added_character_ids)}个新character_ids (原有{len(scene_character_ids)}个, 新增{len(added_character_ids)}个, 合并后{len(merged_character_ids)}个)")
        else:
            logger.info(f"📊 Scene {scene.scene_number} 无新增匹配: 原有{len(scene_character_ids)}个, 新匹配{len(new_character_ids)}个(均为已有), 合并后{len(merged_character_ids)}个")
        
        # ⭐ 更新scene到数据库（使用asyncpg CRUD）
        if scene.uuid:
            try:
                await update_scene(
                    scene_uuid=scene.uuid,
                    scene_data={"character_ids": merged_character_ids}
                )
                logger.info(f"✅ Scene {scene.scene_number} character_ids已更新到数据库: {len(merged_character_ids)}个")
            except Exception as e:
                logger.warning(f"⚠️ Scene {scene.scene_number} 更新到数据库失败: {e}")
        
        logger.info(f"🎯 Scene {scene.scene_number} Visual Elements匹配: scene已有{len(scene_character_ids)}个, 新匹配{len(new_character_ids)}个, 合并后{len(merged_character_ids)}个")
    
    return all_messages


async def visual_elements_matching_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any
) -> Union[VideoAgentState, Dict[str, Any]]:
    """视觉元素匹配节点
    
    功能：为所有scenes匹配visual elements并更新character_ids
    位置：在scene_generation_node之后，storyboard_detail_generation_node之前
    
    Args:
        state: 视频代理状态
        runtime: 运行时上下文
        llm: LLM实例（保留参数以保持接口兼容，但实际不使用）
        send_event_func: 事件发送函数（保留参数以保持接口兼容）
    
    Returns:
        更新后的状态，包含 messages
    """
    try:
        logger.info(f"🔍 开始视觉元素匹配节点，conversation_id: {state.get('conversation_id')}")
        
        # 检查是否跳过视觉元素匹配
        if SKIP_VISUAL_ELEMENTS_MATCHING:
            logger.info("⏭️ 跳过视觉元素匹配（SKIP_VISUAL_ELEMENTS_MATCHING=True）")
            return {"messages": []}
        
        # 获取所有scenes
        scene_uuids = state.get("scene_uuids", [])
        if not scene_uuids:
            logger.warning("⚠️ 没有scene_uuids，跳过视觉元素匹配")
            return {"messages": []}
        
        # ✅ 使用asyncpg CRUD，不需要数据库连接
        scenes_data = await get_scenes_from_db(scene_uuids)
        
        if not scenes_data:
            logger.warning("⚠️ 未找到scenes数据，跳过视觉元素匹配")
            return {"messages": []}
        
        logger.info(f"📊 获取到 {len(scenes_data)} 个scenes，开始匹配visual elements")
        
        # 获取角色信息
        character_uuids = state.get("character_uuids", [])
        if not character_uuids:
            logger.warning("⚠️ 没有character_uuids，跳过视觉元素匹配")
            return {"messages": []}
        
        characters_data = await get_characters_from_db(character_uuids)
        logger.info(f"👥 获取到 {len(characters_data)} 个角色")
        
        # 获取用户输入和选项
        user_input_data = state.get("user_input_data")
        user_option = user_input_data.user_option if user_input_data else None
        user_input = user_input_data.user_input if user_input_data else ""
        
        # 批量匹配（并发处理，避免串行等待）
        batch_size = 10  # 每批处理10个scenes
        total_scenes = len(scenes_data)
        all_messages = []
        
        # 创建批次列表
        batches = []
        for i in range(0, total_scenes, batch_size):
            batch = scenes_data[i:i+batch_size]
            batch_num = i // batch_size + 1
            batches.append({
                'batch': batch,
                'batch_num': batch_num
            })
        
        # ✅ 添加并发限制（使用 semaphore 控制批次并发数）
        from ..utils.prompt_utils import get_concurrency_limit
        max_concurrent_batches = get_concurrency_limit("visual_elements_matching")
        semaphore = asyncio.Semaphore(max_concurrent_batches)
        logger.info(f"📊 Visual Elements匹配: {len(batches)} 个批次，最大并发: {max_concurrent_batches}")
        
        # 并发处理所有批次（带并发限制）
        async def process_batch(batch_info: Dict[str, Any]) -> List[BaseMessage]:
            async with semaphore:  # ✅ 使用 semaphore 限制并发
                batch = batch_info['batch']
                batch_num = batch_info['batch_num']
                total_batches = len(batches)
                
                logger.info(f"🔍 处理第{batch_num}/{total_batches}批，共{len(batch)}个scenes")
                
                # 匹配并更新visual elements（内部会为每个scene创建独立的数据库session）
                batch_messages = await match_and_update_visual_elements_for_batch(
                    current_batch=batch,
                    characters_data=characters_data,
                    user_option=user_option,
                    user_input=user_input,
                    detected_language=state.get("detected_language")
                )
                
                logger.info(f"✅ 第{batch_num}批匹配完成")
                return batch_messages
        
        # 并发执行所有批次（受 semaphore 限制）
        batch_results = await asyncio.gather(*[process_batch(batch_info) for batch_info in batches])
        
        # 协作式取消：若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
        await raise_if_cancelled()
        
        # 收集所有messages
        for batch_messages in batch_results:
            all_messages.extend(batch_messages)
        
        logger.info(f"✅ 视觉元素匹配完成，共处理 {total_scenes} 个scenes")
        return {"messages": all_messages}
        
    except BusinessException as e:
        logger.error(f"视觉元素匹配失败: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"视觉元素匹配失败: {e}")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"视觉元素匹配失败: {str(e)}"
        )
