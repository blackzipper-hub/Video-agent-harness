"""
音效生成功能模块
负责音效生成节点的实现和相关功能
"""
import logging
import asyncio
from typing import List, Optional, Dict, Any, Union, cast, TYPE_CHECKING
from langchain_core.messages import BaseMessage
from langgraph.runtime import Runtime
from datetime import datetime

if TYPE_CHECKING:
    from ....schemas.video.video_generation import VideoGenerationDB, VideoGenerationVersionDB

from ....models.video_state import VideoAgentState, AudioEffectVersion, AudioEffectWithVersions
from ....exceptions import BusinessException, BusinessExceptionCode
from ....services.agent.base_agent import MessageType
from ..schemas import VideoContextSchema
from ....services.agent.utils.database_utils import save_audio_effect_to_db, get_completed_audio_effect_uuids_by_shot_number
from ..utils.message_utils import extract_ai_message_json
from ..utils.cancellation import raise_if_cancelled

logger = logging.getLogger(__name__)


async def generate_single_audio_effect(
    video_gen: "VideoGenerationDB",
    video_version: "VideoGenerationVersionDB",
    story_outline_uuid: str,
    state: VideoAgentState,
) -> tuple[AudioEffectVersion, List[BaseMessage]]:
    """生成单个视频的音效 - ``create_react_agent`` + 工具经 llm_resilience。"""
    try:
        logger.info(f"🎵 为视频{video_gen.shot_number}生成音效")
        
        from langchain_core.messages import SystemMessage, HumanMessage
        from app.orchestration.skills.prompt_context import (
            facts_human_message,
            skill_system_message,
        )

        system = skill_system_message(
            "audio-effect-tool-director",
            lead="Follow audio-effect-tool-director.",
        )
        facts = {
            "shot_number": video_gen.shot_number,
            "is_bridge": bool(video_gen.is_bridge),
            "duration": video_version.duration,
            "video_url": video_version.video_url,
            "motion_prompt": getattr(video_version, "motion_prompt", "") or "",
            "prompt": getattr(video_version, "prompt", "") or "",
        }
        tool_prompt_messages = [
            SystemMessage(content=system),
            HumanMessage(content=facts_human_message(facts)),
        ]
        
        # 强制语言：按 state 中的 detected_language 输出
        detected_language = state.get("detected_language") if isinstance(state, dict) else None
        from ....services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
        apply_language_suffix_to_system_message_in_messages(tool_prompt_messages, detected_language)
        
        from ....models.image_result import AudioGenerationResult
        from ....tools.audioeffect.mmaudio import get_mmaudio_tools
        from prompts.prompt_config import PROMPTS_CONFIG, PromptName
        from ....services.agent.utils.llm_resilience import (
            StructuredResilienceKind,
            ainvoke_structured_resilient,
        )

        result = await ainvoke_structured_resilient(
            prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_AUDIO_EFFECT_GENERATION],
            kind=StructuredResilienceKind.CREATE_REACT_AGENT,
            agent_inputs={"messages": tool_prompt_messages},
            agent_tools=get_mmaudio_tools(),
        )
        
        # ✅ 获取完整的 agent messages
        # 返回本次LLM调用的 input messages + output messages
        agent_full_messages = result.get("messages", [])
        input_message_count = len(tool_prompt_messages)
        output_messages = agent_full_messages[input_message_count:] if len(agent_full_messages) > input_message_count else []
        
        # 返回本次调用的 input messages + output messages
        agent_messages = tool_prompt_messages + output_messages
        
        # 获取结构化响应
        structured_response : AudioGenerationResult = cast(AudioGenerationResult, result.get("structured_response"))
        
        # 保存AIMessage（包含工具调用参数）
        ai_messages_json = extract_ai_message_json(result)
        
        if not structured_response:
            raise Exception("未获取到结构化响应")
        
        # 创建音效版本对象
        if structured_response.success:
            audio_effect_version = AudioEffectVersion(
                version_number=1,
                shot_number=video_gen.shot_number,
                video_url=video_version.video_url,
                audio_prompt=structured_response.generated_prompt or "",
                enhanced_prompt=structured_response.generated_prompt or "",
                audio_url=structured_response.audio_url,
                video_with_audio_url=structured_response.video_with_audio_url,
                provider=structured_response.provider,
                duration=structured_response.duration,
                params={
                    "guidance_scale": structured_response.guidance_scale,
                    "num_inference_steps": structured_response.num_inference_steps
                } if hasattr(structured_response, 'guidance_scale') else None,
                is_bridge=video_gen.is_bridge,
                success=True,
                created_at=datetime.now().isoformat(),
                ai_messages_json=ai_messages_json
            )
        else:
            audio_effect_version = AudioEffectVersion(
                version_number=1,
                shot_number=video_gen.shot_number,
                video_url=video_version.video_url,
                audio_prompt=structured_response.generated_prompt or "",
                enhanced_prompt=structured_response.generated_prompt or "",
                audio_url="",
                provider="wavespeed",
                duration=None,
                params=None,
                is_bridge=video_gen.is_bridge,
                success=False,
                error_msg=structured_response.message or "音效生成失败",
                created_at=datetime.now().isoformat(),
                ai_messages_json=ai_messages_json
            )
        
        return audio_effect_version, agent_messages
        
    except Exception as e:
        logger.error(f"❌ 视频{video_gen.shot_number} 音效生成异常: {e}")
        # 返回失败的音效记录
        failed_audio_effect = AudioEffectVersion(
            version_number=1,
            shot_number=video_gen.shot_number,
            video_url=video_version.video_url if video_version else "",
            audio_prompt="",
            enhanced_prompt="",
            audio_url="",
            provider="wavespeed",
            duration=None,
            params=None,
            is_bridge=video_gen.is_bridge if video_gen else False,
            success=False,
            error_msg=str(e),
            created_at=datetime.now().isoformat()
        )
        return failed_audio_effect, []


async def audio_effect_generation_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any
) -> Union[VideoAgentState, Dict[str, Any]]:
    """音效生成节点 - 为视频生成音效（prompt + LLM 在 generate_single_audio_effect 内按需 load）"""
    try:
        logger.info("🎵 开始音效生成节点")
        
        # ✅ 检查是否需要生成音效
        generation_config = state.get("generation_config")
        if generation_config and not generation_config.generate_audio_effect:
            logger.info(f"⏭️ 跳过音效生成: {generation_config.reason}")
            return {}
        
        # 获取必要的数据
        story_outline_uuid = state.get("story_outline_uuid")
        video_generation_uuids = state.get("video_generation_uuids", [])
        conversation_id = state.get("conversation_id")  # 保持原始类型（int）
        thread_id = state.get("thread_id")
        run_id = state.get("run_id")
        user_id = state.get("user_id")
        
        if not story_outline_uuid:
            logger.warning("缺少故事大纲UUID，跳过音效生成")
            return {"audio_effects_with_versions": []}
        
        if not video_generation_uuids:
            logger.warning("缺少视频生成UUID，跳过音效生成")
            return {"audio_effects_with_versions": []}
        
        # ✅ 使用asyncpg CRUD，不需要数据库连接
        # 获取视频生成数据和详细镜头数据
        from ....crud.video.video_generation import get_video_generations_by_uuids, get_video_generation_versions_by_video_generation_ids
        
        video_generations = await get_video_generations_by_uuids(video_generation_uuids)
        if not video_generations:
            logger.warning("未找到视频生成数据")
            return {"audio_effects_with_versions": []}
        
        # 获取视频生成版本数据
        video_generation_ids = [vg.uuid for vg in video_generations]
        video_versions = await get_video_generation_versions_by_video_generation_ids(video_generation_ids)
        
        # 按视频生成ID分组版本数据
        versions_by_video_gen = {}
        for version in video_versions:
            if version.video_generation_id not in versions_by_video_gen:
                versions_by_video_gen[version.video_generation_id] = []
            versions_by_video_gen[version.video_generation_id].append(version)
        
        # 筛选需要音效的视频
        videos_need_audio_effects = []
        for video_gen in video_generations:
            versions = versions_by_video_gen.get(video_gen.uuid, [])
            if versions:
                # 使用当前版本的视频
                current_version = versions[video_gen.current_version_index] if video_gen.current_version_index < len(versions) else versions[0]
                if current_version.success and current_version.video_url:
                    videos_need_audio_effects.append((video_gen, current_version))
        
        if not videos_need_audio_effects:
            logger.info("没有需要生成音效的视频")
            return {"audio_effects_with_versions": []}

        logger.info(f"🎵 找到 {len(videos_need_audio_effects)} 个需要生成音效的视频")

        # ♻️ 断点续跑幂等：跳过本 outline 下「音效已成功」的镜头，避免取消/恢复后重复生成
        already_done_audio_effect_uuids: List[str] = []
        try:
            _done_by_shot = await get_completed_audio_effect_uuids_by_shot_number(
                story_outline_uuid,
                str(conversation_id) if conversation_id else "",
                str(thread_id) if thread_id else "",
            )
            if _done_by_shot:
                pending_videos = [
                    (vg, vv) for (vg, vv) in videos_need_audio_effects if vg.shot_number not in _done_by_shot
                ]
                already_done_audio_effect_uuids = [
                    _done_by_shot[vg.shot_number]
                    for (vg, _vv) in videos_need_audio_effects
                    if vg.shot_number in _done_by_shot
                ]
                skipped_count = len(videos_need_audio_effects) - len(pending_videos)
                if skipped_count:
                    logger.info(f"♻️ 断点续跑：跳过 {skipped_count} 个已成功音效的视频")
                videos_need_audio_effects = pending_videos
        except Exception as _skip_err:
            logger.warning(f"⚠️ 音效断点续跑检查失败，按全量生成: {_skip_err}")

        if not videos_need_audio_effects:
            logger.info("ℹ️ 所有视频音效均已生成，无需重复生成")
            return {"audio_effect_uuids": already_done_audio_effect_uuids}

        from prompts.prompt_config import PromptName
        logger.info("✅ 音效生成使用 audio-effect-tool-director skill")

        all_audio_effect_versions = []
        all_audio_effect_uuids = list(already_done_audio_effect_uuids)
        all_messages = []

        batch_size = 5
        total_batches = (len(videos_need_audio_effects) + batch_size - 1) // batch_size

        for batch_idx in range(total_batches):
            # 协作式取消：开新批次前检查，取消后不再启动新一批生成
            await raise_if_cancelled()
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, len(videos_need_audio_effects))
            current_batch = videos_need_audio_effects[start_idx:end_idx]

            logger.info(f"🎵 处理第 {batch_idx + 1}/{total_batches} 批音效生成 ({len(current_batch)} 个视频)")

            tasks = []
            for video_gen, video_version in current_batch:
                task = generate_single_audio_effect(
                    video_gen, video_version, story_outline_uuid, state,
                )
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 协作式取消：本批若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
            await raise_if_cancelled()

            for i, result in enumerate(results):
                video_gen, video_version = current_batch[i]

                if isinstance(result, BaseException):
                    logger.error(f"❌ 视频{video_gen.shot_number} 音效生成异常: {result}")
                    audio_effect_version = AudioEffectVersion(
                        version_number=1,
                        shot_number=video_gen.shot_number,
                        video_url=video_version.video_url,
                        audio_prompt="",
                        enhanced_prompt="",
                        audio_url="",
                        provider="wavespeed",
                        duration=None,
                        params=None,
                        is_bridge=video_gen.is_bridge,
                        success=False,
                        error_msg=str(result),
                        created_at=datetime.now().isoformat()
                    )
                    agent_messages = []
                else:
                    audio_effect_version, agent_messages = result
                    all_messages.extend(agent_messages)

                all_audio_effect_versions.append(audio_effect_version)
                result = audio_effect_version

                try:
                    audio_effect_uuid = await save_audio_effect_to_db(
                        result, video_gen, story_outline_uuid,
                        str(conversation_id) if conversation_id else None, thread_id, run_id, user_id
                    )
                    all_audio_effect_uuids.append(audio_effect_uuid)
                    logger.info(f"✅ 视频{video_gen.shot_number} 音效生成并保存成功")
                except Exception as save_error:
                    logger.error(f"❌ 视频{video_gen.shot_number} 音效保存失败: {save_error}")

        # 构建返回数据
        audio_effects_with_versions = []
        for audio_effect_version in all_audio_effect_versions:
            audio_effect_with_versions = AudioEffectWithVersions(
                shot_number=audio_effect_version.shot_number,
                versions=[audio_effect_version],
                current_index=0,
                is_bridge=audio_effect_version.is_bridge,
                has_audio_effect=True,
                video_generation_id=next((vg.uuid for vg, _ in videos_need_audio_effects if vg.shot_number == audio_effect_version.shot_number), "")
            )
            audio_effects_with_versions.append(audio_effect_with_versions)
        
        logger.info(f"🎵 音效生成完成: {len(audio_effects_with_versions)} 个视频")
        
        # 使用流式完成消息（与其余节点一致）
        from ....services.agent.utils.prompt_utils import generate_completion_message_stream
        from ....services.agent.base_agent import MessageType
        
        detected_language = state.get("detected_language")
        user_message, completion_message = await generate_completion_message_stream(
            event_type=MessageType.AUDIO_EFFECTS_GENERATED,
            messages=all_messages,
            send_event_func=send_event_func,
            conversation_id=state.get("conversation_id"),
            lang=detected_language,
        )
        
        # 收集completion message
        if completion_message:
            all_messages.append(completion_message)
        
        logger.info(f"📝 生成的用户消息: {user_message}")
        
        # 发送音效生成完成事件给前端
        await send_event_func(
            conversation_id=state["conversation_id"],
            event_type=MessageType.AUDIO_EFFECTS_GENERATED,
            message=user_message,  # 使用LLM生成的消息
            extra_data={
                "audio_effect_count": len(audio_effects_with_versions),
                "audio_effect_uuids": all_audio_effect_uuids,
                "success_count": sum(1 for aev in all_audio_effect_versions if aev.success),
                "failed_count": sum(1 for aev in all_audio_effect_versions if not aev.success),
                "run_id": state.get("run_id"),
                "thread_id": state.get("thread_id")
            }
        )
        
        return {
            "audio_effects_with_versions": audio_effects_with_versions,
            "messages": all_messages,
            "audio_effect_uuids": all_audio_effect_uuids
        }
        
    except BusinessException as e:
        logger.error(f"音效生成失败: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"音效生成节点异常: {e}")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"音效生成失败: {str(e)}"
        )

