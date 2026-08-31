"""
旁白生成功能模块
负责旁白生成节点的实现和相关功能
"""
import logging
import asyncio
from typing import List, Optional, Dict, Any, Union, Tuple, cast
from langchain_core.messages import BaseMessage
from langgraph.runtime import Runtime
from datetime import datetime

from ....models.video_state import (
    VideoAgentState,
    StoryOutline,
    CharacterProfile,
    NarrationVersion,
    DetailedShot,
    NarrationWithVersions,
    VisualElementType,
)
from ....models.tool_enums import ContentCategory, GenerationMode
from ....exceptions import BusinessException, BusinessExceptionCode
from ....crud.video.video_audio import create_narration, create_narration_version
from ....services.agent.base_agent import MessageType
from ..schemas import VideoContextSchema
from ....services.agent.utils.database_utils import get_story_outline_from_db, get_characters_from_db, get_detailed_shots_from_db, save_narration_to_db, get_completed_narration_uuids_by_shot_number
from ..utils.message_utils import extract_ai_message_json
from ..utils.cancellation import raise_if_cancelled
logger = logging.getLogger(__name__)

_EMPTY_SHOT_GM = GenerationMode.EMPTY_SHOT.value


from app.orchestration.skills.prompt_context import (
    facts_human_message,
    skill_system_message,
)


def _shot_to_narration_plan_row(
    shot: DetailedShot,
    characters_by_id: Dict[str, CharacterProfile],
) -> Dict[str, Any]:
    from .narration_gender_utils import narration_gender_from_shot

    names: List[str] = []
    for character_id in shot.character_ids or []:
        character = characters_by_id.get(character_id)
        if character and character.type == VisualElementType.CHARACTER:
            names.append(character.name)
    return {
        "shot_number": shot.shot_number,
        "duration": shot.duration,
        "narration": shot.narration or "",
        "scene_description": shot.scene_description or "",
        "lighting": shot.lighting or "",
        "visual_effects": shot.visual_effects or "",
        "is_bridge": bool(shot.is_bridge),
        "character_ids": list(shot.character_ids or []),
        "character_names": names,
        "narration_gender": narration_gender_from_shot(shot),
    }


async def _run_narration_plan_deep_agent(
    *,
    shots: List[DetailedShot],
    characters_by_id: Dict[str, CharacterProfile],
    thread_id: str,
    run_id: str,
    detected_language: Optional[str],
) -> Tuple[Dict[int, Any], List[BaseMessage]]:
    """Plan voice_hint / speaker_gender via narration deep-agent (TTS stays Program)."""
    import uuid as _uuid
    from app.contracts.artifacts.narration import NarrationShotDraft
    from app.services.agent.video.narration_stage import (
        export_narration_plan_inputs,
        generate_narration_plan_via_deep_agent,
    )

    tid = thread_id or f"narr_{_uuid.uuid4().hex[:8]}"
    rid = run_id or f"run_{_uuid.uuid4().hex[:8]}"
    rows = [_shot_to_narration_plan_row(s, characters_by_id) for s in shots]
    paths = export_narration_plan_inputs(thread_id=tid, run_id=rid, shots=rows)
    art, msgs = await generate_narration_plan_via_deep_agent(
        thread_id=tid,
        run_id=rid,
        input_paths=paths,
        detected_language=detected_language,
    )
    by_shot: Dict[int, NarrationShotDraft] = {}
    for row in art.shots or []:
        if row.narration_text and str(row.narration_text).strip():
            by_shot[int(row.shot_number)] = row
    logger.info("🎙️ narration plan (deep-agent): %d shots with hints", len(by_shot))
    return by_shot, list(msgs or [])


def _build_narration_tts_messages(
    *,
    shot: DetailedShot,
    speaker_context: str,
    required_voice_id: str,
    speaker_gender_label: str,
    voice_hint: str,
    detected_language: Optional[str],
) -> List[BaseMessage]:
    """Per-shot TTS react-agent input: narration-tool-director skill + shot brief."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from ....services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages

    system_text = skill_system_message(
        "narration-tool-director",
        lead="Follow narration-tool-director.",
    )
    facts = {
        "shot_number": shot.shot_number,
        "is_bridge": bool(shot.is_bridge),
        "duration": shot.duration,
        "shot_type": getattr(shot, "shot_type", "") or "",
        "scene_description": shot.scene_description or "",
        "lighting": shot.lighting or "",
        "visual_effects": shot.visual_effects or "",
        "narration": shot.narration or "",
        "speaker_context": speaker_context,
        "speaker_gender": speaker_gender_label,
        "required_voice_id": required_voice_id,
        "voice_hint": voice_hint or "",
        "detected_language": detected_language or "",
    }
    messages: List[BaseMessage] = [
        SystemMessage(content=system_text),
        HumanMessage(content=facts_human_message(facts)),
    ]
    apply_language_suffix_to_system_message_in_messages(messages, detected_language)
    return messages


def _is_empty_shot(shot: DetailedShot) -> bool:
    gm = getattr(shot, "generation_mode", None)
    return bool(gm and str(gm).strip().lower() == _EMPTY_SHOT_GM)


def _is_product_launch_category(content_category: Optional[str]) -> bool:
    return content_category == ContentCategory.PRODUCT_LAUNCH.value


def _filter_shots_for_narration_tts(
    shots: List[DetailedShot],
    *,
    content_category: Optional[str] = None,
) -> Tuple[List[DetailedShot], List[DetailedShot]]:
    """筛出需要 TTS 的镜头；空镜（empty_shot）默认不生成旁白。

    Product Launch：empty_shot 但有 narration 文案时仍生成 TTS（片头/CTA 画外音兜底）。
    Short Drama：Seedance 口型对白 / silence 镜即使误填 narration 也不走 TTS。
    """
    from .voice_delivery_contract import should_generate_narration_tts

    with_narration: List[DetailedShot] = []
    skipped: List[DetailedShot] = []
    skipped_voice: List[DetailedShot] = []
    allow_empty_shot_tts = _is_product_launch_category(content_category)
    for shot in shots:
        if not (shot.narration and shot.narration.strip()):
            continue
        if not allow_empty_shot_tts and not should_generate_narration_tts(shot):
            skipped_voice.append(shot)
            continue
        if _is_empty_shot(shot):
            if allow_empty_shot_tts:
                with_narration.append(shot)
                continue
            skipped.append(shot)
            continue
        with_narration.append(shot)
    if skipped:
        logger.info(
            "⏭️ 跳过 %d 个空镜镜头的旁白 TTS: %s",
            len(skipped),
            [s.shot_number for s in skipped],
        )
    if skipped_voice:
        logger.info(
            "⏭️ 跳过非 TTS 旁白镜（Seedance对白/silence）: %s",
            [s.shot_number for s in skipped_voice],
        )
    if allow_empty_shot_tts:
        empty_with_tts = [s for s in with_narration if _is_empty_shot(s)]
        if empty_with_tts:
            logger.info(
                "🎙️ Product Launch：empty_shot 但有旁白，仍生成 TTS: %s",
                [s.shot_number for s in empty_with_tts],
            )
    return with_narration, skipped

def _resolve_default_voice_id_for_shot(
    shot: DetailedShot,
    detected_language: Optional[str],
) -> Optional[str]:
    """按 detail 节点写入的 narration_gender 解析默认 TTS 声线。"""
    from ....models.image_result import default_voice_for_language_and_gender
    from .narration_gender_utils import narration_gender_from_shot

    gender = narration_gender_from_shot(shot)
    if not gender:
        return None
    return default_voice_for_language_and_gender(detected_language, gender)


async def _emit_empty_narrations_generated(
    send_event_func: Any,
    state: Dict[str, Any],
    *,
    reason: str,
    narration_uuids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Workflow 含旁白步但无需/无可 TTS 时仍发完成事件，避免 FE 进度卡住。

    文案路径与有 TTS 时一致：generate_completion_message_stream（i18n task_context + LLM），
    禁止发空 message（FE 会退化成显示裸 event_type）。
    """
    from langchain_core.messages import AIMessage, HumanMessage

    from ....services.agent.utils.prompt_utils import generate_completion_message_stream

    uuids = list(narration_uuids or [])
    logger.info(
        "ℹ️ %s — 仍发送 narrations_generated（narration_count=%d）",
        reason,
        len(uuids),
    )
    # 无 per-shot TTS agent 历史时，用简短上下文供 completion LLM 写用户可见总结
    if uuids:
        history = [
            HumanMessage(
                content=(
                    f"Narration stage: no new TTS needed (reason={reason}). "
                    f"Reusing {len(uuids)} existing narration clip(s)."
                )
            ),
            AIMessage(
                content=(
                    f"Existing narration audio already covers this batch "
                    f"({len(uuids)} clip(s)); narration step complete."
                )
            ),
        ]
    else:
        history = [
            HumanMessage(
                content=(
                    f"Narration stage: no TTS voiceover for this batch "
                    f"(reason={reason}). Dialogue may be in-clip; no separate "
                    "narration audio files were produced."
                )
            ),
            AIMessage(
                content=(
                    "No separate narration TTS was required; "
                    "narration workflow step is complete."
                )
            ),
        ]

    user_message, _completion_message = await generate_completion_message_stream(
        event_type=MessageType.NARRATIONS_GENERATED,
        messages=history,
        send_event_func=send_event_func,
        conversation_id=state.get("conversation_id"),
        lang=state.get("detected_language"),
    )

    await send_event_func(
        conversation_id=state["conversation_id"],
        event_type=MessageType.NARRATIONS_GENERATED,
        message=user_message,
        extra_data={
            "narration_count": len(uuids),
            "narration_uuids": uuids,
            "success_count": len(uuids),
            "failed_count": 0,
            "empty": len(uuids) == 0,
            "reason": reason,
            "run_id": state.get("run_id"),
            "thread_id": state.get("thread_id"),
        },
    )
    out: Dict[str, Any] = {"narrations_with_versions": []}
    if uuids:
        out["narration_uuids"] = uuids
    return out


def _build_narration_speaker_context(
    shot: DetailedShot,
    characters_by_id: Dict[str, CharacterProfile],
    default_voice_id: Optional[str],
    detected_language: Optional[str],
) -> Tuple[str, str, str, Optional[str]]:
    """生成 prompt 中的讲解者/声线说明。性别以 detail 节点 narration_gender 为准。"""
    from ....models.image_result import default_voice_for_language_and_gender
    from .narration_gender_utils import narration_gender_from_shot, narration_gender_label

    speakers: List[str] = []
    for character_id in shot.character_ids or []:
        character = characters_by_id.get(character_id)
        if not character or character.type != VisualElementType.CHARACTER:
            continue
        speakers.append(character.name)
    speaker_gender = narration_gender_from_shot(shot)
    gender_label = narration_gender_label(speaker_gender)
    if not speakers:
        speaker_context = "未指定讲解角色"
    else:
        speaker_context = f"{'、'.join(speakers)}（讲解者性别：{gender_label}）"
    if default_voice_id:
        required_voice_id = default_voice_id
    elif speaker_gender:
        required_voice_id = default_voice_for_language_and_gender(detected_language, speaker_gender)
    else:
        required_voice_id = "按语言默认（detail 未标注 narration_gender 时中文默认女声 News_Anchor）"
        logger.warning(
            "镜头%d 有旁白但缺少 narration_gender（detail 节点应写入 additional_data），将使用语言默认声线",
            shot.shot_number,
        )
    return speaker_context, required_voice_id, gender_label, speaker_gender


async def _generate_single_narration(
    shot: DetailedShot, 
    story_outline_uuid: str, 
    state: VideoAgentState, 
    cached_prompt_template: Any = None,
    characters_by_id: Optional[Dict[str, CharacterProfile]] = None,
    narration_plan_by_shot: Optional[Dict[int, Any]] = None,
) -> tuple[NarrationVersion, List[BaseMessage]]:
    """生成单个镜头的旁白 - ``create_react_agent`` + 工具经 llm_resilience。"""
    try:
        logger.info(f"🎙️ 为镜头{shot.shot_number}生成旁白")
        
        detected_language = state.get("detected_language") if isinstance(state, dict) else None
        characters_by_id = characters_by_id or {}
        default_voice_id = _resolve_default_voice_id_for_shot(shot, detected_language)
        speaker_context, required_voice_id, speaker_gender_label, speaker_gender = (
            _build_narration_speaker_context(
                shot, characters_by_id, default_voice_id, detected_language,
            )
        )
        plan_row = (narration_plan_by_shot or {}).get(shot.shot_number)
        voice_hint = getattr(plan_row, "voice_hint", "") if plan_row else ""
        if plan_row and getattr(plan_row, "speaker_gender", None) in ("f", "m"):
            from ....models.image_result import default_voice_for_language_and_gender
            required_voice_id = default_voice_for_language_and_gender(
                detected_language, plan_row.speaker_gender,
            )
            speaker_gender = plan_row.speaker_gender

        tool_prompt_messages = _build_narration_tts_messages(
            shot=shot,
            speaker_context=speaker_context,
            required_voice_id=required_voice_id,
            speaker_gender_label=speaker_gender_label,
            voice_hint=voice_hint,
            detected_language=detected_language,
        )
        
        from ....models.image_result import SpeechGenerationResult
        from ....tools.context_schemas import SpeechGenerationContext
        from ....services.tool_service import ToolService
        from prompts.prompt_config import PROMPTS_CONFIG, PromptName
        from ....services.agent.utils.llm_resilience import (
            StructuredResilienceKind,
            ainvoke_structured_resilient,
        )
        from ..utils.message_utils import patch_tool_metrics_from_last_tool_message

        tools_info = ToolService.get_speech_generation_tools()
        speech_tools = tools_info.tool_objects

        context = SpeechGenerationContext(
            target_duration=float(shot.duration) if shot.duration else None,
            detected_language=detected_language,
            default_voice_id=default_voice_id,
            speaker_gender=speaker_gender,
            shot_number=shot.shot_number,
        )

        result = await ainvoke_structured_resilient(
            prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_NARRATION_GENERATION],
            kind=StructuredResilienceKind.CREATE_AGENT,
            agent_inputs={"messages": tool_prompt_messages},
            agent_tools=speech_tools,
            context_schema=SpeechGenerationContext,
            agent_invoke_context=context,
            wrap_agent_parse_fallback=True,
            log_context={"shot_number": shot.shot_number, "phase": "narration_generation"},
        )
        
        # ✅ 获取完整的 agent messages（input + output）
        agent_full_messages = result.get("messages", [])
        input_message_count = len(tool_prompt_messages)
        output_messages = agent_full_messages[input_message_count:] if len(agent_full_messages) > input_message_count else []
        
        # 返回本次调用的 input messages + output messages
        agent_messages = tool_prompt_messages + output_messages
        
        # 获取结构化响应
        structured_response = cast(SpeechGenerationResult, result.get("structured_response"))
        structured_response = patch_tool_metrics_from_last_tool_message(
            result.get("messages", []), structured_response
        )
        if not structured_response:
            raise Exception("未获取到结构化响应")
        
        # 保存AIMessage（包含工具调用参数）
        ai_messages_json = extract_ai_message_json(result)
        
        # 创建旁白版本对象
        if structured_response.success:
            narration_version = NarrationVersion(
                version_number=1,
                shot_number=shot.shot_number,
                narration_text=shot.narration,
                enhanced_prompt=structured_response.generated_text or shot.narration,
                audio_url=structured_response.audio_url,
                provider=structured_response.provider,
                duration=structured_response.duration,
                params={
                    "voice_id": structured_response.voice_id,
                    "emotion": structured_response.emotion,
                    "speed": 1.0,
                } if hasattr(structured_response, 'voice_id') else None,
                is_bridge=shot.is_bridge,
                success=True,
                created_at=datetime.now().isoformat(),
                ai_messages_json=ai_messages_json
            )
        else:
            narration_version = NarrationVersion(
                version_number=1,
                shot_number=shot.shot_number,
                narration_text=shot.narration,
                enhanced_prompt=shot.narration,
                audio_url="",
                provider="wavespeed",
                duration=None,
                params={"voice_id": "Wise_Woman", "emotion": "neutral"},
                is_bridge=shot.is_bridge,
                success=False,
                error_msg=structured_response.message or "语音合成失败",
                created_at=datetime.now().isoformat(),
                ai_messages_json=ai_messages_json
            )
        
        return narration_version, agent_messages
        
    except Exception as e:
        logger.error(f"❌ 镜头{shot.shot_number} 旁白生成异常: {e}")
        # 返回失败的旁白记录
        failed_narration = NarrationVersion(
            version_number=1,
            shot_number=shot.shot_number,
            narration_text=shot.narration or "",
            enhanced_prompt=shot.narration or "",
            audio_url="",
            provider="wavespeed",
            duration=None,
            params={"voice_id": "Wise_Woman", "emotion": "neutral"},
            is_bridge=shot.is_bridge,
            success=False,
            error_msg=str(e),
            created_at=datetime.now().isoformat()
        )
        return failed_narration, []




async def narration_generation_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any
) -> Union[VideoAgentState, Dict[str, Any]]:
    """旁白生成节点 - 为有旁白的镜头生成语音（节点内按需 load VIDEO_NARRATION_GENERATION）"""
    try:
        logger.info("🎙️ 开始旁白生成节点")
        
        # ✅ 检查是否需要生成旁白（config 关闭 = workflow 不含旁白步 → 不发完成事件）
        generation_config = state.get("generation_config")
        if generation_config and not generation_config.generate_narration:
            logger.info(f"⏭️ 跳过旁白生成: {generation_config.reason}")
            return {"narrations_with_versions": []}
        
        # 获取必要的数据（prompt + LLM 在 _generate_single_narration 内按需 load）
        shot_uuids = state.get("shot_uuids", [])
        if not shot_uuids:
            logger.warning("⚠️ 没有找到详细镜头数据，跳过旁白生成")
            return await _emit_empty_narrations_generated(
                send_event_func,
                state,
                reason="no_shot_uuids",
            )
        
        # 加载数据（使用asyncpg CRUD）
        shots = await get_detailed_shots_from_db(shot_uuids)
        
        if not shots:
            logger.warning("⚠️ 无法获取详细镜头信息，跳过旁白生成")
            return await _emit_empty_narrations_generated(
                send_event_func,
                state,
                reason="no_shots_loaded",
            )

        character_uuids = state.get("character_uuids") or []
        characters = await get_characters_from_db(character_uuids) if character_uuids else []
        characters_by_id = {character.id: character for character in characters}

        user_input_data = state.get("user_input_data")
        user_option = user_input_data.user_option if user_input_data else None
        content_category = (
            user_option.content_category.value
            if user_option and user_option.content_category
            else None
        )
        
        # 筛选有旁白的镜头（空镜 empty_shot 不生成 TTS；Product Launch 例外见 _filter_shots_for_narration_tts）
        shots_with_narration, skipped_empty_shots = _filter_shots_for_narration_tts(
            shots, content_category=content_category
        )
        
        if not shots_with_narration:
            reason = (
                "empty_or_no_tts_shots"
                if skipped_empty_shots
                else "no_narration_text"
            )
            return await _emit_empty_narrations_generated(
                send_event_func,
                state,
                reason=reason,
            )
        
        logger.info(f"🎙️ 发现 {len(shots_with_narration)} 个镜头需要生成旁白")
        
        # 获取故事大纲UUID用于关联
        story_outline_uuid = state.get("story_outline_uuid")
        if not story_outline_uuid:
            raise BusinessException(
                BusinessExceptionCode.INVALID_PARAMETER,
                "缺少故事大纲UUID"
            )
        
        # ♻️ 断点续跑幂等：跳过本 outline 下「旁白已成功」的镜头，避免取消/恢复后重复生成
        already_done_narration_uuids: List[str] = []
        try:
            _done_by_shot = await get_completed_narration_uuids_by_shot_number(
                story_outline_uuid, str(state.get("thread_id") or "")
            )
            if _done_by_shot:
                pending_shots_with_narration = [
                    s for s in shots_with_narration if s.shot_number not in _done_by_shot
                ]
                already_done_narration_uuids = [
                    _done_by_shot[s.shot_number]
                    for s in shots_with_narration
                    if s.shot_number in _done_by_shot
                ]
                skipped_count = len(shots_with_narration) - len(pending_shots_with_narration)
                if skipped_count:
                    logger.info(f"♻️ 断点续跑：跳过 {skipped_count} 个已成功旁白的镜头")
                shots_with_narration = pending_shots_with_narration
        except Exception as _skip_err:
            logger.warning(f"⚠️ 旁白断点续跑检查失败，按全量生成: {_skip_err}")

        if not shots_with_narration:
            return await _emit_empty_narrations_generated(
                send_event_func,
                state,
                reason="all_narrations_already_done",
                narration_uuids=already_done_narration_uuids,
            )

        all_shots_with_narration, _ = _filter_shots_for_narration_tts(
            shots, content_category=content_category
        )
        total_narration_shots = len(all_shots_with_narration)
        narration_completed_count = total_narration_shots - len(shots_with_narration)

        await send_event_func(
            event_type=MessageType.NARRATION_GENERATION_START,
            conversation_id=state["conversation_id"],
            extra_data={
                "total": total_narration_shots,
                "thread_id": state.get("thread_id"),
                "run_id": state.get("run_id"),
            },
            hidden=True,
            save_to_db=False,
        )
        await send_event_func(
            event_type=MessageType.NARRATION_GENERATION_PROGRESS,
            conversation_id=state["conversation_id"],
            extra_data={
                "completed": narration_completed_count,
                "total": total_narration_shots,
                "thread_id": state.get("thread_id"),
                "run_id": state.get("run_id"),
            },
            hidden=True,
            save_to_db=False,
        )
        
        # 旁白 plan：deep-agent 一次规划 voice_hint；TTS 走 narration-tool-director + Program 工具
        narration_plan_by_shot: Dict[int, Any] = {}
        plan_messages: List[BaseMessage] = []
        try:
            narration_plan_by_shot, plan_messages = await _run_narration_plan_deep_agent(
                shots=shots_with_narration,
                characters_by_id=characters_by_id,
                thread_id=str(state.get("thread_id") or ""),
                run_id=str(state.get("run_id") or ""),
                detected_language=state.get("detected_language"),
            )
        except Exception as plan_err:
            logger.warning("⚠️ narration plan deep-agent failed, TTS without voice_hint: %s", plan_err)

        logger.info("✅ 旁白 TTS 使用 narration-tool-director skill（不再加载 plan mustache）")
        
        # 批量生成旁白 - 5个一批（已完成镜头的 narration uuid 一并带上，保证下游拿到完整列表）
        all_narration_versions = []
        all_narration_uuids = list(already_done_narration_uuids)
        all_messages = list(plan_messages)  # 收集 plan + react agent messages
        
        # 分批处理，每批5个
        batch_size = 5
        total_batches = (len(shots_with_narration) + batch_size - 1) // batch_size
        
        for batch_idx in range(total_batches):
            # 协作式取消：开新批次前检查，取消后不再启动新一批生成
            await raise_if_cancelled()
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, len(shots_with_narration))
            current_batch = shots_with_narration[start_idx:end_idx]
            
            logger.info(f"🎙️ 处理第 {batch_idx + 1}/{total_batches} 批旁白生成 ({len(current_batch)} 个镜头)")
            
            # 并行生成当前批次的旁白
            tasks = []
            for shot in current_batch:
                task = _generate_single_narration(
                    shot, story_outline_uuid, state,
                    characters_by_id=characters_by_id,
                    narration_plan_by_shot=narration_plan_by_shot,
                )
                tasks.append(task)
            
            # 等待当前批次完成
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 协作式取消：本批若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
            await raise_if_cancelled()
            
            # 处理结果并保存到数据库
            current_narration_uuids = []
            for i, result in enumerate(results):
                shot = current_batch[i]
                
                if isinstance(result, BaseException):
                    logger.error(f"❌ 镜头{shot.shot_number} 旁白生成异常: {result}")
                    narration_version = NarrationVersion(
                        version_number=1,
                        shot_number=shot.shot_number,
                        narration_text=shot.narration or "",
                        enhanced_prompt=shot.narration or "",
                        audio_url="",
                        provider="wavespeed",
                        duration=None,
                        params={"voice_id": "Wise_Woman", "emotion": "neutral"},
                        is_bridge=shot.is_bridge,
                        success=False,
                        error_msg=str(result),
                        created_at=datetime.now().isoformat()
                    )
                    agent_messages = []
                else:
                    narration_version, agent_messages = result
                    # 收集messages
                    all_messages.extend(agent_messages)
                
                result = narration_version
                
                # 保存旁白到数据库
                narration_uuid = await save_narration_to_db(
                    narration_version=result,
                    shot=shot,
                    story_outline_uuid=story_outline_uuid,
                    conversation_id=str(state["conversation_id"]),
                    thread_id=str(state["thread_id"]),
                    run_id=state["run_id"],
                    user_id=state["user_id"]
                )

                if result.success and result.duration and getattr(shot, "uuid", None):
                    from ....crud.video.video_story import update_detailed_shot_duration
                    await update_detailed_shot_duration(shot.uuid, float(result.duration))
                
                current_narration_uuids.append(narration_uuid)
                all_narration_versions.append(result)
                
                if result.success:
                    logger.info(f"✅ 镜头{shot.shot_number} 旁白生成成功: {narration_uuid}")
                else:
                    logger.warning(f"⚠️ 镜头{shot.shot_number} 旁白生成失败，已记录: {narration_uuid}")
            
            # 批次处理完成后，更新统计信息
            all_narration_uuids.extend(current_narration_uuids)
            narration_completed_count += len(current_batch)
            await send_event_func(
                event_type=MessageType.NARRATION_GENERATION_PROGRESS,
                conversation_id=state["conversation_id"],
                extra_data={
                    "completed": narration_completed_count,
                    "total": total_narration_shots,
                    "thread_id": state.get("thread_id"),
                    "run_id": state.get("run_id"),
                },
                hidden=True,
                save_to_db=False,
            )
            logger.info(f"✅ 第{batch_idx + 1}批保存了 {len(current_narration_uuids)} 个旁白记录")
        
        # 构建带版本的旁白数据
        narrations_with_versions = []
        for narration_version in all_narration_versions:
            narration_with_versions = NarrationWithVersions(
                shot_number=narration_version.shot_number,
                versions=[narration_version],
                current_index=0,
                is_bridge=narration_version.is_bridge,
                has_narration=True
            )
            narrations_with_versions.append(narration_with_versions)
        
        logger.info(f"🎙️ 旁白生成完成: {len(narrations_with_versions)} 个镜头")
        
        # 使用LLM根据收集的messages生成完成消息
        from ....services.agent.utils.prompt_utils import generate_completion_message_stream
        
        detected_language = state.get("detected_language")
        user_message, completion_message = await generate_completion_message_stream(
            event_type=MessageType.NARRATIONS_GENERATED,
            messages=all_messages,  # 传入收集的react agent messages
            send_event_func=send_event_func,
            conversation_id=state.get("conversation_id"),
            lang=detected_language
        )
        
        # 收集completion message
        if completion_message:
            all_messages.append(completion_message)
        
        logger.info(f"📝 生成的用户消息: {user_message}")
        
        # 发送旁白生成完成事件给前端
        await send_event_func(
            conversation_id=state["conversation_id"],
            event_type=MessageType.NARRATIONS_GENERATED,
            message=user_message,  # 使用LLM生成的消息
            extra_data={
                "narration_count": len(narrations_with_versions),
                "narration_uuids": all_narration_uuids,
                "success_count": sum(1 for nv in all_narration_versions if nv.success),
                "failed_count": sum(1 for nv in all_narration_versions if not nv.success),
                "run_id": state.get("run_id"),
                "thread_id": state.get("thread_id")
            }
        )
        
        return {
            "narrations_with_versions": narrations_with_versions,
            "messages": all_messages,
            "narration_uuids": all_narration_uuids
        }
        
    except BusinessException as e:
        logger.error(f"旁白生成失败: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"旁白生成异常: {str(e)}")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"旁白生成异常: {str(e)}"
        )
