"""
故事梗概生成功能模块
负责故事梗概和分镜大纲生成节点的实现和相关功能
"""
import logging
import uuid as uuid_lib
from typing import Dict, Any, Union, cast, List, Tuple, Optional
from langgraph.runtime import Runtime
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from ....models.video_state import (
    VideoAgentState,
    UserInput,
    VideoAnalysisResult,
    AudioTranscription,
    StoryOutline,
    StoryOutlineForLLMMode,
    StoryChapter,
    StoryStructure,
    ContentCategory,
)
from ....models.user_options import UserOption
from ....exceptions import BusinessException, BusinessExceptionCode
from ....crud.video.video_story import (
    create_video_story_outline,
    create_chapter,
    get_video_story_outline_by_thread_id,
    get_chapters_by_story_outline_id,
)
from pydantic import BaseModel, Field
from typing import Tuple
from ....services.agent.base_agent import MessageType
from ..schemas import VideoContextSchema
from ....services.agent.utils.database_utils import get_video_analysis_from_db, get_audio_transcription_with_sections, build_audio_context_for_prompts
from ....utils.chapter_order import reindex_chapter_orders_inplace
# ==================== 从 tools/new/video_outline_tools.py 移入 ====================
class DurationValidationResult(BaseModel):
    """时长验证结果"""
    is_valid: bool = Field(description="时长是否有效")
    total_duration: float = Field(description="章节总时长")
    target_duration: float = Field(description="目标时长")
    error: float = Field(description="时长误差")
    tolerance: float = Field(description="允许误差")


class OutlineGenerationResult(BaseModel):
    """故事梗概生成结果"""
    success: bool = Field(description="是否成功生成")
    story_outline: Optional[StoryOutline] = Field(description="生成的故事梗概", default=None)
    duration_validation: Optional[DurationValidationResult] = Field(description="时长验证结果", default=None)

# 章节时长配置（秒）
# 注意：min 不能再硬编码 30 —— 对 30s 短片若 LLM 产出多章，旧兜底会写成 30/30/-30。
# 程序兜底以「规划用最小成片秒数」(min_video_duration) 为单元均分；prompt 侧同步用同一最小值。
MIN_CHAPTER_DURATION_SECONDS = 30  # 仅作「长视频」叙事建议参考，不再用于强制 floor
MAX_CHAPTER_DURATION_SECONDS = 50  # 章节最大时长（建议）


logger = logging.getLogger(__name__)


def _analysis_from_user_request(
    user_input_data: Optional[UserInput],
) -> VideoAnalysisResult:
    """Build the minimum analysis contract for a text-first outline request.

    Studio workflows may start directly at ``outline.generate`` and therefore do
    not necessarily have a persisted analysis artifact.  The legacy outline
    generator still expects ``VideoAnalysisResult``; adapt the user request here
    instead of forcing the dynamic workflow through the legacy analysis stage.
    """
    prompt = (user_input_data.user_input if user_input_data else "").strip()
    user_option = user_input_data.user_option if user_input_data else None
    duration = float(getattr(user_option, "duration", 30) or 30)
    content_category = getattr(user_option, "content_category", None)
    if content_category is None:
        content_category = ContentCategory.SHORT_DRAMA

    return VideoAnalysisResult(
        video_type="short_drama" if content_category == ContentCategory.SHORT_DRAMA else "video",
        duration=duration,
        main_character="",
        purpose=prompt or "Generate a story outline from the user's request.",
        key_elements=[prompt] if prompt else [],
        style_preferences=[],
        target_audience="",
        next_action="outline",
        content_category=content_category,
        extra={"source": "text_first_outline", "user_request": prompt},
    )


def _validate_outline_duration(story_outline: StoryOutline, analysis_data: VideoAnalysisResult, audio_transcription: Optional[AudioTranscription] = None) -> DurationValidationResult:
    """
    验证故事梗概的时长是否符合要求
    
    Returns:
        DurationValidationResult: 时长验证结果
    """
    try:
        # 计算章节总时长
        chapters_duration = sum(chapter.duration for chapter in story_outline.structure.chapters)
        
        # 确定目标时长
        if audio_transcription:
            # 音频驱动模式：目标时长是音频总时长
            target_duration = audio_transcription.duration
        else:
            # 视频驱动模式：目标时长是分析数据中的时长
            target_duration = analysis_data.duration
        
        # 计算误差
        duration_error = abs(chapters_duration - target_duration)
        tolerance = 1.0  # 允许1秒误差
        
        is_valid = duration_error <= tolerance
        
        return DurationValidationResult(
            is_valid=is_valid,
            total_duration=chapters_duration,
            target_duration=target_duration,
            error=duration_error,
            tolerance=tolerance
        )
        
    except Exception as e:
        logger.error(f"验证时长失败: {e}")
        return DurationValidationResult(
            is_valid=False,
            total_duration=0.0,
            target_duration=0.0,
            error=float('inf'),
            tolerance=0.0
        )


async def outline_generation_node(
    state: VideoAgentState, 
    runtime: Runtime[VideoContextSchema],
    send_event_func
) -> Union[VideoAgentState, Dict[str, Any]]:
    """故事梗概和分镜大纲生成节点（使用ReAct Agent迭代优化）"""        
    # 幂等：一个 thread 只对应一个视频/一份大纲。若本 thread 已存在「带章节」的大纲（多为上一次
    # 被取消/中断的 run 已生成），直接复用，跳过重复 LLM 生成与重复入库，避免产生第二份大纲
    # （进而导致下游分镜/关键帧/视频按两份大纲翻倍）。仅在大纲含章节时复用，规避中途取消产生的半成品。
    thread_id_for_idem = state.get("thread_id", "")
    if thread_id_for_idem:
        existing_outline = await get_video_story_outline_by_thread_id(thread_id_for_idem)
        if existing_outline and getattr(existing_outline, "uuid", None):
            existing_chapters = await get_chapters_by_story_outline_id(existing_outline.uuid)
            if existing_chapters:
                logger.info(
                    "♻️ outline 幂等：thread=%s 已存在大纲 %s（%d 章），复用并跳过重复生成",
                    thread_id_for_idem, existing_outline.uuid, len(existing_chapters),
                )
                await send_event_func(
                    conversation_id=state.get("conversation_id"),
                    event_type=MessageType.STORY_OUTLINE_GENERATED,
                    message=getattr(existing_outline, "title", "") or "",
                    extra_data={
                        "story_outline_uuid": existing_outline.uuid,
                        "story_title": getattr(existing_outline, "title", ""),
                        "run_id": state.get("run_id"),
                        "thread_id": thread_id_for_idem,
                        "reused": True,
                    },
                )
                return {
                    "story_outline_uuid": existing_outline.uuid,
                    "messages": [],
                }

    # 使用抽取的方法从数据库获取数据（文本驱动时可能没有 analysis_uuid）
    analysis_uuid = state.get("analysis_uuid")
    audio_transcription_uuids = state.get("audio_transcription_uuids", [])
    user_input_data = state.get("user_input_data")

    if analysis_uuid:
        analysis_data = await get_video_analysis_from_db(analysis_uuid)
    else:
        logger.info(
            "outline_generation: no analysis_uuid; using text-driven synthetic analysis"
        )
        analysis_data = _analysis_from_user_request(user_input_data)
    audio_transcription, audio_sections = await get_audio_transcription_with_sections(
        audio_transcription_uuids or []
    )
    
    # 获取 user_option
    user_option = user_input_data.user_option if user_input_data else None
    
    # 不传 state.messages：跨节点内部 LLM 轨迹与多模型不兼容；梗概已有 analysis/转录等输入
    outline_artifact_path: Optional[str] = None
    outline_artifact = None
    outline_generation_result, raw_message, outline_artifact = await _generate_outline_with_deep_agent(
        user_input_data=user_input_data,
        analysis_data=analysis_data,
        audio_transcription=audio_transcription,
        sections=audio_sections,
        detected_language=state.get("detected_language"),
        user_option=user_option,
        thread_id=state.get("thread_id", "") or "",
        run_id=state.get("run_id", "") or "",
    )
    story_outline = outline_generation_result.story_outline
    
    # 保存到数据库并获取UUID
    story_outline_uuid = None

    from app.services.agent.video.outline_stage.persist import outline_additional_data
    from app.services.agent.stage_runtime.workspace import artifact_relpath

    if outline_artifact is not None:
        outline_artifact_path = artifact_relpath(
            state.get("thread_id", "") or "",
            state.get("run_id", "") or "",
            "outline.json",
        )
    
    # 保存故事大纲（使用asyncpg CRUD）
    # 首先保存故事大纲
    outline_db = await create_video_story_outline(
        run_id=state.get("run_id", ""),
        title=story_outline.title,
        theme=story_outline.theme,
        description=story_outline.description,
        key_message=story_outline.key_message,
        total_duration=story_outline.total_duration,
        style_guide=story_outline.style_guide,
        analysis_id=analysis_uuid,  # 关联视频分析UUID
        user_id=state.get("user_id", ""),
        conversation_id=str(state.get("conversation_id", "")),
        thread_id=state.get("thread_id", ""),
        additional_data=(
            outline_additional_data(outline_artifact_path) if outline_artifact_path else None
        ),
        audio_transcription_uuid=(state.get("audio_transcription_uuids") or [None])[0],
    )
    if not outline_db:
        raise BusinessException(BusinessExceptionCode.INTERNAL_SERVER_ERROR, "创建故事大纲失败")
    
    # 保存章节信息（含 audio_section_uuid 绑定曲式段落）
    for chapter in story_outline.structure.chapters:
        ch_data = {
            "user_id": state["user_id"],
            "conversation_id": str(state.get("conversation_id", "")),
            "thread_id": state.get("thread_id", ""),
            "run_id": state.get("run_id", ""),
            "story_outline_id": outline_db.uuid,
            "title": chapter.title,
            "description": chapter.description,
            "duration": chapter.duration,
            "order": chapter.order,
            "audio_segment_ids": chapter.audio_segment_ids,
        }
        if getattr(chapter, "audio_section_uuid", None) is not None:
            ch_data["audio_section_uuid"] = chapter.audio_section_uuid
        cues = getattr(chapter, "enhancement_cues", None)
        if cues:
            ch_data["additional_data"] = {
                "enhancement_cues": [
                    c.model_dump() if hasattr(c, "model_dump") else dict(c)
                    for c in cues
                ]
            }
        await create_chapter(chapter_data=ch_data)
    story_outline_uuid = outline_db.uuid
    logger.info(f"💾 故事大纲已保存到数据库: {story_outline_uuid}")

    if outline_artifact is not None and outline_artifact_path:
        try:
            from app.services.agent.video.outline_stage.persist import stamp_and_write_outline_artifact

            mode = "audio_driven" if audio_transcription is not None else "video_driven"
            outline_artifact_path = stamp_and_write_outline_artifact(
                artifact=outline_artifact,
                story_outline_uuid=story_outline_uuid,
                thread_id=state.get("thread_id", "") or "",
                run_id=state.get("run_id", "") or "",
                mode=mode,
            )
            logger.info("💾 outline artifact stamped path=%s", outline_artifact_path)
        except Exception as e:
            logger.warning("outline artifact stamp failed (non-blocking): %s", e)
    
    # ✅ raw_message 现在只包含新增的消息（不含历史）
    all_messages = raw_message if isinstance(raw_message, list) else ([raw_message] if raw_message else [])
    detected_language = state.get("detected_language", "zh")
    
    # ✅ 使用 generate_completion_message_stream 生成 user_message（支持流式）
    from ..utils.prompt_utils import generate_completion_message_stream
    user_message, completion_message = await generate_completion_message_stream(
        event_type=MessageType.STORY_OUTLINE_GENERATED,
        messages=all_messages,
        send_event_func=send_event_func,
        conversation_id=state.get("conversation_id"),
        lang=detected_language
    )
    
    # 追加 completion_message 到 all_messages
    if completion_message:
        all_messages.append(completion_message)
    
    logger.info(f"📝 生成的用户消息: {user_message}")
    
    # 发送生成完成事件 (使用LLM生成的user_message)
    await send_event_func(
        conversation_id=state["conversation_id"],
        event_type=MessageType.STORY_OUTLINE_GENERATED,
        message=user_message,
        extra_data={
            "story_outline_uuid": story_outline_uuid,
            "story_title": story_outline.title,
            "run_id": state.get("run_id"),
            "thread_id": state.get("thread_id"),
            **({"artifact_path": outline_artifact_path} if outline_artifact_path else {}),
        }
    )
    
    return {
        "story_outline_uuid": story_outline_uuid,
        "messages": all_messages
    }


def _segments_in_section(
    segments: List[Any],
    section_start: float,
    section_end: float,
    *,
    is_last_section: bool = False,
) -> List[str]:
    """返回 start 落在 [section_start, section_end) 内的 segment 的 uuid 列表。
    按 segment.start 归属，确保每个 segment 只分配给一个 section，避免跨界双分配。

    最后一节：若曲式 ``end_time`` 短于实际转录（如末段 section 为 [210,259) 而仍有
    ``start==259`` 的 segment），将本节上界抬到 ``max(section_end, max(seg.end))``，
    避免末秒片段漏进任何 chapter（进而无 scene / 仅 video_segment 占位）。"""
    st = float(section_start)
    et = float(section_end)
    if is_last_section and segments:
        timeline_end = max(float(getattr(s, "end", 0) or 0) for s in segments)
        et = max(et, timeline_end)
    out = []
    for seg in segments:
        if seg.start >= st and seg.start < et:
            out.append(seg.uuid)
    return out


def _section_start_end_raw(sec: Any) -> Tuple[float, float]:
    if isinstance(sec, dict):
        return float(sec.get("start_time") or 0), float(sec.get("end_time") or 0)
    return float(getattr(sec, "start_time", 0) or 0), float(getattr(sec, "end_time", 0) or 0)


def _effective_section_closed_bounds(
    sorted_sections: List[Any],
    section_index: int,
    segments: List[Any],
) -> Tuple[float, float]:
    """与 _segments_in_section 末节扩 et 一致；用于闭区间距离（选最近曲式窗）。"""
    st, et = _section_start_end_raw(sorted_sections[section_index])
    n = len(sorted_sections)
    if section_index == n - 1 and segments:
        timeline_end = max(float(getattr(s, "end", 0) or 0) for s in segments)
        et = max(et, timeline_end)
    return st, et


def _distance_point_to_closed_interval(t: float, st: float, et: float) -> float:
    if t < st:
        return st - t
    if t > et:
        return t - et
    return 0.0


def _assign_orphan_audio_segments_to_nearest_chapters(
    chapters: List[StoryChapter],
    segments: List[Any],
    sorted_sections: List[Any],
) -> None:
    """有 sections 且 chapter 与 section 已 1:1 时：将未出现在任何 chapter.audio_segment_ids 的切片
    按 segment.start 到各曲式窗（闭区间，末节 et 已扩）的最小距离归并到最近一章，并增加该章 duration。"""
    if not chapters or not segments or not sorted_sections:
        return
    if len(chapters) != len(sorted_sections):
        logger.warning(
            "orphan 音频归章跳过：章节数 %s 与曲式数 %s 不一致",
            len(chapters),
            len(sorted_sections),
        )
        return
    uuid_to_seg: Dict[str, Any] = {}
    for seg in segments:
        u = getattr(seg, "uuid", None)
        if u is not None and str(u) != "":
            uuid_to_seg[str(u)] = seg
    assigned: set = set()
    for ch in chapters:
        for u in ch.audio_segment_ids or []:
            assigned.add(str(u))
    orphans: List[Any] = []
    for seg in segments:
        u = getattr(seg, "uuid", None)
        if u is None or str(u) == "":
            continue
        if str(u) not in assigned:
            orphans.append(seg)
    if not orphans:
        return
    n_sec = len(sorted_sections)
    for seg in orphans:
        u = str(seg.uuid)
        t = float(getattr(seg, "start", 0) or 0)
        best_j = 0
        best_d = float("inf")
        for j in range(n_sec):
            st, et = _effective_section_closed_bounds(sorted_sections, j, segments)
            d = _distance_point_to_closed_interval(t, st, et)
            if d < best_d or (abs(d - best_d) < 1e-9 and j < best_j):
                best_d = d
                best_j = j
        ch = chapters[best_j]
        if ch.audio_segment_ids is None:
            ch.audio_segment_ids = []
        ch.audio_segment_ids.append(u)
        dur = float(getattr(seg, "duration", 0) or 0)
        if dur <= 0 and hasattr(seg, "end") and hasattr(seg, "start"):
            dur = float(seg.end) - float(seg.start)
        ch.duration = float(ch.duration) + dur
        logger.info(
            "📌 orphan 音频片段 start=%.3fs 归至最近曲式章节 order=%s（%s），+duration=%.3fs",
            t,
            best_j,
            getattr(ch, "title", ""),
            dur,
        )
    for ch in chapters:
        if ch.audio_segment_ids:
            ch.audio_segment_ids.sort(
                key=lambda uid: float(getattr(uuid_to_seg.get(str(uid)), "start", 0) or 0)
            )


def _convert_llm_mode_outline_to_outline(
    llm_outline: StoryOutlineForLLMMode,
    *,
    sections: Optional[List[Any]] = None,
    segments: Optional[List[Any]] = None,
    target_duration: Optional[float] = None,
    user_option: Optional[Any] = None,
) -> StoryOutline:
    """将 LLM 输出转为完整 StoryOutline。Audio 有 sections 时按段落 1:1 填；Audio 无 sections 时按 audio_segment_indices 填；Video 时采用 LLM duration，出错再纠正。"""
    chapters_llm = llm_outline.structure.chapters
    sorted_sections = sorted(sections or [], key=lambda s: getattr(s, "start_time", 0) or 0)
    uuid_to_segment = {seg.uuid: seg for seg in (segments or [])}
    index_to_uuid = {i: seg.uuid for i, seg in enumerate(segments or [])}
    uuid_to_index = {seg.uuid: i for i, seg in enumerate(segments or [])}

    chapters: List[StoryChapter] = []
    n_sec_outline = len(sorted_sections)
    for i, ch in enumerate(chapters_llm):
        duration = 0.0
        audio_section_uuid = None
        audio_segment_ids: List[str] = []

        if sections and len(sections) > 0 and i < len(sorted_sections) and segments:
            sec = sorted_sections[i]
            st = float(getattr(sec, "start_time", 0) or 0)
            et = float(getattr(sec, "end_time", 0) or 0)
            duration = max(0.0, et - st)
            _section_uuid = getattr(sec, "uuid", None)
            audio_section_uuid = str(_section_uuid) if _section_uuid is not None else None
            audio_segment_ids = _segments_in_section(
                segments, st, et, is_last_section=(i == n_sec_outline - 1)
            )
            logger.info(f"📊 章节 '{ch.title}': section 1:1, duration={duration:.2f}s, {len(audio_segment_ids)} 个片段")
        elif segments and ch.audio_segment_indices:
            for idx in ch.audio_segment_indices:
                if idx in index_to_uuid:
                    audio_segment_ids.append(index_to_uuid[idx])
            if audio_segment_ids:
                duration = sum(uuid_to_segment[u].duration for u in audio_segment_ids if u in uuid_to_segment)
            logger.info(f"📊 章节 '{ch.title}': audio_segment_indices -> {len(audio_segment_ids)} 个片段, duration={duration:.2f}s")
        elif target_duration is not None and not segments:
            # Video-driven：优先保留 LLM / deep-agent 不均匀时长，后面只在出错时纠正
            try:
                duration = float(ch.duration) if ch.duration is not None else 0.0
            except (TypeError, ValueError):
                duration = 0.0

        chapters.append(
            StoryChapter(
                id=ch.id,
                title=ch.title,
                description=ch.description,
                duration=duration,
                order=ch.order,
                audio_segment_ids=audio_segment_ids or None,
                audio_section_uuid=audio_section_uuid,
                enhancement_cues=list(ch.enhancement_cues) if ch.enhancement_cues else None,
            )
        )

    # 有 sections 时：章节数与段落 1:1，多退少补（prompt 已约束相同数量，后置兜底）
    if sections and len(sections) > 0:
        n_sec = len(sorted_sections)
        segs_for_section = segments or []
        if len(chapters) > n_sec:
            chapters = chapters[:n_sec]
            for i, c in enumerate(chapters):
                c.order = i
            logger.info(f"📋 章节数与段落 1:1：LLM 返回多于 {n_sec} 章，已截断为前 {n_sec} 章")
        elif len(chapters) < n_sec:
            for i in range(len(chapters), n_sec):
                sec = sorted_sections[i]
                st = float(getattr(sec, "start_time", 0) or 0)
                et = float(getattr(sec, "end_time", 0) or 0)
                sec_type = getattr(sec, "section_type", None) or f"段落{i+1}"
                ctx = getattr(sec, "suggested_context", None) or ""
                emotion = getattr(sec, "section_emotion", None) or ""
                desc = (ctx or emotion) or sec_type
                _section_uuid = getattr(sec, "uuid", None)
                seg_ids = _segments_in_section(
                    segs_for_section, st, et, is_last_section=(i == n_sec - 1)
                )
                chapters.append(
                    StoryChapter(
                        id=str(uuid_lib.uuid4()),
                        title=str(sec_type),
                        description=str(desc),
                        duration=max(0.0, et - st),
                        order=i,
                        audio_segment_ids=seg_ids or None,
                        audio_section_uuid=str(_section_uuid) if _section_uuid is not None else None,
                    )
                )
            logger.info(f"📋 章节数与段落 1:1：LLM 返回少于 {n_sec} 章，已按段落补足至 {n_sec} 章")
        if segs_for_section:
            _assign_orphan_audio_segments_to_nearest_chapters(
                chapters, segs_for_section, sorted_sections
            )

    # Audio 无 sections 时：兜底未分配片段
    if segments and not (sections and len(sections) > 0):
        assigned = set()
        for c in chapters:
            for u in c.audio_segment_ids or []:
                assigned.add(uuid_to_index.get(u, -1))
        unassigned = [i for i in range(len(segments)) if i not in assigned]
        if unassigned and chapters:
            for idx in unassigned:
                chapters[0].audio_segment_ids = (chapters[0].audio_segment_ids or []) + [index_to_uuid[idx]]
            if chapters[0].audio_segment_ids:
                chapters[0].duration = sum(
                    uuid_to_segment[u].duration for u in chapters[0].audio_segment_ids if u in uuid_to_segment
                )

    total = sum(c.duration for c in chapters)
    story_outline = StoryOutline(
        title=llm_outline.title,
        theme=llm_outline.theme,
        structure=StoryStructure(chapters=chapters),
        key_message=llm_outline.key_message,
        total_duration=float(round(total, 2)) if total > 0 else float(llm_outline.total_duration) if llm_outline.total_duration is not None else 0.0,
        style_guide=llm_outline.style_guide,
        description=llm_outline.description,
        user_message=llm_outline.user_message or "",
    )
    if total > 0:
        logger.info(
            "[时长诊断] outline 章节 duration 求和 raw=%.6f StoryOutline.total_duration(原逻辑 float(round(sum,2)))=%.4f",
            float(total),
            float(round(total, 2)),
        )

    # Video driven：优先保留 LLM duration；仅当 sum≠target / ≤0 时程序纠正
    if target_duration is not None and not segments:
        story_outline = _correct_outline_duration_if_needed(
            story_outline, target_duration, user_option
        )

    # 统一 0-based order（兼容 LLM 输出 1-based 或乱序）
    reindex_chapter_orders_inplace(story_outline.structure.chapters)

    return story_outline


async def _process_audio_driven_outline(
    story_outline: StoryOutline,
    audio_transcription: AudioTranscription,
    sections: Optional[List[Any]] = None,
) -> StoryOutline:
    """处理 Audio Driven 模式的 outline：
    - 若有 sections：章节与段落 1:1，按 order 对齐；duration 与 audio_segment_ids 由段落时间范围推导，并写入 audio_section_uuid。
    - 若无 sections：将 LLM 返回的 index 转为 UUID，兜底未分配片段，再按片段计算 duration。
    """
    chapters = story_outline.structure.chapters
    segments = audio_transcription.segments
    uuid_to_segment = {seg.uuid: seg for seg in segments}

    if sections and len(sections) > 0:
        # 按 start_time 排序段落（与 DB 顺序一致）
        sorted_sections = sorted(sections, key=lambda s: getattr(s, "start_time", 0) or 0)
        n_sec_proc = len(sorted_sections)
        for i, chapter in enumerate(chapters):
            if i >= n_sec_proc:
                break
            sec = sorted_sections[i]
            st = float(getattr(sec, "start_time", 0) or 0)
            et = float(getattr(sec, "end_time", 0) or 0)
            chapter.audio_section_uuid = getattr(sec, "uuid", None)
            chapter.duration = max(0, et - st)
            chapter.audio_segment_ids = _segments_in_section(
                segments, st, et, is_last_section=(i == n_sec_proc - 1)
            )
            logger.info(f"📊 章节 '{chapter.title}': section 1:1, duration={chapter.duration:.2f}s, {len(chapter.audio_segment_ids)} 个片段")
        if segments:
            _assign_orphan_audio_segments_to_nearest_chapters(
                chapters, segments, sorted_sections
            )
    else:
        # 原有逻辑：index -> UUID，兜底未分配，再按片段算 duration
        index_to_uuid = {i: seg.uuid for i, seg in enumerate(segments)}
        str_index_to_uuid = {str(i): seg.uuid for i, seg in enumerate(segments)}
        uuid_to_index = {seg.uuid: i for i, seg in enumerate(segments)}
        logger.info(f"📋 创建音频片段映射: {len(index_to_uuid)} 个片段")

        assigned_indices = set()
        for chapter in chapters:
            if chapter.audio_segment_ids:
                uuid_list = []
                for idx in chapter.audio_segment_ids:
                    if idx in str_index_to_uuid:
                        uuid_list.append(str_index_to_uuid[idx])
                        assigned_indices.add(int(idx))
                    else:
                        logger.warning(f"⚠️ 章节 '{chapter.title}' 的音频片段 index '{idx}' 无效")
                chapter.audio_segment_ids = uuid_list
                logger.info(f"📌 章节 '{chapter.title}': 分配了 {len(uuid_list)} 个音频片段")

        total_segments = len(segments)
        unassigned_indices = set(range(total_segments)) - assigned_indices
        if unassigned_indices:
            logger.warning(f"⚠️ 发现 {len(unassigned_indices)} 个未分配的音频片段: {sorted(unassigned_indices)}")
            for unassigned_idx in sorted(unassigned_indices):
                best_chapter = None
                min_distance = float('inf')
                for ch in chapters:
                    if not ch.audio_segment_ids:
                        continue
                    for u in ch.audio_segment_ids:
                        idx = uuid_to_index.get(u)
                        if idx is not None and abs(idx - unassigned_idx) < min_distance:
                            min_distance = abs(idx - unassigned_idx)
                            best_chapter = ch
                if best_chapter:
                    best_chapter.audio_segment_ids.append(index_to_uuid[unassigned_idx])
                    best_chapter.audio_segment_ids.sort(key=lambda u: uuid_to_index[u])
                elif chapters:
                    ch0 = chapters[0]
                    if not ch0.audio_segment_ids:
                        ch0.audio_segment_ids = []
                    ch0.audio_segment_ids.append(index_to_uuid[unassigned_idx])

        for chapter in chapters:
            if chapter.audio_segment_ids:
                chapter.duration = sum(
                    uuid_to_segment[u].duration for u in chapter.audio_segment_ids if u in uuid_to_segment
                )
                logger.info(f"📊 章节 '{chapter.title}': duration = {chapter.duration:.2f}s ({len(chapter.audio_segment_ids)} 个片段)")
            else:
                logger.warning(f"⚠️ 章节 '{chapter.title}' 没有分配音频片段")

    story_outline.total_duration = sum(c.duration for c in chapters)
    logger.info(f"📈 故事总时长: {story_outline.total_duration}s")
    return story_outline


def _allocate_durations_summing_to_target(
    target_duration: float,
    part_count: int,
    unit: int,
) -> List[float]:
    """兼容旧名；实现见 app.agent_config.duration.allocate_durations_summing_to_target。"""
    from app.agent_config.duration import allocate_durations_summing_to_target
    return allocate_durations_summing_to_target(target_duration, part_count, unit)


def _llm_chapter_durations_are_valid(
    chapters: List[StoryChapter],
    target_duration: float,
    *,
    tolerance: float = 1.0,
) -> bool:
    """LLM 章节秒数可用：全为正、总和≈target。"""
    if not chapters:
        return False
    durs = [float(c.duration or 0) for c in chapters]
    if any(d <= 0 for d in durs):
        return False
    return abs(sum(durs) - float(target_duration)) <= tolerance


def _correct_outline_duration_if_needed(
    story_outline: StoryOutline,
    target_duration: float,
    user_option: Optional[Any] = None,
) -> StoryOutline:
    """Video-driven：优先保留 LLM 章节 duration；仅出错时程序纠正。"""
    chapters = story_outline.structure.chapters
    target = float(target_duration or 0)
    if not chapters:
        return story_outline

    if target <= 0:
        return _normalize_outline_duration(story_outline, target, user_option)

    if _llm_chapter_durations_are_valid(chapters, target):
        # 吸一点浮点误差到最后一章，保持 LLM 相对节奏
        head = sum(float(c.duration or 0) for c in chapters[:-1])
        chapters[-1].duration = float(round(target - head, 3))
        story_outline.total_duration = float(target)
        logger.info(
            "✅ 保留 LLM 章节时长：durations=%s sum=%.3f target=%.3f",
            [c.duration for c in chapters],
            sum(c.duration for c in chapters),
            target,
        )
        return story_outline

    logger.warning(
        "⚠️ LLM 章节时长无效（durs=%s sum=%.3f target=%.3f），程序纠正",
        [c.duration for c in chapters],
        sum(float(c.duration or 0) for c in chapters),
        target,
    )
    return _normalize_outline_duration(story_outline, target, user_option)


def _normalize_outline_duration(
    story_outline: StoryOutline,
    target_duration: float,
    user_option: Optional[Any] = None
) -> StoryOutline:
    """Video-driven 程序纠正：按规划单元重分配章节时长（禁止负时长）。"""
    from app.agent_config.duration import (
        allocate_durations_summing_to_target,
        get_video_driven_duration_values,
        preferred_planning_unit,
    )

    chapters = story_outline.structure.chapters
    if not chapters:
        return story_outline

    target = float(target_duration or 0)
    if target <= 0:
        for chapter in chapters:
            chapter.duration = 0.0
        story_outline.total_duration = 0.0
        logger.warning("⚠️ target_duration<=0，章节时长全部置 0")
        return story_outline

    video_durations = get_video_driven_duration_values(user_option)
    unit = preferred_planning_unit(video_durations)
    durs = allocate_durations_summing_to_target(target, len(chapters), unit)
    for chapter, dur in zip(chapters, durs):
        chapter.duration = float(round(dur, 3))

    if chapters:
        chapters[-1].duration = float(
            round(target - sum(c.duration for c in chapters[:-1]), 3)
        )
        if chapters[-1].duration < 0:
            chapters[-1].duration = 0.0

    story_outline.total_duration = float(target)
    logger.info(
        "📐 程序纠正章节时长（Video Driven）：chapters=%s unit=%ss durations=%s sum=%.3f target=%.3f",
        len(chapters),
        unit,
        [c.duration for c in chapters],
        sum(c.duration for c in chapters),
        target,
    )
    return story_outline


async def _generate_outline_with_deep_agent(
    *,
    user_input_data: Optional[UserInput],
    analysis_data: VideoAnalysisResult,
    audio_transcription: Optional[AudioTranscription],
    sections: Optional[List[Any]],
    detected_language: Optional[str],
    user_option: Optional[Any],
    thread_id: str,
    run_id: str,
) -> Tuple[OutlineGenerationResult, List[BaseMessage], Any]:
    """OM-style: export JSON inputs → create_deep_agent + outline-director skill → artifact → convert."""
    if not (thread_id and run_id):
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "缺少 thread_id/run_id，无法走 outline deep-agent",
        )
    from app.services.agent.video.outline_stage import (
        export_outline_inputs,
        generate_outline_via_deep_agent,
    )

    is_audio_driven = audio_transcription is not None
    target_duration = (
        audio_transcription.duration if audio_transcription else (analysis_data.duration if analysis_data else 0)
    )
    logger.info(
        "🎯 outline deep-agent path: target=%.1fs mode=%s",
        target_duration,
        "audio_driven" if is_audio_driven else "video_driven",
    )

    input_paths = export_outline_inputs(
        thread_id=thread_id,
        run_id=run_id,
        user_input_data=user_input_data,
        analysis_data=analysis_data,
        audio_transcription=audio_transcription,
        sections=sections,
        user_option=user_option,
    )
    # Pass section count for audio tool validation
    if is_audio_driven and sections:
        input_paths["expect_section_count"] = len(sections)

    artifact, agent_messages = await generate_outline_via_deep_agent(
        thread_id=thread_id,
        run_id=run_id,
        input_paths=input_paths,
        detected_language=detected_language,
    )

    llm_outline = artifact.to_llm_mode()
    story_outline = _convert_llm_mode_outline_to_outline(
        llm_outline,
        sections=sections if is_audio_driven else None,
        segments=audio_transcription.segments if is_audio_driven else None,
        target_duration=target_duration if not is_audio_driven else None,
        user_option=user_option if not is_audio_driven else None,
    )
    if not is_audio_driven:
        duration_check = _validate_outline_duration(story_outline, analysis_data, audio_transcription)
        if not duration_check.is_valid:
            logger.warning("⚠️ 时长验证失败，误差: %s秒，使用代码规则均分", duration_check.error)
            story_outline = _normalize_outline_duration(story_outline, target_duration, user_option)

    # Sync artifact chapters from normalized outline (durations / audio ids)
    from app.contracts.artifacts.outline import OutlineChapterArtifact

    artifact.chapters = [
        OutlineChapterArtifact(
            id=ch.id,
            order=ch.order,
            title=ch.title,
            description=ch.description,
            duration=ch.duration,
            enhancement_cues=list(ch.enhancement_cues or []),
        )
        for ch in story_outline.structure.chapters
    ]
    artifact.total_duration = int(round(float(story_outline.total_duration)))
    artifact.title = story_outline.title
    artifact.theme = story_outline.theme
    artifact.description = story_outline.description
    artifact.key_message = story_outline.key_message
    artifact.style_guide = story_outline.style_guide

    result = OutlineGenerationResult(success=True, story_outline=story_outline, duration_validation=None)
    logger.info("✅ outline deep-agent 故事梗概生成成功")
    return result, agent_messages, artifact


