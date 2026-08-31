"""
build_snapshot_from_db — 从 DB 构建完整 ProjectSnapshot。

根据 run_id + user_id 查询各表，统计各阶段完成情况，返回结构化快照。
"""
import logging
from collections import Counter
from typing import Dict, List, Optional

from ....models.project_snapshot import (
    OutlinePreview,
    PhaseStatus,
    PipelinePhase,
    ProjectSnapshot,
    StageStats,
)

_OUTLINE_DESC_PREVIEW_CHARS = 120


def _truncate_preview(text: str, max_chars: int = _OUTLINE_DESC_PREVIEW_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _build_outline_preview(outline: object, analysis: object = None) -> Optional[OutlinePreview]:
    """从大纲行构造短预览，便于 LLM 判断标题/主题/描述是否与新风格冲突。"""
    if outline is None:
        return None
    themes = getattr(outline, "themes", None) or []
    if isinstance(themes, list):
        theme_text = "、".join(str(t).strip() for t in themes if str(t).strip())
    else:
        theme_text = str(themes).strip()

    style_tags = ""
    if analysis is not None:
        raw_prefs = getattr(analysis, "style_preferences", None)
        if isinstance(raw_prefs, list):
            style_tags = "、".join(str(x).strip() for x in raw_prefs if str(x).strip())
        elif raw_prefs:
            style_tags = str(raw_prefs).strip()

    preview: OutlinePreview = {
        "title": (getattr(outline, "title", None) or "").strip(),
        "theme": theme_text,
        "description": _truncate_preview(getattr(outline, "description", None) or ""),
        "key_message": _truncate_preview(getattr(outline, "key_message", None) or "", 80),
        "style_tags": style_tags,
    }
    if not any(preview.values()):
        return None
    return preview

logger = logging.getLogger(__name__)


def _build_versions_count(
    versions: list,
    parent_id_attr: str,
    parents: list,
) -> Dict[int, int]:
    """统计每个 shot_number 有多少个版本（只记 >1 的）。"""
    parent_version_counts: Dict[str, int] = Counter()
    for v in versions:
        pid = getattr(v, parent_id_attr, None)
        if pid:
            parent_version_counts[pid] += 1

    parent_id_to_shot: Dict[str, int] = {}
    for p in parents:
        sn = getattr(p, "shot_number", None)
        pid = getattr(p, "uuid", None)
        if sn is not None and pid:
            parent_id_to_shot[pid] = sn

    result: Dict[int, int] = {}
    for pid, count in parent_version_counts.items():
        if count > 1 and pid in parent_id_to_shot:
            result[parent_id_to_shot[pid]] = count
    return result


def _compute_simple_stage(items: list) -> StageStats:
    """无 success 字段的实体（outline、scene 等），存在即成功。"""
    total = len(items)
    return StageStats(
        status=PhaseStatus.COMPLETED.value if total > 0 else PhaseStatus.NOT_STARTED.value,
        total=total,
        succeeded=total,
        failed_items=[],
        versions_count={},
    )


def _compute_versioned_stage(
    parents: list,
    versions: list,
    parent_id_attr: str,
) -> StageStats:
    """有版本表的实体（keyframe、video_generation）。

    按 current version 的 success 判断成功/失败。
    """
    if not parents:
        return StageStats(
            status=PhaseStatus.NOT_STARTED.value,
            total=0, succeeded=0, failed_items=[], versions_count={},
        )

    version_counts = _build_versions_count(versions, parent_id_attr, parents)

    # 找每个 parent 的最新 version
    latest: Dict[str, object] = {}
    for v in versions:
        pid = getattr(v, parent_id_attr, None)
        if pid is None:
            continue
        if pid not in latest:
            latest[pid] = v
        elif getattr(v, "version_number", 0) > getattr(latest[pid], "version_number", 0):
            latest[pid] = v

    succeeded = 0
    failed_items: List[int] = []
    for p in parents:
        cv = latest.get(p.uuid)
        if cv and getattr(cv, "success", True):
            succeeded += 1
        else:
            sn = getattr(p, "shot_number", None)
            if sn is not None:
                failed_items.append(sn)

    total = len(parents)
    if succeeded == total:
        status = PhaseStatus.COMPLETED.value
    elif succeeded == 0:
        status = PhaseStatus.FAILED.value
    else:
        status = PhaseStatus.PARTIAL.value

    return StageStats(
        status=status,
        total=total,
        succeeded=succeeded,
        failed_items=failed_items,
        versions_count=version_counts,
    )


def _determine_phase(
    outline: StageStats,
    characters: StageStats,
    scenes: StageStats,
    keyframes: StageStats,
    videos: StageStats,
    narrations: StageStats,
    segments: StageStats,
    assembly: StageStats,
    music: Optional[StageStats] = None,
) -> str:
    """根据各阶段的 status 推断当前主阶段。"""
    _started = lambda s: s.get("status") not in (PhaseStatus.NOT_STARTED.value, None)

    if assembly.get("status") == PhaseStatus.COMPLETED.value:
        return PipelinePhase.COMPLETED.value
    if _started(segments):
        return PipelinePhase.SEGMENTS.value
    if _started(videos):
        return PipelinePhase.VIDEO.value
    if _started(keyframes):
        return PipelinePhase.KEYFRAME.value
    if _started(scenes):
        return PipelinePhase.SCENE.value
    if _started(characters):
        return PipelinePhase.CHARACTER.value
    if _started(outline):
        return PipelinePhase.OUTLINE.value
    if music and _started(music):
        return PipelinePhase.MUSIC.value
    return PipelinePhase.USER_INPUT.value


_GATE_TO_PHASE: Dict[str, str] = {
    "after_music": PipelinePhase.MUSIC.value,
    "after_outline": PipelinePhase.OUTLINE.value,
    "after_character": PipelinePhase.GATE_CHARACTER.value,
    "after_storyboard_detail": PipelinePhase.STORYBOARD.value,
    "after_keyframe_reflection": PipelinePhase.GATE_KEYFRAME.value,
    "after_shots": PipelinePhase.GATE_SHOTS.value,
}

_FAILED_STAGE_TO_PHASE: Dict[str, str] = {
    "music": PipelinePhase.MUSIC.value,
    "outline": PipelinePhase.OUTLINE.value,
    "character": PipelinePhase.GATE_CHARACTER.value,
    "keyframe": PipelinePhase.GATE_KEYFRAME.value,
    "video": PipelinePhase.GATE_SHOTS.value,
}


def _phase_from_pending_gate(pending_gate: Optional[str]) -> Optional[str]:
    """门控/失败暂停点比产物统计更能反映「当前在等什么」。"""
    if not pending_gate:
        return None
    if pending_gate in _GATE_TO_PHASE:
        return _GATE_TO_PHASE[pending_gate]
    if pending_gate.startswith("failed_"):
        return _FAILED_STAGE_TO_PHASE.get(pending_gate[len("failed_"):])
    return None


async def _resolve_pipeline_task_status(
    thread_id: str = "",
    run_id: str = "",
) -> str:
    """Pipeline 任务状态：优先 thread 上最新 resume run（门控 resume 链）。

    ChatAgent 每次问「现在在哪个阶段」也会在 thread 上新建 main run（短暂 running），
    不能按 created_at 取全 thread 最新 run。Pipeline 进度在 resume 链上；无 resume 时
    回退到锚点 main run（delegated_va_run_id）。
    """
    try:
        if thread_id:
            from ....models.database import get_asyncpg_pool
            from ....utils.asyncpg_utils import fetch_one
            pool = get_asyncpg_pool()
            async with pool.acquire() as conn:
                row = await fetch_one(
                    conn,
                    """
                    SELECT run_id, status, run_type, created_at
                    FROM conversation_runs
                    WHERE thread_id = $1
                      AND run_type = 'resume'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    thread_id,
                )
            if row and row.get("status"):
                logger.info(
                    "resolve_pipeline_task_status: source=thread_latest_resume "
                    f"thread_id={thread_id} anchor_run_id={run_id} "
                    f"latest_run_id={row.get('run_id')} status={row.get('status')} "
                    f"run_type={row.get('run_type')} created_at={row.get('created_at')}"
                )
                return row["status"]
            logger.info(
                "resolve_pipeline_task_status: source=no_resume_on_thread "
                f"thread_id={thread_id} anchor_run_id={run_id}"
            )
        if run_id:
            from ....crud.conversation import async_get_conversation_run_by_run_id
            run_obj = await async_get_conversation_run_by_run_id(run_id)
            if run_obj and run_obj.status:
                logger.info(
                    "resolve_pipeline_task_status: source=anchor_main "
                    f"thread_id={thread_id} anchor_run_id={run_id} status={run_obj.status}"
                )
                return run_obj.status
    except Exception as e:
        logger.warning(
            f"resolve_pipeline_task_status: failed thread_id={thread_id} "
            f"anchor_run_id={run_id} error={e}"
        )
    logger.info(
        "resolve_pipeline_task_status: source=unknown "
        f"thread_id={thread_id} anchor_run_id={run_id}"
    )
    return "unknown"


async def _resolve_pending_gate(thread_id: str = "") -> Optional[str]:
    """从 thread 上最新未 continued 的 interrupt 解析 pending_gate（含 after_* 与 failed_*）。

    只读：若全部 continued，但 conversation_runs 仍有 interrupted（典型：resume 已失败、
    continued 尚未回滚），仍展示最近门控 step，避免 companion 因 pending_gate=None 拒续。
    """
    if not (thread_id or "").strip():
        return None
    try:
        import json
        from ....crud.conversation import (
            async_get_conversation_by_thread_id,
            async_get_conversation_messages,
        )
        from ....models.database import get_asyncpg_pool

        conversation = await async_get_conversation_by_thread_id(thread_id)
        if not conversation:
            return None
        messages = await async_get_conversation_messages(conversation.id)
        last_continued_step: Optional[str] = None
        for msg in reversed(messages):
            if msg.get("event_type") != "interrupt":
                continue
            event_data = msg.get("event_data")
            if isinstance(event_data, str):
                try:
                    event_data = json.loads(event_data)
                except Exception:
                    continue
            if not event_data:
                continue
            interrupt_data = event_data.get("interrupt_data")
            step = (
                str(interrupt_data.get("step"))
                if isinstance(interrupt_data, dict) and interrupt_data.get("step")
                else None
            )
            if not step:
                continue
            if event_data.get("continued"):
                if last_continued_step is None:
                    last_continued_step = step
                continue
            return step
        if last_continued_step:
            pool = get_asyncpg_pool()
            async with pool.acquire() as conn:
                still = await conn.fetchval(
                    "SELECT run_id FROM conversation_runs "
                    "WHERE thread_id = $1 AND status = 'interrupted' "
                    "ORDER BY created_at DESC LIMIT 1",
                    thread_id,
                )
            if still:
                return last_continued_step
        return None
    except Exception as e:
        logger.warning(
            f"_resolve_pending_gate: failed thread_id={thread_id} error={e}"
        )
        return None


async def build_snapshot_from_db(
    run_id: str,
    user_id: str = "",
    thread_id: str = "",
) -> ProjectSnapshot:
    """根据 thread_id（优先）或 run_id 从 DB 查所有表，构建 ProjectSnapshot。

    artifact 表都有 thread_id 字段，直接 WHERE thread_id = $1 一条查询搞定，
    不需要先解析 run_ids 再逐个遍历。无 thread_id 时回退到 run_id 查询。

    Args:
        run_id: 视频生成任务的 run_id（兜底用）。
        user_id: 用户 ID（部分 CRUD 需要）。
        thread_id: LangGraph 线程 ID（优先使用）。
    """
    from ....crud.video.video_character import (
        get_character_versions_batch,
    )
    from ....crud.video.video_keyframe import (
        get_keyframe_versions_by_keyframe_ids,
    )
    from ....crud.video.video_generation import (
        get_video_generation_versions_by_video_generation_ids,
    )

    use_thread = bool(thread_id)
    logger.info(f"build_snapshot: thread_id={thread_id}, run_id={run_id}, use_thread={use_thread}")

    if use_thread:
        from ....crud.video.video_story import (
            get_video_story_outline_by_thread_id,
            get_scenes_by_thread_id,
        )
        from ....crud.video.video_character import get_characters_by_thread_id
        from ....crud.video.video_keyframe import get_keyframes_by_thread_id
        from ....crud.video.video_generation import get_video_generations_by_thread_id
        from ....crud.video.video_audio import (
            get_narrations_by_thread_id,
            get_music_generations_by_thread_id,
        )
        from ....crud.video.video_segment import get_video_segments_with_data_by_thread_id
        from ....crud.video.video_other import get_video_assemblies_by_thread_id

        outline = await get_video_story_outline_by_thread_id(thread_id)
        scenes = await get_scenes_by_thread_id(thread_id)
        characters = await get_characters_by_thread_id(thread_id, user_id) if user_id else []
        keyframes = await get_keyframes_by_thread_id(thread_id)
        video_gens = await get_video_generations_by_thread_id(thread_id)
        narrations = await get_narrations_by_thread_id(thread_id)
        music_gens = await get_music_generations_by_thread_id(thread_id)
        segments = await get_video_segments_with_data_by_thread_id(thread_id)
        assemblies = await get_video_assemblies_by_thread_id(thread_id)
    else:
        from ....crud.video.video_story import (
            get_video_story_outline_by_run_id,
            get_scenes_by_run_id,
        )
        from ....crud.video.video_character import get_characters_by_run_id
        from ....crud.video.video_keyframe import get_keyframes_by_run_id
        from ....crud.video.video_generation import get_video_generations_by_run_id
        from ....crud.video.video_audio import (
            get_narrations_by_run_id,
            get_music_generations_by_run_id,
        )
        from ....crud.video.video_segment import get_video_segments_with_data_by_run_id

        outline = await get_video_story_outline_by_run_id(run_id)
        scenes = await get_scenes_by_run_id(run_id)
        characters = await get_characters_by_run_id(run_id, user_id) if user_id else []
        keyframes = await get_keyframes_by_run_id(run_id)
        video_gens = await get_video_generations_by_run_id(run_id)
        narrations = await get_narrations_by_run_id(run_id)
        music_gens = await get_music_generations_by_run_id(run_id)
        segments = await get_video_segments_with_data_by_run_id(run_id)
        assemblies = []

    outline_stats = _compute_simple_stage([outline] if outline else [])

    analysis = None
    if outline is not None:
        try:
            from ....crud.video.video_other import (
                get_video_analysis_by_thread_id,
                get_video_analysis_by_uuid,
            )
            analysis_id = getattr(outline, "analysis_id", None)
            if analysis_id:
                analysis = await get_video_analysis_by_uuid(analysis_id)
            elif use_thread and thread_id:
                analysis = await get_video_analysis_by_thread_id(thread_id)
        except Exception as e:
            logger.warning("build_snapshot: load analysis for outline_preview failed: %s", e)
    outline_preview = _build_outline_preview(outline, analysis)

    scene_stats = _compute_simple_stage(scenes)

    char_version_counts: Dict[int, int] = {}
    if characters:
        char_uuids = [c.uuid for c in characters]
        char_versions_map = await get_character_versions_batch(char_uuids, user_id)
        all_char_versions = [v for vlist in char_versions_map.values() for v in vlist]
        char_version_counts = _build_versions_count(all_char_versions, "video_character_id", characters)
    char_stats = StageStats(
        status=PhaseStatus.COMPLETED.value if characters else PhaseStatus.NOT_STARTED.value,
        total=len(characters),
        succeeded=len(characters),
        failed_items=[],
        versions_count=char_version_counts,
    )

    kf_versions: list = []
    if keyframes:
        kf_uuids = [k.uuid for k in keyframes]
        kf_versions = await get_keyframe_versions_by_keyframe_ids(kf_uuids)
    keyframe_stats = _compute_versioned_stage(keyframes, kf_versions, "keyframe_id")

    vg_versions: list = []
    if video_gens:
        vg_uuids = [v.uuid for v in video_gens]
        vg_versions = await get_video_generation_versions_by_video_generation_ids(vg_uuids)
    video_stats = _compute_versioned_stage(video_gens, vg_versions, "video_generation_id")

    total_shots = 0
    if video_gens:
        total_shots = len(video_gens)
    elif keyframes:
        total_shots = len(set(getattr(k, "shot_number", 0) for k in keyframes))

    narration_stats = _compute_simple_stage(narrations)
    music_stats = _compute_simple_stage(music_gens)
    segment_stats = _compute_simple_stage(segments)

    successful_assemblies = [a for a in assemblies if getattr(a, "success", False)]
    assembly_stats = _compute_simple_stage(successful_assemblies)

    # 总时长：从最新成功的 assembly 取
    total_duration_sec = 0.0
    if successful_assemblies:
        total_duration_sec = getattr(successful_assemblies[0], "total_duration", 0.0) or 0.0

    phase = _determine_phase(
        outline_stats, char_stats, scene_stats, keyframe_stats,
        video_stats, narration_stats, segment_stats, assembly_stats,
        music_stats,
    )

    task_status = await _resolve_pipeline_task_status(thread_id=thread_id, run_id=run_id)
    pending_gate = await _resolve_pending_gate(thread_id) if use_thread else None
    gate_phase = _phase_from_pending_gate(pending_gate)
    if gate_phase:
        phase = gate_phase
    logger.info(
        f"build_snapshot_result: thread_id={thread_id} anchor_run_id={run_id} "
        f"phase={phase} task_status={task_status} pending_gate={pending_gate} "
        f"total_shots={total_shots} total_duration_sec={total_duration_sec:.1f}"
    )

    snap = ProjectSnapshot(
        run_id=thread_id if use_thread else run_id,
        phase=phase,
        task_status=task_status,
        total_shots=total_shots,
        total_duration_sec=total_duration_sec,
        outline=outline_stats,
        characters=char_stats,
        scenes=scene_stats,
        keyframes=keyframe_stats,
        videos=video_stats,
        narrations=narration_stats,
        music=music_stats,
        segments=segment_stats,
        assembly=assembly_stats,
        pending_gate=pending_gate,
    )
    if outline_preview:
        snap["outline_preview"] = outline_preview
    return snap
