"""
详细分镜生成功能模块
负责详细分镜生成节点的实现和相关功能
"""
import logging
import asyncio
from typing import List, Optional, Dict, Any, Union, cast, Tuple
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from ....models.video_state import VideoAgentState, StoryOutline, VideoAnalysisResult, StoryboardScene, CharacterProfile, StoryboardDetailLLMOutput, ContentCategory
from ....models.user_options import UserOption
from ....exceptions import BusinessException, BusinessExceptionCode
from ....crud.video.video_story import create_storyboard_detail, create_detailed_shot, get_detailed_shots_by_conversation
from ....services.agent.base_agent import MessageType
from ..schemas import VideoContextSchema
from ....services.agent.utils.prompt_utils import (
    apply_language_suffix_to_system_message_in_messages,
    attach_images_to_messages,
    generate_completion_message_stream,
)
from ....services.agent.utils.database_utils import get_story_outline_from_db, get_characters_from_db, get_video_analysis_from_db, get_scenes_from_db
from ..utils.cancellation import raise_if_cancelled
from prompts.prompt_config import PromptName

logger = logging.getLogger(__name__)


async def storyboard_detail_generation_node(state: VideoAgentState,
                                            runtime: Runtime[VideoContextSchema],
                                            send_event_func: Any) -> Union[VideoAgentState, Dict[str, Any]]:
    """详细分镜生成节点 - 基于场景批量生成详细镜头"""

    try:
        # 获取基础信息
        story_outline_uuid = state.get("story_outline_uuid")
        scene_uuids = state.get("scene_uuids", [])
        character_uuids = state.get("character_uuids", [])
        analysis_uuid = state.get("analysis_uuid")

        if not story_outline_uuid:
            raise BusinessException(
                BusinessExceptionCode.VIDEO_ANALYSIS_UUID_MISSING,
                "缺少故事梗概UUID"
            )

        if not scene_uuids:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "缺少场景数据，无法生成详细分镜"
            )

        # ✅ 修复：使用asyncpg CRUD，不需要数据库连接
        story_outline = await get_story_outline_from_db(story_outline_uuid)
        scenes_data = await get_scenes_from_db(scene_uuids)
        characters_data = await get_characters_from_db(character_uuids)
        analysis_data = await get_video_analysis_from_db(analysis_uuid) if analysis_uuid else None
        user_input_data = state.get("user_input_data")
        images = user_input_data.images if user_input_data else []
        user_option = user_input_data.user_option if user_input_data else None
        user_input = user_input_data.user_input if user_input_data else ""
        _cc = user_option.content_category if user_option else None
        is_product_launch = _cc == ContentCategory.PRODUCT_LAUNCH
        is_short_drama = _cc == ContentCategory.SHORT_DRAMA

        logger.info(f"🎬 开始基于{len(scenes_data)}个场景生成详细分镜")

        thread_id = str(state.get("thread_id") or "")
        run_id = str(state.get("run_id") or "")
        if not (thread_id and run_id):
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "缺少 thread_id/run_id，无法走 storyboard deep-agent",
            )

        # 幂等：若本 thread 下这批 scene 都已生成过详细分镜（多为上一次被取消/中断的 run 已生成），
        # 直接复用、跳过重复 LLM 生成与重复入库，避免在续跑时把分镜（以及下游关键帧/视频）翻倍。
        scene_id_set = {s.uuid for s in scenes_data}
        if scene_id_set:
            existing_shots = await get_detailed_shots_by_conversation(
                str(state["conversation_id"]), str(state["thread_id"])
            )
            relevant_shots = [sh for sh in existing_shots if getattr(sh, "scene_id", None) in scene_id_set]
            covered_scene_ids = {getattr(sh, "scene_id", None) for sh in relevant_shots}
            if scene_id_set.issubset(covered_scene_ids):
                relevant_shots.sort(key=lambda s: getattr(s, "shot_number", 0))
                ordered_shot_uuids = [sh.uuid for sh in relevant_shots]
                logger.info(
                    "♻️ storyboard_detail 幂等：thread=%s 全部 %d 个场景已有分镜（%d 个 shot），复用并跳过重复生成",
                    state.get("thread_id"), len(scene_id_set), len(ordered_shot_uuids),
                )
                reuse_messages: List[BaseMessage] = []
                user_message, completion_message = await generate_completion_message_stream(
                    event_type=MessageType.STORYBOARD_DETAIL_GENERATED,
                    messages=reuse_messages,
                    send_event_func=send_event_func,
                    conversation_id=state.get("conversation_id"),
                    lang=state.get("detected_language"),
                )
                if completion_message:
                    reuse_messages.append(completion_message)
                await send_event_func(
                    conversation_id=state["conversation_id"],
                    event_type=MessageType.STORYBOARD_DETAIL_GENERATED,
                    message=user_message,
                    extra_data={
                        "shot_uuids": ordered_shot_uuids,
                        "run_id": state.get("run_id"),
                        "thread_id": state.get("thread_id"),
                        "reused": True,
                    },
                )
                return {
                    "shot_uuids": ordered_shot_uuids,
                    "messages": reuse_messages,
                }

        # 批量处理策略：每次处理多个场景，根据剩余时长智能调整
        total_scenes = len(scenes_data)
        
        # 从配置中获取批处理大小
        from ..utils.prompt_utils import CONCURRENCY_LIMITS, get_concurrency_limit, add_random_delay
        batch_size = CONCURRENCY_LIMITS.get("storyboard_detail_batch_size", 5)
        
        context_size = 2  # 前后各2个场景作为上下文信息
        max_concurrent = get_concurrency_limit("storyboard_detail_generation")

        logger.info(f"🎬 开始并行处理{total_scenes}个场景的详细分镜，每批最多{batch_size}个，最大并发{max_concurrent}")

        # 创建批次列表
        batches = []
        processed_scenes = 0
        batch_index = 0
        
        while processed_scenes < total_scenes:
            batch_index += 1
            # 确定当前批次要处理的场景数量（根据剩余时长智能调整，最多10个）
            current_batch_size = min(batch_size, total_scenes - processed_scenes)
            current_batch = scenes_data[processed_scenes:processed_scenes + current_batch_size]

            # 获取上下文场景（分为前文和后文）
            # 前文场景：前context_size个场景（不包括当前批次）
            prev_start = max(0, processed_scenes - context_size)
            prev_scenes = scenes_data[prev_start:processed_scenes] if processed_scenes > 0 else []

            # 后文场景：后context_size个场景（不包括当前批次）
            next_start = processed_scenes + current_batch_size
            next_end = min(total_scenes, next_start + context_size)
            next_scenes = scenes_data[next_start:next_end] if next_start < total_scenes else []

            # 构建上下文字典
            context_scenes = {
                'prev': prev_scenes,
                'next': next_scenes
            }

            batches.append({
                'batch_index': batch_index,
                'batch': current_batch,
                'context_scenes': context_scenes,
                'start_index': processed_scenes
            })

            processed_scenes += current_batch_size

        logger.info(f"🎬 共创建{len(batches)}个批次，开始并行生成")

        # 定义单个批次的处理函数
        async def process_batch(batch_info: Dict[str, Any], semaphore: asyncio.Semaphore) -> Dict[str, Any]:
            """处理单个批次"""
            # 添加随机延迟避免并发请求过于集中
            await add_random_delay()
            
            async with semaphore:
                batch_index = batch_info['batch_index']
                current_batch = batch_info['batch']
                context_scenes = batch_info['context_scenes']
                
                # 确定当前批次的场景编号范围
                current_start_scene = current_batch[0].scene_number if current_batch else 0
                current_end_scene = current_batch[-1].scene_number if current_batch else 0

                logger.info(f"🎬 批次{batch_index}：处理场景{current_start_scene}-{current_end_scene}（共{len(current_batch)}个场景），前文{len(context_scenes['prev'])}个场景，后文{len(context_scenes['next'])}个场景")

                from app.services.agent.video.storyboard_stage import (
                    export_storyboard_batch_inputs,
                    generate_storyboard_batch_via_deep_agent,
                )
                _batch_id = f"b{batch_index}"
                _cc = user_option.content_category if user_option else None
                _img_urls = []
                for img in (images or []):
                    u = getattr(img, "url", None) or (img if isinstance(img, str) else None)
                    if u:
                        _img_urls.append(u)
                _paths = export_storyboard_batch_inputs(
                    thread_id=thread_id,
                    run_id=run_id,
                    batch_id=_batch_id,
                    story_outline=story_outline,
                    scenes=current_batch,
                    characters=characters_data,
                    analysis=analysis_data,
                    user_input=user_input,
                    prev_scenes=context_scenes.get("prev") or [],
                    next_scenes=context_scenes.get("next") or [],
                    is_lip_sync_mv=_cc == ContentCategory.LIP_SYNC_MV,
                    is_product_launch=_cc == ContentCategory.PRODUCT_LAUNCH,
                    reference_image_urls=_img_urls,
                )
                storyboard_detail_llm, all_messages = await generate_storyboard_batch_via_deep_agent(
                    thread_id=thread_id,
                    run_id=run_id,
                    input_paths=_paths,
                    detected_language=state.get("detected_language"),
                )

                logger.info(f"🎬 批次{batch_index}生成了{len(storyboard_detail_llm.shots)}个详细镜头，返回 {len(all_messages)} 条消息")

                return {
                    'batch_index': batch_index,
                    'storyboard_detail_llm': storyboard_detail_llm,
                    'all_messages': all_messages,
                    'current_batch': current_batch
                }

        # 创建信号量限制并发数
        semaphore = asyncio.Semaphore(max_concurrent)
        
        # 并行执行所有批次
        tasks = [process_batch(batch_info, semaphore) for batch_info in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 协作式取消：若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
        await raise_if_cancelled()

        # 按批次索引排序结果
        sorted_results = []
        for result in results:
            if isinstance(result, BaseException):
                logger.error(f"批次处理异常: {result}")
                continue
            if result:
                sorted_results.append(result)
        
        sorted_results.sort(key=lambda x: x['batch_index'])

        # ✅ 使用asyncpg CRUD，不需要数据库连接
        all_shot_uuids = []
        all_messages = []
        
        for i, result in enumerate(sorted_results):
            storyboard_detail_llm = result['storyboard_detail_llm']
            result_messages = result['all_messages']
            current_batch = result['current_batch']
            batch_index = result['batch_index']

            all_messages.extend(result_messages)

            # 保存详细分镜到数据库
            # 创建详细分镜记录（total_duration 直接继承自 story_outline）
            storyboard_detail_uuid = await create_storyboard_detail(
                story_outline_id=story_outline_uuid,
                total_duration=story_outline.total_duration,  # 直接继承 total_duration
                visual_style=story_outline.style_guide,
                shots_count=len(storyboard_detail_llm.shots),
                conversation_id=str(state["conversation_id"]),
                thread_id=str(state["thread_id"]),
                run_id=state["run_id"],
                user_id=state["user_id"]
            )

            # 创建 scene_number -> scene 的映射，用于快速查找
            scene_map = {scene.scene_number: scene for scene in current_batch}
            characters_by_id = {character.id: character for character in characters_data}

            # 创建详细镜头记录
            current_shot_uuids = []
            for shot_llm in storyboard_detail_llm.shots:
                # 🎯 根据 shot_number 找到对应的 scene
                scene = scene_map.get(shot_llm.shot_number)
                if not scene:
                    logger.error(f"❌ 镜头{shot_llm.shot_number}找不到对应的场景")
                    continue
                
                scene_uuid = scene.uuid
                
                # 🎯 从 scene 直接继承固定字段，从 LLM 输出获取细节字段
                shot_data = {
                    "shot_number": scene.scene_number,  # 继承 scene_number
                    "duration": scene.duration,  # 继承 duration
                    "shot_type": shot_llm.shot_type,
                    "camera_position": shot_llm.camera_position,  # 新增：相机机位
                    "camera_angle": shot_llm.camera_angle,  # 新增：相机角度
                    "subject_angle": shot_llm.subject_angle,  # 新增：主体角度
                    "subject_pose": shot_llm.subject_pose,  # 新增：主体姿势
                    "scene_description": shot_llm.scene_description,
                    "camera_movement": shot_llm.camera_movement,
                    "lighting": shot_llm.lighting,
                    "visual_effects": shot_llm.visual_effects,
                    "transition": shot_llm.transition,
                    "dialogue": shot_llm.dialogue,
                    "sound_effects": shot_llm.sound_effects,
                    "is_bridge": scene.is_bridge,  # 继承 is_bridge
                    "character_ids": scene.character_ids,  # 继承 character_ids
                    "audio_segment_ids": scene.audio_segment_ids,  # 继承 audio_segment_ids
                    "style_guide": scene.visual_style or story_outline.style_guide if story_outline else None,  # 继承 visual_style
                    "narration": shot_llm.narration
                }

                # 从 scene 直接继承 generation_mode
                _scene_gen_mode = scene.generation_mode
                if is_product_launch:
                    from ....models.tool_enums import GenerationMode
                    from ....models.video_state import VisualElementType
                    # 仅回填 dialogue；空旁白表示 B-roll/空镜，勿用 scene_description 否则会被 TTS 误读
                    if not (shot_data.get("narration") or "").strip():
                        shot_data["narration"] = (shot_llm.dialogue or "").strip()
                    _has_speech = bool((shot_data.get("narration") or "").strip())
                    if _has_speech:
                        _has_person = any(
                            characters_by_id.get(cid)
                            and characters_by_id[cid].type == VisualElementType.CHARACTER
                            for cid in (shot_data.get("character_ids") or [])
                        )
                        _scene_gen_mode = (
                            GenerationMode.LIPSYNC.value
                            if _has_person
                            else GenerationMode.NORMAL.value
                        )
                    else:
                        _scene_gen_mode = GenerationMode.NORMAL.value
                else:
                    # 短剧等：对白≠旁白；禁止 dialogue→narration 拷贝
                    pass

                from .voice_delivery_contract import (
                    infer_contract_from_dialogue_narration,
                    merge_voice_into_additional_data,
                )
                sp = getattr(shot_llm, "speaker_directions", None)
                dn = getattr(shot_llm, "delivery_note", None)
                if is_short_drama or not is_product_launch:
                    if not (sp and str(sp).strip()) or not (dn and str(dn).strip()):
                        sp2, dn2 = infer_contract_from_dialogue_narration(
                            shot_data.get("dialogue"),
                            shot_data.get("narration"),
                        )
                        sp = sp or sp2
                        dn = dn or dn2

                narration_gender = None
                additional_data = None
                if (shot_data.get("narration") or "").strip():
                    from .narration_gender_utils import (
                        NARRATION_GENDER_KEY,
                        resolve_narration_gender_for_detail_shot,
                    )
                    narration_gender = resolve_narration_gender_for_detail_shot(
                        shot_llm,
                        shot_data.get("character_ids"),
                        characters_by_id,
                        shot_data.get("narration"),
                    )
                    if narration_gender:
                        additional_data = {NARRATION_GENDER_KEY: narration_gender}

                if not is_product_launch:
                    additional_data = merge_voice_into_additional_data(
                        additional_data,
                        speaker_directions=sp,
                        delivery_note=dn,
                        provider_text=(shot_data.get("narration") or None),
                    )
                    from .visual_field_contract import (
                        fold_machine_visual_fields,
                        inherit_scene_visual_fields_to_shot_ad,
                    )
                    additional_data = inherit_scene_visual_fields_to_shot_ad(
                        scene, additional_data
                    ) or None
                    additional_data = fold_machine_visual_fields(
                        shot_language=getattr(shot_llm, "shot_language", None),
                        action_beats=getattr(shot_llm, "action_beats", None),
                        hero_moment=getattr(shot_llm, "hero_moment", None),
                        existing=additional_data,
                    ) or None
                
                shot_uuid = await create_detailed_shot(
                    story_outline_id=story_outline_uuid,
                    storyboard_detail_id=storyboard_detail_uuid,
                    scene_id=scene_uuid,
                    shot_number=shot_data["shot_number"],
                    shot_type=shot_data.get("shot_type", ""),
                    camera_angle=shot_data.get("camera_angle") or "",
                    duration=shot_data.get("duration", 0),
                    description=shot_data.get("scene_description", ""),
                    visual_notes=shot_data.get("visual_effects", ""),
                    character_action=shot_data.get("dialogue", ""),
                    transition=shot_data.get("transition", ""),
                    conversation_id=str(state["conversation_id"]),
                    thread_id=str(state["thread_id"]),
                    run_id=state["run_id"],
                    user_id=state["user_id"],
                    character_ids=shot_data.get("character_ids"),
                    is_bridge=shot_data.get("is_bridge", False),
                    audio_segment_ids=shot_data.get("audio_segment_ids"),
                    style_guide=shot_data.get("style_guide"),
                    narration=shot_data.get("narration"),
                    additional_data=additional_data,
                    chapter_id=scene.chapter_id,  # 直接从 scene 继承 chapter_id
                    generation_mode=_scene_gen_mode,  # 从 scene 继承 generation_mode
                    camera_position=shot_data.get("camera_position"),
                    camera_movement=shot_data.get("camera_movement"),
                    lighting=shot_data.get("lighting"),
                    subject_angle=shot_data.get("subject_angle"),
                    subject_pose=shot_data.get("subject_pose"),
                    sound_effects=shot_data.get("sound_effects"),
                )
                current_shot_uuids.append(shot_uuid)

            all_shot_uuids.extend(current_shot_uuids)
            logger.info(f"✅ 批次{batch_index}保存了{len(current_shot_uuids)}个详细镜头到数据库")

        # 发送详细分镜生成完成事件 (使用LLM生成的user_message)
        from ....services.agent.utils.prompt_utils import generate_completion_message_stream
        user_message, completion_message = await generate_completion_message_stream(
            event_type=MessageType.STORYBOARD_DETAIL_GENERATED,
            messages=all_messages,
            send_event_func=send_event_func,
            conversation_id=state.get("conversation_id"),
            lang=state.get("detected_language")
        )
        if completion_message:
            all_messages.append(completion_message)

        await send_event_func(
            conversation_id=state["conversation_id"],
            event_type=MessageType.STORYBOARD_DETAIL_GENERATED,
            message=user_message,
            extra_data={
                "shot_uuids": all_shot_uuids,
                "run_id": state.get("run_id"),
                "thread_id": state.get("thread_id")
            }
        )

        return {
            "shot_uuids": all_shot_uuids,
            "messages": all_messages
        }

    except BusinessException as e:
        logger.error(f"storyboard_detail_generation_node:业务异常 - {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"storyboard_detail_generation_node:未知异常 - {str(e)}")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"详细分镜生成失败: {str(e)}"
        )
