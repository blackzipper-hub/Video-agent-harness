"""
分镜首帧合规修订服务
在 storyboard_detail_generation 之后对已落库的 detailed_shots 做首帧合规检查与修订，写回 DB。
"""
import logging
import asyncio
from typing import List, Optional, Dict, Any, Union, Tuple
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from ....models.tool_enums import GenerationMode
from ....models.video_state import VideoAgentState
from ....models.user_options import should_enable_lipsync_for_run
from ....exceptions import BusinessException, BusinessExceptionCode
from ..schemas import VideoContextSchema
from ....crud.video.video_story import (
    get_detailed_shots_by_uuids,
    update_detailed_shot_first_frame_revision,
)
from ..utils.cancellation import raise_if_cancelled
from ..utils.prompt_utils import (
    get_concurrency_limit,
    add_random_delay,
    CONCURRENCY_LIMITS,
)

logger = logging.getLogger(__name__)

# 为 true 时跳过首帧修订节点（直通，不修改 shots）
SKIP_FIRST_FRAME_REVISION = False


class FirstFrameRevisionItem(BaseModel):
    """单个镜头的首帧修订结果"""
    shot_number: int = Field(description="镜头编号，与输入一致")
    has_violation: bool = Field(description="是否违反首帧约束")
    violation_type: Optional[str] = Field(
        default=None,
        description="违规类型：reveal_full_body_from_partial / subject_turn / person_enter_from_empty / reveal_new_object，仅 has_violation 时填"
    )
    revised_scene_description: Optional[str] = Field(default=None, description="修订后的画面描述")
    revised_camera_movement: Optional[str] = Field(default=None, description="修订后的镜头运动")
    revised_shot_type: Optional[str] = Field(default=None, description="修订后的景别")
    revised_subject_angle: Optional[str] = Field(default=None, description="修订后的主体角度")
    revised_subject_pose: Optional[str] = Field(default=None, description="修订后的主体姿势")
    revised_generation_mode: Optional[str] = Field(
        default=None,
        description="修订后的生成模式：normal / lipsync / empty_shot。根据修订后描述判断是否露脸、对嘴；局部无脸/背影/空镜填 normal"
    )


class FirstFrameRevisionOutput(BaseModel):
    """首帧合规修订批量输出"""
    shots: List[FirstFrameRevisionItem] = Field(description="每个镜头的修订结果，顺序与输入一致")


def _shot_row_for_prompt(shot: Any) -> Dict[str, Any]:
    """从 DB 镜头对象提取供 prompt 使用的字段"""
    return {
        "shot_number": getattr(shot, "shot_number", 0),
        "shot_type": getattr(shot, "shot_type", "") or "",
        "scene_description": getattr(shot, "scene_description", "") or "",
        "camera_movement": getattr(shot, "camera_movement", "") or "",
        "subject_angle": getattr(shot, "subject_angle", "") or "",
        "subject_pose": getattr(shot, "subject_pose", "") or "",
    }


def _truncate_for_log(text: str, max_len: int = 200) -> str:
    if not text:
        return ""
    text = (text or "").strip()
    return (text[:max_len] + "…") if len(text) > max_len else text


async def _revision_batch(
    shots_batch: List[Any],
    *,
    batch_index: int = 0,
    run_id: str = "",
    thread_id: str = "",
    allow_lipsync: bool = True,
) -> Tuple[int, List[BaseMessage]]:
    """处理一批镜头的首帧修订，返回修订数量与 messages。修订结果与输入顺序一致，按索引匹配 shot_uuid。"""
    if not shots_batch:
        return 0, []
    shot_numbers = [getattr(s, "shot_number", i) for i, s in enumerate(shots_batch)]
    logger.info(
        "[first_frame_revision] thread_id=%s run_id=%s batch=%s shots=%s",
        thread_id, run_id, batch_index, shot_numbers
    )
    for idx, s in enumerate(shots_batch):
        desc = getattr(s, "scene_description", "") or ""
        logger.info(
            "[first_frame_revision] shot_number=%s scene_description_preview=%s",
            getattr(s, "shot_number", idx),
            _truncate_for_log(desc, 280)
        )
    shots_for_prompt = [_shot_row_for_prompt(s) for s in shots_batch]
    if not (thread_id and run_id):
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "缺少 thread_id/run_id，无法走 first-frame deep-agent",
        )
    from app.services.agent.video.first_frame_stage import (
        export_first_frame_inputs,
        generate_first_frame_via_deep_agent,
    )
    _paths = export_first_frame_inputs(
        thread_id=thread_id,
        run_id=run_id,
        batch_id=f"b{batch_index}",
        shots=shots_for_prompt,
        allow_lipsync=allow_lipsync,
    )
    art, da_msgs = await generate_first_frame_via_deep_agent(
        thread_id=thread_id,
        run_id=run_id,
        input_paths=_paths,
    )
    # Artifact draft ≠ FirstFrameRevisionItem; coerce via dump (same fields).
    out = FirstFrameRevisionOutput(
        shots=[
            FirstFrameRevisionItem.model_validate(
                s.model_dump() if hasattr(s, "model_dump") else s
            )
            for s in (art.shots or [])
        ]
    )
    messages = list(da_msgs or [])
    result = {"structured_response": out, "messages": messages}
    revised_count = 0
    if not result or "structured_response" not in result:
        logger.warning(
            "[first_frame_revision] thread_id=%s run_id=%s batch=%s Agent 未返回有效结果，跳过本批",
            thread_id, run_id, batch_index
        )
        return 0, messages
    out = result.get("structured_response")
    if not isinstance(out, FirstFrameRevisionOutput) or not out.shots:
        # deep-agent path may return pydantic subclass via FirstFrameArtifact shots
        if hasattr(out, "shots") and out.shots:
            pass
        else:
            return 0, messages
    if not isinstance(out, FirstFrameRevisionOutput):
        out = FirstFrameRevisionOutput(shots=[FirstFrameRevisionItem(**s.model_dump()) for s in out.shots])
    for i, item in enumerate(out.shots):
        logger.info(
            "[first_frame_revision] thread_id=%s run_id=%s shot_number=%s has_violation=%s violation_type=%s revised_generation_mode=%s",
            thread_id, run_id, item.shot_number, item.has_violation, item.violation_type, getattr(item, "revised_generation_mode", None)
        )
        if i >= len(shots_batch):
            logger.warning("[first_frame_revision] 输出索引 %s 超出批次长度 %s，跳过", i, len(shots_batch))
            continue
        shot = shots_batch[i]
        shot_uuid = getattr(shot, "uuid", None)
        if not shot_uuid:
            logger.warning("[first_frame_revision] 镜头索引 %s 无 uuid，跳过", i)
            continue
        has_update = item.has_violation or getattr(item, "revised_generation_mode", None) is not None
        if not has_update:
            continue
        revised_gm = getattr(item, "revised_generation_mode", None)
        if (
            not allow_lipsync
            and revised_gm
            and str(revised_gm).strip().lower() == GenerationMode.LIPSYNC.value
        ):
            revised_gm = GenerationMode.NORMAL.value
        ok = await update_detailed_shot_first_frame_revision(
            shot_uuid,
            scene_description=item.revised_scene_description if item.has_violation else None,
            camera_movement=item.revised_camera_movement if item.has_violation else None,
            shot_type=item.revised_shot_type if item.has_violation else None,
            subject_angle=item.revised_subject_angle if item.has_violation else None,
            subject_pose=item.revised_subject_pose if item.has_violation else None,
            generation_mode=revised_gm,
        )
        if ok:
            revised_count += 1
            logger.info(
                "[first_frame_revision] 已写回 DB shot_number=%s uuid=%s violation_type=%s revised_generation_mode=%s revised_desc_preview=%s",
                item.shot_number, shot_uuid, item.violation_type, getattr(item, "revised_generation_mode", None),
                _truncate_for_log(item.revised_scene_description or "", 150)
            )
        else:
            logger.warning("[first_frame_revision] 写回 DB 失败 shot_number=%s uuid=%s", item.shot_number, shot_uuid)
    agent_messages = result.get("messages", [])
    output_messages = agent_messages[len(messages):] if len(agent_messages) > len(messages) else []
    return revised_count, list(messages) + output_messages


async def storyboard_first_frame_revision_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any,
) -> Union[VideoAgentState, Dict[str, Any]]:
    """分镜首帧合规修订节点：对 storyboard_detail 产出的 shots 做首帧检查与修订并写回 DB。"""
    run_id = state.get("run_id", "") or ""
    thread_id = state.get("thread_id", "") or ""
    logger.info(
        "[first_frame_revision] 节点入口 thread_id=%s run_id=%s conversation_id=%s",
        thread_id, run_id, state.get("conversation_id")
    )
    try:
        from .music_generation_service import should_skip_keyframe_pipeline_from_state

        if should_skip_keyframe_pipeline_from_state(state):
            logger.info(
                "[first_frame_revision] 跳过（shot_workflow_mode=reference_t2v） thread_id=%s",
                thread_id,
            )
            return {"messages": []}
        if SKIP_FIRST_FRAME_REVISION:
            logger.info("[first_frame_revision] 跳过（SKIP_FIRST_FRAME_REVISION=True） thread_id=%s", thread_id)
            return {"messages": []}
        shot_uuids = state.get("shot_uuids", [])
        if not shot_uuids:
            logger.warning("[first_frame_revision] 无 shot_uuids，跳过 thread_id=%s run_id=%s", thread_id, run_id)
            return {"messages": []}
        shots = await get_detailed_shots_by_uuids(shot_uuids)
        if not shots:
            logger.warning("[first_frame_revision] 未查到任何镜头，跳过 thread_id=%s run_id=%s", thread_id, run_id)
            return {"messages": []}
        logger.info(
            "[first_frame_revision] 待检查镜头数=%s shot_uuids_count=%s thread_id=%s run_id=%s",
            len(shots), len(shot_uuids), thread_id, run_id
        )
        detected_language = state.get("detected_language") or "zh"
        batch_size = CONCURRENCY_LIMITS.get("first_frame_revision_batch_size", 5)
        max_concurrent = get_concurrency_limit("storyboard_first_frame_revision")
        semaphore = asyncio.Semaphore(max_concurrent)
        if not (thread_id and run_id):
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "缺少 thread_id/run_id，无法走 first-frame deep-agent",
            )
        user_input_data = state.get("user_input_data")
        user_option = user_input_data.user_option if user_input_data else None
        from ....services.agent.utils.database_utils import get_audio_transcription_from_db

        audio_transcription_uuids = state.get("audio_transcription_uuids", [])
        _at = None
        if audio_transcription_uuids:
            _at = await get_audio_transcription_from_db(audio_transcription_uuids)
        allow_lipsync = should_enable_lipsync_for_run(
            user_option,
            music_intent=state.get("music_intent"),
            music_workflow_mode=state.get("music_workflow_mode"),
            audio_transcription=_at,
        )
        all_messages = []
        total_revised = 0
        batches = []
        for i in range(0, len(shots), batch_size):
            batches.append(shots[i : i + batch_size])
        async def process_one_batch(batch: List[Any], b_idx: int) -> Tuple[int, List[BaseMessage]]:
            await add_random_delay()
            async with semaphore:
                return await _revision_batch(
                    batch,
                    batch_index=b_idx,
                    run_id=run_id,
                    thread_id=thread_id,
                    allow_lipsync=allow_lipsync,
                )
        results = await asyncio.gather(*[process_one_batch(b, b_idx) for b_idx, b in enumerate(batches)])
        
        # 协作式取消：若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
        await raise_if_cancelled()
        
        for rev_count, msgs in results:
            total_revised += rev_count
            all_messages.extend(msgs)
        logger.info(
            "[first_frame_revision] 完成 thread_id=%s run_id=%s 总镜头=%s 修订数=%s",
            thread_id, run_id, len(shots), total_revised
        )
        return {"messages": all_messages}
    except BusinessException as e:
        logger.error("首帧修订节点 业务异常: %s", e.detail)
        raise
    except Exception as e:
        logger.error("首帧修订节点 异常: %s", e, exc_info=True)
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "分镜首帧合规修订失败: " + str(e),
        )
