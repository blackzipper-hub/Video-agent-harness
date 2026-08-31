"""
错误追踪相关的 CRUD 操作 - asyncpg版本
"""

import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import json
import uuid as uuid_lib
import msgspec

from ..models.error_tracking import (
    VideoTaskRecordDB,
    VideoShotEditRecordDB,
    VideoStoryboardEditRecordDB,
    VideoCharacterEditRecordDB
)
from ..models.database import get_asyncpg_pool
from ..utils.asyncpg_utils import fetch_one, fetch_all, fetch_val, execute, insert_and_return, now_utc
from .video.row_normalize import row_to_struct_safe
from ..schemas.video_llm import VideoConsistencyLevel

logger = logging.getLogger(__name__)

# 从 DB 读出的 artifact 为字符串，用 enum 的 value 集合判断是否通过
_IMAGE_ARTIFACT_PASS_VALUES = {VideoConsistencyLevel.GOOD.value, VideoConsistencyLevel.ACCEPTABLE.value, VideoConsistencyLevel.N_A.value}


# ========== msgspec Struct 定义 ==========

class TaskRecordResult(msgspec.Struct, kw_only=True):
    """任务记录查询结果"""
    # === 基础字段 ===
    id: int
    uuid: str
    
    # === 任务标识 ===
    task_id: str
    conversation_id: str
    thread_id: str
    user_id: str
    
    # === 语言和配置 ===
    detected_language: Optional[str]
    generation_config: Optional[Dict[str, Any]]
    actual_target_duration: Optional[float]
    
    # === 任务输入 ===
    task_input: str
    user_input_data: Optional[Dict[str, Any]]
    
    # === 任务状态 ===
    task_start_time: datetime
    task_finish_time: Optional[datetime]
    task_status: str
    
    # === 关联的 UUID 引用 ===
    analysis_uuid: Optional[str]
    audio_transcription_uuids: Optional[List[str]]
    story_outline_uuid: Optional[str]
    character_uuids: Optional[List[str]]
    scene_uuids: Optional[List[str]]
    shot_uuids: Optional[List[str]]
    keyframe_uuids: Optional[List[str]]
    narration_uuids: Optional[List[str]]
    audio_effect_uuids: Optional[List[str]]
    video_generation_uuids: Optional[List[str]]
    music_generation_uuids: Optional[List[str]]
    video_segments_uuids: Optional[List[str]]
    video_assembly_uuid: Optional[str]
    final_video_url: Optional[str]

    # === 计费（6 字段）===
    billing_status: Optional[str] = None
    langsmith_cost: Optional[float] = None
    cost: Optional[float] = None
    cost_calculated: Optional[bool] = None
    credits_deducted: Optional[bool] = None
    credits_amount: Optional[int] = None

    # === Tool 一致性汇总（列表/详情用，读路径 row_to_struct_safe 防御 DB 多列）===
    tool_consistency_summary: Optional[Dict[str, Any]] = None

    # === 时间戳 ===
    created_at: datetime
    updated_at: datetime


# ========== Helper Functions ==========

_TASK_RECORD_LIST_FIELDS = (
    "audio_transcription_uuids",
    "character_uuids",
    "scene_uuids",
    "shot_uuids",
    "keyframe_uuids",
    "narration_uuids",
    "audio_effect_uuids",
    "video_generation_uuids",
    "music_generation_uuids",
    "video_segments_uuids",
)

_TASK_RECORD_DICT_FIELDS = (
    "generation_config",
    "user_input_data",
    "tool_consistency_summary",
)


def _row_to_task_record(row: Optional[Dict]) -> Optional[TaskRecordResult]:
    """将数据库行转换为TaskRecordResult对象（只传 schema 字段，DB 多列不崩）"""
    if row:
        row = dict(row)
        from .video.row_normalize import set_effective_duration
        set_effective_duration(row, "actual_target_duration_sec", "actual_target_duration")
    return row_to_struct_safe(
        row, TaskRecordResult,
        list_fields=_TASK_RECORD_LIST_FIELDS, dict_fields=_TASK_RECORD_DICT_FIELDS
    )


class ShotEditRecordResult(msgspec.Struct, kw_only=True):
    """Shot 编辑记录查询结果"""
    id: int
    uuid: str
    task_record_id: Optional[str]
    video_generation_id: str
    cascaded_from_storyboard_edit_id: Optional[str]
    user_id: str
    conversation_id: str
    thread_id: str
    run_id: str
    shot_number: int
    old_version_id: str
    new_version_id: str
    old_version_number: int
    new_version_number: int
    old_video_url: str
    new_video_url: str
    duration: Optional[float]
    model: str
    old_prompt: str
    new_prompt: Optional[str]
    user_action: str
    user_feedback: Optional[str]
    edit_instruction: Optional[str]
    success: bool
    error_msg: Optional[str]
    created_at: datetime


class StoryboardEditRecordResult(msgspec.Struct, kw_only=True):
    """Storyboard 编辑记录查询结果"""
    id: int
    uuid: str
    task_record_id: Optional[str]
    keyframe_id: str
    user_id: str
    conversation_id: str
    thread_id: str
    run_id: str
    shot_number: int
    old_version_id: str
    new_version_id: str
    old_version_number: int
    new_version_number: int
    old_image_url: str
    new_image_url: str
    model: str
    old_prompt: str
    new_prompt: Optional[str]
    user_action: str
    user_feedback: Optional[str]
    edit_instruction: Optional[str]
    success: bool
    error_msg: Optional[str]
    created_at: datetime


class CharacterEditRecordResult(msgspec.Struct, kw_only=True):
    """Character 编辑记录查询结果"""
    id: int
    uuid: str
    task_record_id: Optional[str]
    character_id: str
    user_id: str
    conversation_id: str
    thread_id: str
    run_id: str
    character_name: str
    old_version_id: str
    new_version_id: str
    old_version_number: int
    new_version_number: int
    old_image_url: str
    new_image_url: str
    model: str
    old_prompt: str
    new_prompt: Optional[str]
    user_action: str
    user_feedback: Optional[str]
    edit_instruction: Optional[str]
    success: bool
    error_msg: Optional[str]
    created_at: datetime


def _row_to_shot_edit_record(row: Optional[Dict]) -> Optional[ShotEditRecordResult]:
    """将数据库行转换为ShotEditRecordResult对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, ShotEditRecordResult)


def _row_to_storyboard_edit_record(row: Optional[Dict]) -> Optional[StoryboardEditRecordResult]:
    """将数据库行转换为StoryboardEditRecordResult对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, StoryboardEditRecordResult)


def _row_to_character_edit_record(row: Optional[Dict]) -> Optional[CharacterEditRecordResult]:
    """将数据库行转换为CharacterEditRecordResult对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, CharacterEditRecordResult)


# ==================== 任务记录 CRUD ====================

async def create_task_record(state: Dict[str, Any]) -> str:
    """
    创建任务记录（使用 asyncpg，无需 db）
    
    Args:
        state: VideoAgentState 字典
        
    Returns:
        任务记录 UUID
    """
    try:
        # 序列化 user_input_data
        user_input_data_dict = None
        user_input_text = ""
        if state.get("user_input_data"):
            user_input_data = state["user_input_data"]
            if hasattr(user_input_data, 'model_dump'):
                user_input_data_dict = user_input_data.model_dump()
                user_input_text = user_input_data.user_input
            elif isinstance(user_input_data, dict):
                user_input_data_dict = user_input_data
                user_input_text = user_input_data.get("user_input", "")
        
        # 序列化 generation_config
        generation_config_dict = None
        if state.get("generation_config"):
            generation_config = state["generation_config"]
            if hasattr(generation_config, 'model_dump'):
                generation_config_dict = generation_config.model_dump()
            elif isinstance(generation_config, dict):
                generation_config_dict = generation_config
        
        # 确保 run_id 是字符串（可能是 UUID 对象）
        run_id = str(state["run_id"]) if state.get("run_id") else None
        if not run_id:
            raise ValueError("run_id is required in state")
        
        # 处理 conversation_id 和 thread_id（可能是 None，但模型要求 str）
        conversation_id = str(state["conversation_id"]) if state.get("conversation_id") else ""
        thread_id = str(state["thread_id"]) if state.get("thread_id") else ""
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            task_record = await insert_and_return(
                conn,
                "video_task_records",
                uuid=str(uuid_lib.uuid4()),
                task_id=run_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                user_id=state["user_id"],
                detected_language=state.get("detected_language"),
                generation_config=json.dumps(generation_config_dict) if generation_config_dict else None,
                actual_target_duration_sec=float(x) if (x := state.get("actual_target_duration")) is not None else None,
                task_input=user_input_text or state.get("task_input", ""),
                user_input_data=json.dumps(user_input_data_dict) if user_input_data_dict else None,
                task_start_time=state.get("task_start_time", now_utc()),
                task_status=state.get("task_status", "processing"),
                analysis_uuid=state.get("analysis_uuid"),
                audio_transcription_uuids=state.get("audio_transcription_uuids"),
                story_outline_uuid=state.get("story_outline_uuid"),
                character_uuids=state.get("character_uuids"),
                scene_uuids=state.get("scene_uuids"),
                shot_uuids=state.get("shot_uuids"),
                keyframe_uuids=state.get("keyframe_uuids"),
                narration_uuids=state.get("narration_uuids"),
                audio_effect_uuids=state.get("audio_effect_uuids"),
                video_generation_uuids=state.get("video_generation_uuids"),
                music_generation_uuids=state.get("music_generation_uuids"),
                video_segments_uuids=state.get("video_segments_uuids"),
                video_assembly_uuid=state.get("video_assembly_uuid"),
                created_at=now_utc(),
                updated_at=now_utc()
            )
            
            logger.info(f"✅ 创建任务记录: uuid={task_record['uuid']}, run_id={run_id}")
            return task_record['uuid']
        
    except Exception as e:
        logger.error(f"❌ 创建任务记录失败: {e}")
        raise


async def update_task_record(run_id: str, updates: Dict[str, Any]) -> None:
    """
    更新任务记录（使用 asyncpg，无需 db）。先跑迁移再上线，调用方保证只传表内列。
    """
    updates = dict(updates)
    if "actual_target_duration" in updates:
        val = updates.pop("actual_target_duration")
        updates["actual_target_duration_sec"] = float(val) if val is not None else None
    updates["updated_at"] = now_utc()

    if not updates:
        return

    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        set_clauses = []
        params = []
        param_idx = 1
        for key, value in updates.items():
            set_clauses.append(f"{key} = ${param_idx}")
            params.append(json.dumps(value) if isinstance(value, (dict, list)) else value)
            param_idx += 1
        params.append(run_id)
        await execute(
            conn,
            f"UPDATE video_task_records SET {', '.join(set_clauses)} WHERE task_id = ${param_idx}",
            *params
        )
    logger.info(f"✅ 更新任务记录: run_id={run_id}, fields={list(updates.keys())}")


async def get_task_record_by_run_id(run_id: str) -> Optional[TaskRecordResult]:
    """根据 run_id 获取任务记录（使用 asyncpg，无需 db）- 返回 msgspec.Struct"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        row = await fetch_one(
            conn,
            "SELECT * FROM video_task_records WHERE task_id = $1",
            run_id
        )
        return _row_to_task_record(row)


async def get_task_record_by_uuid(uuid: str) -> Optional[TaskRecordResult]:
    """根据 UUID 获取任务记录（使用 asyncpg，无需 db）- 返回 msgspec.Struct"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        row = await fetch_one(
            conn,
            "SELECT * FROM video_task_records WHERE uuid = $1",
            uuid
        )
        return _row_to_task_record(row)


async def get_task_record_from_conversation_run(run_id: str) -> Optional[TaskRecordResult]:
    """
    当 video_task_records 无记录时，用 conversation_runs 的 run 构造虚拟 TaskRecordResult，
    使对话记录里 video 类型的 run 也能用与「视频任务」相同的详情接口与前端组件展示。
    仅当 run 存在且 agent_type 为 video 时返回，否则返回 None。
    """
    from .conversation import async_get_conversation_run_by_run_id
    run = await async_get_conversation_run_by_run_id(run_id)
    if not run:
        return None
    at = (getattr(run, "agent_type", None) or "").strip().lower()
    if at != "video":
        return None
    user_input_data = None
    if getattr(run, "user_input", None) or getattr(run, "user_input_files", None):
        try:
            uif = getattr(run, "user_input_files", None)
            if isinstance(uif, str) and uif:
                import json as _json
                uif = _json.loads(uif)
            user_input_data = {
                "user_input": getattr(run, "user_input", None) or "",
                "user_input_files": uif,
            }
        except Exception:
            user_input_data = {"user_input": getattr(run, "user_input", None) or ""}
    return TaskRecordResult(
        id=0,
        uuid=getattr(run, "uuid", None) or "",
        task_id=run_id,
        conversation_id=run.conversation_id,
        thread_id=run.thread_id,
        user_id=run.user_id,
        detected_language=None,
        generation_config=None,
        actual_target_duration=None,
        task_input=getattr(run, "user_input", None) or "",
        user_input_data=user_input_data,
        task_start_time=run.created_at,
        task_finish_time=getattr(run, "completed_at", None),
        task_status=run.status,
        analysis_uuid=None,
        audio_transcription_uuids=None,
        story_outline_uuid=None,
        character_uuids=None,
        scene_uuids=None,
        shot_uuids=None,
        keyframe_uuids=None,
        narration_uuids=None,
        audio_effect_uuids=None,
        video_generation_uuids=None,
        music_generation_uuids=None,
        video_segments_uuids=None,
        video_assembly_uuid=None,
        final_video_url=None,
        billing_status=getattr(run, "billing_status", None),
        langsmith_cost=getattr(run, "langsmith_cost", None),
        cost=getattr(run, "cost", None),
        cost_calculated=getattr(run, "cost_calculated", None),
        credits_deducted=getattr(run, "credits_deducted", None),
        credits_amount=getattr(run, "credits_amount", None),
        tool_consistency_summary=None,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


# ==================== tool_consistency_summary 汇总 ====================


def _ensure_metrics_dict(val: Any, label: str = "") -> Optional[Dict[str, Any]]:
    """DB 读出的 JSONB 可能是 dict 或 str，统一为 dict 便于聚合。"""
    if val is None:
        return None
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            logger.warning("tool_consistency_summary: %s metrics 非合法 JSON，忽略: %s", label, val[:200] if len(val) > 200 else val)
            return None
    return None


def _actual_attempts_from_metrics(m: Dict[str, Any], details_key: str = "consistency_details") -> int:
    """从单条 metrics 得到实际尝试次数（含重试）。优先 total_attempts，否则用 details 条数。"""
    raw = m.get("total_attempts")
    if raw is not None:
        try:
            n = int(raw)
            if n >= 0:
                return n
        except (TypeError, ValueError):
            pass
    details = m.get(details_key) or []
    return max(1, len(details)) if details else 1


def _aggregate_video_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从 video_generation_versions 行列表聚合 video_tool_metrics 得到 summary 子块。"""
    total = len(rows)
    pass_count = 0
    best_effort_count = 0
    first_frame_violation_count = 0
    total_video_attempts = 0
    for r in rows:
        m = _ensure_metrics_dict(r.get("video_tool_metrics"), "video")
        if not m:
            pass_count += 1
            total_video_attempts += 1
            continue
        total_video_attempts += _actual_attempts_from_metrics(m)
        if m.get("best_effort_selected"):
            best_effort_count += 1
        checks = m.get("consistency_checks", 0)
        if checks == 0 or m.get("consistency_pass", 0) >= 1:
            pass_count += 1
        for d in m.get("consistency_details") or []:
            if isinstance(d, dict) and d.get("first_frame_violation"):
                first_frame_violation_count += 1
    return {
        "total_calls": total,
        "pass_count": pass_count,
        "fail_count": total - pass_count,
        "best_effort_count": best_effort_count,
        "first_frame_violation_count": first_frame_violation_count,
        "total_video_attempts": total_video_attempts,
    }


def _aggregate_image_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从 version 行列表聚合 image_tool_metrics 得到 summary 子块。"""
    total = len(rows)
    pass_count = 0
    best_effort_count = 0
    face_fail = 0
    accessories_fail = 0
    clothing_fail = 0
    body_fail = 0
    hair_fail = 0
    artifact_fail = 0
    total_image_generations = 0
    for r in rows:
        m = _ensure_metrics_dict(r.get("image_tool_metrics"), "image")
        if not m:
            pass_count += 1  # 无 metrics 视为通过（老数据或未跑 wrapper）
            total_image_generations += 1
            continue
        total_image_generations += _actual_attempts_from_metrics(m)
        if m.get("best_effort_selected"):
            best_effort_count += 1
        checks = m.get("consistency_checks", 0)
        if checks == 0 or m.get("consistency_pass", 0) >= 1:
            pass_count += 1
        for d in m.get("consistency_details") or []:
            if not isinstance(d, dict):
                continue
            if not d.get("passed"):
                if d.get("face_level") and d.get("face_level") not in ("identical", "very_similar", "n_a"):
                    face_fail += 1
                if d.get("accessories_level") and d.get("accessories_level") not in ("identical", "very_similar", "n_a"):
                    accessories_fail += 1
                if d.get("clothing_level") and d.get("clothing_level") not in ("identical", "very_similar", "n_a"):
                    clothing_fail += 1
                if d.get("body_level") and d.get("body_level") not in ("identical", "very_similar", "n_a"):
                    body_fail += 1
                if d.get("hair_level") and d.get("hair_level") not in ("identical", "very_similar", "n_a"):
                    hair_fail += 1
                sev = d.get("severe_abnormality")
                if sev and sev not in _IMAGE_ARTIFACT_PASS_VALUES:
                    artifact_fail += 1
                elif d.get("artifact") and d.get("artifact") not in _IMAGE_ARTIFACT_PASS_VALUES:
                    artifact_fail += 1
                elif d.get("artifact_level") and d.get("artifact_level") not in ("identical", "very_similar", "n_a"):
                    artifact_fail += 1
    return {
        "total_calls": total,
        "pass_count": pass_count,
        "fail_count": total - pass_count,
        "best_effort_count": best_effort_count,
        "face_fail_count": face_fail,
        "accessories_fail_count": accessories_fail,
        "clothing_fail_count": clothing_fail,
        "body_fail_count": body_fail,
        "hair_fail_count": hair_fail,
        "artifact_fail_count": artifact_fail,
        "total_image_generations": total_image_generations,
    }


async def build_tool_consistency_summary(run_id: str) -> Optional[Dict[str, Any]]:
    """根据 run_id 从 character / keyframe version 表聚合 tool metrics，得到 task record 的 tool_consistency_summary；并包含反思统计。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        char_rows = await fetch_all(
            conn,
            "SELECT image_tool_metrics FROM video_character_generation_versions WHERE run_id = $1",
            run_id,
        )
        kf_rows = await fetch_all(
            conn,
            "SELECT image_tool_metrics FROM video_keyframe_versions WHERE run_id = $1",
            run_id,
        )
        video_rows = await fetch_all(
            conn,
            "SELECT video_tool_metrics FROM video_generation_versions WHERE run_id = $1",
            run_id,
        )
        # 反思：该 run 下反思次数与重生关键帧数（video_keyframe_reflections 表有 run_id）
        reflection_row = await fetch_one(
            conn,
            """SELECT COUNT(*) AS reflection_count, COALESCE(SUM(regenerated_keyframes), 0)::int AS regenerated_count
               FROM video_keyframe_reflections WHERE run_id = $1""",
            run_id,
        )
    char_rows = [dict(r) for r in (char_rows or [])]
    kf_rows = [dict(r) for r in (kf_rows or [])]
    video_rows = [dict(r) for r in (video_rows or [])]
    reflection_count = int(reflection_row["reflection_count"]) if reflection_row else 0
    regenerated_count = int(reflection_row["regenerated_count"]) if reflection_row else 0

    # 调试日志：每类 version 行数、首条 metrics 类型与 actual 取值，便于核对「实际次数」是否来自 total_attempts/details
    def _log_metrics_sample(rows: List[Dict], metrics_key: str, kind: str) -> None:
        if not rows:
            logger.info("tool_consistency_summary run_id=%s %s: 0 rows", run_id, kind)
            return
        first = rows[0].get(metrics_key)
        m = _ensure_metrics_dict(first, kind)
        if m:
            ta = m.get("total_attempts")
            details = m.get("consistency_details") or []
            actual = _actual_attempts_from_metrics(m, "consistency_details")
            logger.info(
                "tool_consistency_summary run_id=%s %s: rows=%s, first row metrics type=%s, total_attempts=%s, len(details)=%s => actual=%s",
                run_id, kind, len(rows), type(first).__name__, ta, len(details), actual,
            )
        else:
            logger.info(
                "tool_consistency_summary run_id=%s %s: rows=%s, first row metrics raw type=%s (not dict/parseable)",
                run_id, kind, len(rows), type(first).__name__,
            )
    _log_metrics_sample(char_rows, "image_tool_metrics", "character")
    _log_metrics_sample(kf_rows, "image_tool_metrics", "keyframe")
    _log_metrics_sample(video_rows, "video_tool_metrics", "video")

    char_sum = _aggregate_image_metrics(char_rows)
    kf_sum = _aggregate_image_metrics(kf_rows)
    video_sum = _aggregate_video_metrics(video_rows)
    logger.info(
        "tool_consistency_summary run_id=%s result: character total_calls=%s total_image_generations=%s, keyframe total_calls=%s total_image_generations=%s, video total_calls=%s total_video_attempts=%s",
        run_id,
        char_sum.get("total_calls"), char_sum.get("total_image_generations"),
        kf_sum.get("total_calls"), kf_sum.get("total_image_generations"),
        video_sum.get("total_calls"), video_sum.get("total_video_attempts"),
    )
    summary_lines = [
        _character_summary_line(char_sum),
        _keyframe_summary_line(kf_sum),
        _video_summary_line(video_sum),
        f"反思 {reflection_count} 次 重生 {regenerated_count}",
    ]
    return {
        "character": char_sum,
        "keyframe": kf_sum,
        "video": video_sum,
        "reflection": {
            "reflection_count": reflection_count,
            "regenerated_count": regenerated_count,
        },
        "summary_lines": summary_lines,
    }


async def build_and_update_tool_consistency_summary(run_id: str) -> Optional[Dict[str, Any]]:
    """聚合一致性汇总并写回 task record。任务结束与「重新统计」接口共用此方法，保证结果一致。"""
    if not run_id:
        logger.warning("build_and_update_tool_consistency_summary: run_id 为空，跳过")
        return None
    summary = await build_tool_consistency_summary(run_id)
    if summary is not None:
        await update_task_record(run_id, {"tool_consistency_summary": summary})
    return summary


def _character_summary_line(char_sum: Dict[str, Any]) -> str:
    """角色汇总一行：通过数 + 实际生图次数（始终展示实际次数，便于与预期对比）。"""
    total = char_sum["total_calls"]
    pass_count = char_sum["pass_count"]
    actual = char_sum.get("total_image_generations", total)
    if actual > total:
        return f"角色 {pass_count}/{total} 通过；实际生图 {actual} 次（预期 {total}，多 {actual - total} 次）"
    return f"角色 {pass_count}/{total} 通过（实际 {actual} 次）"


def _keyframe_summary_line(kf_sum: Dict[str, Any]) -> str:
    """关键帧汇总一行：通过数 + 实际生图次数（始终展示实际次数）。"""
    total = kf_sum["total_calls"]
    pass_count = kf_sum["pass_count"]
    actual = kf_sum.get("total_image_generations", total)
    if actual > total:
        return f"关键帧 {pass_count}/{total} 通过；实际生图 {actual} 次（预期 {total}，多 {actual - total} 次）"
    return f"关键帧 {pass_count}/{total} 通过（实际 {actual} 次）"


def _video_summary_line(video_sum: Dict[str, Any]) -> str:
    """视频汇总一行：通过数 + 实际尝试次数（始终展示实际次数）。"""
    total = video_sum["total_calls"]
    pass_count = video_sum["pass_count"]
    actual = video_sum.get("total_video_attempts", total)
    if actual > total:
        return f"视频 {pass_count}/{total} 通过；实际尝试 {actual} 次（预期 {total}，多 {actual - total} 次）"
    return f"视频 {pass_count}/{total} 通过（实际 {actual} 次）"


def build_image_consistency_display_lines(metrics: Optional[Dict[str, Any]]) -> List[str]:
    """从 image_tool_metrics 生成多维度一致性展示行（脸/配饰/服装等级 + 原因），供详情页逐行展示。"""
    if not metrics or not isinstance(metrics, dict):
        return []
    lines = []
    for i, d in enumerate(metrics.get("consistency_details") or []):
        if not isinstance(d, dict):
            continue
        model = d.get("model", "")
        passed = d.get("passed", False)
        status = "通过" if passed else "未通过"
        lines.append(f"尝试 {i + 1}: {model} — {status}")
        image_url = d.get("image_url")
        if image_url:
            lines.append(f"  当次图片: {image_url}")
        failure_reason = d.get("failure_reason")
        if failure_reason:
            lines.append(f"  失败原因: {failure_reason}")
        has_char = d.get("has_character")
        if has_char is not None:
            lines.append(f"  需检角色: {'是' if has_char else '否'}")
        per_char = d.get("per_character")
        if per_char and isinstance(per_char, list):
            for pc in per_char:
                if not isinstance(pc, dict):
                    continue
                name = pc.get("name") or "未命名角色"
                lines.append(f"  角色: {name}")
                face = pc.get("face_level") or "—"
                acc = pc.get("accessories_level") or "—"
                cloth = pc.get("clothing_level") or "—"
                body = pc.get("body_level") or "—"
                hair = pc.get("hair_level") or "—"
                style = pc.get("style_level") or "—"
                art = pc.get("artifact") or "—"
                lines.append(f"    脸: {face}  配饰: {acc}  服装: {cloth}  体型: {body}  发型: {hair}  风格: {style}  变形: {art}")
                lines.append(f"    说明: {pc.get('reason') or '—'}")
                lines.append(f"    通过: {'是' if pc.get('passed') else '否'}")
        _whole = d.get("severe_abnormality") or d.get("artifact")
        lines.append(f"  整图画面: {_whole or '—'}")
        _sar = d.get("severe_abnormality_reason")
        if _sar:
            lines.append(f"  整图说明: {_sar}")
        lines.append(f"  综合说明: {d.get('reason') or '—'}")
        lines.append(f"  建议 prompt: {d.get('suggested_prompt') or '—'}")
    return lines


def build_video_consistency_display_lines(metrics: Optional[Dict[str, Any]]) -> List[str]:
    """从 video_tool_metrics 生成多维度一致性展示行（首帧聚合 + 镜头/风格/明显异常 + 原因 + 汇总 + 当次视频 URL），供详情页逐行展示。"""
    if not metrics or not isinstance(metrics, dict):
        return []
    lines = []
    for i, d in enumerate(metrics.get("consistency_details") or []):
        if not isinstance(d, dict):
            continue
        model = d.get("model", "")
        passed = d.get("passed", False)
        status = "通过" if passed else "未通过"
        lines.append(f"尝试 {i + 1}: {model} — {status}")
        video_url = d.get("video_url")
        if video_url:
            lines.append(f"  当次视频: {video_url}")
        failure_reason = d.get("failure_reason")
        if failure_reason:
            lines.append(f"  失败原因: {failure_reason}")
        ffc = d.get("first_frame_consistency") or "—"
        cam = d.get("camera_movement") or "—"
        style = d.get("style_consistency") or "—"
        abn = d.get("severe_abnormality") or d.get("artifact") or "—"
        lines.append(f"  首帧: {ffc}  镜头: {cam}  风格: {style}  明显异常: {abn}")
        per_char_ff = d.get("per_character_first_frame")
        if per_char_ff and isinstance(per_char_ff, list):
            for pc in per_char_ff:
                if not isinstance(pc, dict):
                    continue
                name = pc.get("name") or "未命名角色"
                lines.append(f"  角色: {name}")
                face_f = pc.get("face_consistency")
                acc_f = pc.get("accessories_consistency")
                cloth_f = pc.get("clothing_consistency")
                body_f = pc.get("body_consistency")
                hair_f = pc.get("hair_consistency")
                frame_f = pc.get("framing_consistency")
                new_f = pc.get("no_new_primary_subjects")
                lines.append(f"    首帧子项: 脸={face_f or '—'} 配饰={acc_f or '—'} 服装={cloth_f or '—'} 体型={body_f or '—'} 发型={hair_f or '—'} 景别={frame_f or '—'} 新主体={new_f or '—'}")
                if pc.get("face_consistency_reason") or pc.get("clothing_consistency_reason"):
                    lines.append(f"    说明: {pc.get('face_consistency_reason') or ''} {pc.get('clothing_consistency_reason') or ''}".strip() or "—")
        if d.get("camera_movement_reason"):
            lines.append(f"  镜头说明: {d['camera_movement_reason']}")
        if d.get("style_consistency_reason"):
            lines.append(f"  风格说明: {d['style_consistency_reason']}")
        abn_reason = d.get("severe_abnormality_reason") or d.get("artifact_reason")
        if abn_reason:
            lines.append(f"  明显异常说明: {abn_reason}")
        if d.get("action_reason"):
            lines.append(f"  动作说明: {d['action_reason']}")
        # 始终输出汇总行，无内容时显示 —，便于前端统一展示 reason
        lines.append(f"  汇总: {d.get('reason_overall') or '—'}")
        if d.get("suggested_prompt"):
            lines.append(f"  建议 prompt: {d['suggested_prompt']}")
    return lines


# ==================== 计费（billing 在 task_record）====================
# 读：get_task_record_by_run_id -> _row_to_task_record（row_to_struct_safe），只传 struct 键，DB 多列不崩。
# 写：update_task_record 由调用方保证只传表内列，先跑迁移再上线。

# ==================== Shot 编辑记录 CRUD ====================

async def create_shot_edit_record(
    task_record_id: Optional[str],
    video_generation_id: str,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    shot_number: int,
    old_version_id: str,
    new_version_id: str,
    old_version_number: int,
    new_version_number: int,
    old_video_url: str,
    new_video_url: str,
    duration: Optional[float],
    model: str,
    old_prompt: str,
    new_prompt: Optional[str],
    user_action: str,
    user_feedback: Optional[str],
    edit_instruction: Optional[str],
    success: bool,
    error_msg: Optional[str],
    cascaded_from_storyboard_edit_id: Optional[str] = None
) -> str:
    """创建 Shot 编辑记录（使用 asyncpg，无需 db）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            edit_record = await insert_and_return(
                conn,
                "video_shot_edit_records",
                uuid=str(uuid_lib.uuid4()),
                task_record_id=task_record_id,
                video_generation_id=video_generation_id,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                shot_number=shot_number,
                old_version_id=old_version_id,
                new_version_id=new_version_id,
                old_version_number=old_version_number,
                new_version_number=new_version_number,
                old_video_url=old_video_url,
                new_video_url=new_video_url,
                duration=duration,
                model=model,
                old_prompt=old_prompt,
                new_prompt=new_prompt,
                user_action=user_action,
                user_feedback=user_feedback,
                edit_instruction=edit_instruction,
                success=success,
                error_msg=error_msg,
                cascaded_from_storyboard_edit_id=cascaded_from_storyboard_edit_id,
                created_at=now_utc()
            )
            
            logger.info(f"✅ 创建 Shot 编辑记录: shot={shot_number}, action={user_action}")
            return edit_record['uuid']
        
    except Exception as e:
        logger.error(f"❌ 创建 Shot 编辑记录失败: {e}")
        raise


# ==================== Storyboard 编辑记录 CRUD ====================

async def create_storyboard_edit_record(
    task_record_id: Optional[str],
    keyframe_id: str,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    shot_number: int,
    old_version_id: str,
    new_version_id: str,
    old_version_number: int,
    new_version_number: int,
    old_image_url: str,
    new_image_url: str,
    model: str,
    old_prompt: str,
    new_prompt: Optional[str],
    user_action: str,
    user_feedback: Optional[str],
    edit_instruction: Optional[str],
    success: bool,
    error_msg: Optional[str]
) -> str:
    """创建 Storyboard 编辑记录（使用 asyncpg，无需 db）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            edit_record = await insert_and_return(
                conn,
                "video_storyboard_edit_records",
                uuid=str(uuid_lib.uuid4()),
                task_record_id=task_record_id,
                keyframe_id=keyframe_id,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                shot_number=shot_number,
                old_version_id=old_version_id,
                new_version_id=new_version_id,
                old_version_number=old_version_number,
                new_version_number=new_version_number,
                old_image_url=old_image_url,
                new_image_url=new_image_url,
                model=model,
                old_prompt=old_prompt,
                new_prompt=new_prompt,
                user_action=user_action,
                user_feedback=user_feedback,
                edit_instruction=edit_instruction,
                success=success,
                error_msg=error_msg,
                created_at=now_utc()
            )
            
            logger.info(f"✅ 创建 Storyboard 编辑记录: shot={shot_number}, action={user_action}")
            return edit_record['uuid']
        
    except Exception as e:
        logger.error(f"❌ 创建 Storyboard 编辑记录失败: {e}")
        raise


# ==================== Character 编辑记录 CRUD ====================

async def create_character_edit_record(
    task_record_id: Optional[str],
    character_id: str,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    character_name: str,
    old_version_id: str,
    new_version_id: str,
    old_version_number: int,
    new_version_number: int,
    old_image_url: str,
    new_image_url: str,
    model: str,
    old_prompt: str,
    new_prompt: Optional[str],
    user_action: str,
    user_feedback: Optional[str],
    edit_instruction: Optional[str],
    success: bool,
    error_msg: Optional[str]
) -> str:
    """创建 Character 编辑记录（使用 asyncpg，无需 db）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            edit_record = await insert_and_return(
                conn,
                "video_character_edit_records",
                uuid=str(uuid_lib.uuid4()),
                task_record_id=task_record_id,
                character_id=character_id,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                character_name=character_name,
                old_version_id=old_version_id,
                new_version_id=new_version_id,
                old_version_number=old_version_number,
                new_version_number=new_version_number,
                old_image_url=old_image_url,
                new_image_url=new_image_url,
                model=model,
                old_prompt=old_prompt,
                new_prompt=new_prompt,
                user_action=user_action,
                user_feedback=user_feedback,
                edit_instruction=edit_instruction,
                success=success,
                error_msg=error_msg,
                created_at=now_utc()
            )
            
            logger.info(f"✅ 创建 Character 编辑记录: character={character_name}, action={user_action}")
            return edit_record['uuid']
        
    except Exception as e:
        logger.error(f"❌ 创建 Character 编辑记录失败: {e}")
        raise


# ==================== 查询相关 CRUD ====================

async def get_task_records_paginated(
    page: int = 1,
    page_size: int = 20,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    task_id: Optional[str] = None,
    user_input: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    email: Optional[str] = None,
) -> tuple[List[TaskRecordResult], int]:
    """分页获取任务记录列表（asyncpg，读路径 _row_to_task_record 防御 DB 多列）。user_input 为模糊匹配 task_input；task_id 为 run_id 精确或模糊；email 为 users.email 模糊匹配（ILIKE），与 user_id 为 AND 关系。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        where_clauses = []
        params = []
        param_idx = 1

        if user_id:
            where_clauses.append(f"user_id = ${param_idx}")
            params.append(user_id)
            param_idx += 1
        if email and email.strip():
            where_clauses.append(f"user_id IN (SELECT user_id FROM users WHERE email ILIKE ${param_idx})")
            params.append(f"%{email.strip()}%")
            param_idx += 1
        if thread_id:
            where_clauses.append(f"thread_id = ${param_idx}")
            params.append(thread_id)
            param_idx += 1
        if task_id:
            task_id_trim = (task_id or "").strip()
            if task_id_trim:
                where_clauses.append(f"(task_id = ${param_idx} OR task_id ILIKE ${param_idx + 1})")
                params.append(task_id_trim)
                params.append(f"%{task_id_trim}%")
                param_idx += 2
        if user_input:
            where_clauses.append(f"task_input ILIKE ${param_idx}")
            params.append(f"%{user_input}%")
            param_idx += 1
        if status:
            where_clauses.append(f"task_status = ${param_idx}")
            params.append(status)
            param_idx += 1
        if start_date:
            where_clauses.append(f"task_start_time >= ${param_idx}")
            params.append(start_date)
            param_idx += 1
        if end_date:
            where_clauses.append(f"task_start_time <= ${param_idx}")
            params.append(end_date)
            param_idx += 1

        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

        total = await fetch_val(
            conn,
            f"SELECT COUNT(*) FROM video_task_records WHERE {where_clause}",
            *params
        )

        offset = (page - 1) * page_size
        rows = await fetch_all(
            conn,
            f"""
            SELECT * FROM video_task_records
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, page_size, offset
        )

        records = [_row_to_task_record(row) for row in rows if row]
        return [r for r in records if r], total


async def get_task_records_grouped_by_thread(
    page: int = 1,
    page_size: int = 20,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    task_id: Optional[str] = None,
    user_input: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    email: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], int]:
    """按 thread_id 分组分页返回任务记录：每行一个 thread，含 run_ids 列表与 run 数量。筛选条件同 get_task_records_paginated（含 email 模糊匹配 users.email）。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        where_clauses = []
        params: List[Any] = []
        param_idx = 1

        if user_id:
            where_clauses.append(f"user_id = ${param_idx}")
            params.append(user_id)
            param_idx += 1
        if email and email.strip():
            where_clauses.append(f"user_id IN (SELECT user_id FROM users WHERE email ILIKE ${param_idx})")
            params.append(f"%{email.strip()}%")
            param_idx += 1
        if thread_id:
            where_clauses.append(f"thread_id = ${param_idx}")
            params.append(thread_id)
            param_idx += 1
        if task_id:
            task_id_trim = (task_id or "").strip()
            if task_id_trim:
                where_clauses.append(f"(task_id = ${param_idx} OR task_id ILIKE ${param_idx + 1})")
                params.append(task_id_trim)
                params.append(f"%{task_id_trim}%")
                param_idx += 2
        if user_input:
            where_clauses.append(f"task_input ILIKE ${param_idx}")
            params.append(f"%{user_input}%")
            param_idx += 1
        if status:
            where_clauses.append(f"task_status = ${param_idx}")
            params.append(status)
            param_idx += 1
        if start_date:
            where_clauses.append(f"task_start_time >= ${param_idx}")
            params.append(start_date)
            param_idx += 1
        if end_date:
            where_clauses.append(f"task_start_time <= ${param_idx}")
            params.append(end_date)
            param_idx += 1

        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

        total = await fetch_val(
            conn,
            f"SELECT COUNT(DISTINCT thread_id) FROM video_task_records WHERE {where_clause}",
            *params
        )

        offset = (page - 1) * page_size
        rows = await fetch_all(
            conn,
            f"""
            SELECT thread_id,
                   MAX(user_id) AS user_id,
                   array_agg(task_id ORDER BY created_at DESC) AS run_ids,
                   COUNT(*) AS run_count,
                   MAX(created_at) AS latest_created_at
            FROM video_task_records
            WHERE {where_clause}
            GROUP BY thread_id
            ORDER BY MAX(created_at) DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, page_size, offset
        )

        result = []
        for row in (rows or []):
            r = dict(row)
            run_ids = r.get("run_ids")
            if hasattr(run_ids, "__iter__") and not isinstance(run_ids, (str, bytes)):
                r["run_ids"] = list(run_ids)
            else:
                r["run_ids"] = []
            result.append(r)
        return result, total


async def get_emails_by_user_ids(user_ids: List[str]) -> Dict[str, str]:
    """批量查询 user_id -> email 映射。空输入或无匹配返回 {}。仅返回非空 email。"""
    cleaned = [u for u in (user_ids or []) if u]
    if not cleaned:
        return {}
    unique_ids = list(dict.fromkeys(cleaned))
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        rows = await fetch_all(
            conn,
            "SELECT user_id, email FROM users WHERE user_id = ANY($1::text[])",
            unique_ids,
        )
    return {r["user_id"]: r["email"] for r in (rows or []) if r.get("user_id") and r.get("email")}


async def get_shot_edit_records(
    task_id: Optional[str] = None,
    user_action: Optional[str] = None,
    success: Optional[bool] = None,
    page: int = 1,
    page_size: int = 20
) -> tuple[List[ShotEditRecordResult], int]:
    """分页获取 Shot 编辑记录（使用 asyncpg，无需 db）- 返回 msgspec.Struct 列表"""
    
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        where_clauses = []
        params = []
        param_idx = 1
        
        if task_id:
            # 先查找 task_record
            task_record = await get_task_record_by_run_id(task_id)
            if task_record:
                where_clauses.append(f"task_record_id = ${param_idx}")
                params.append(task_record.uuid)
                param_idx += 1
        if user_action:
            where_clauses.append(f"user_action = ${param_idx}")
            params.append(user_action)
            param_idx += 1
        if success is not None:
            where_clauses.append(f"success = ${param_idx}")
            params.append(success)
            param_idx += 1
        
        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        # 查询总数
        total = await fetch_val(
            conn,
            f"SELECT COUNT(*) FROM video_shot_edit_records WHERE {where_clause}",
            *params
        )
        
        # 查询数据
        offset = (page - 1) * page_size
        rows = await fetch_all(
            conn,
            f"""
            SELECT * FROM video_shot_edit_records
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, page_size, offset
        )
        
        records = [_row_to_shot_edit_record(row) for row in rows if row]
        return [r for r in records if r], total


async def get_storyboard_edit_records(
    task_id: Optional[str] = None,
    user_action: Optional[str] = None,
    success: Optional[bool] = None,
    page: int = 1,
    page_size: int = 20
) -> tuple[List[StoryboardEditRecordResult], int]:
    """分页获取 Storyboard 编辑记录（使用 asyncpg，无需 db）- 返回 msgspec.Struct 列表"""
    
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        where_clauses = []
        params = []
        param_idx = 1
        
        if task_id:
            task_record = await get_task_record_by_run_id(task_id)
            if task_record:
                where_clauses.append(f"task_record_id = ${param_idx}")
                params.append(task_record.uuid)
                param_idx += 1
        if user_action:
            where_clauses.append(f"user_action = ${param_idx}")
            params.append(user_action)
            param_idx += 1
        if success is not None:
            where_clauses.append(f"success = ${param_idx}")
            params.append(success)
            param_idx += 1
        
        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        total = await fetch_val(
            conn,
            f"SELECT COUNT(*) FROM video_storyboard_edit_records WHERE {where_clause}",
            *params
        )
        
        offset = (page - 1) * page_size
        rows = await fetch_all(
            conn,
            f"""
            SELECT * FROM video_storyboard_edit_records
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, page_size, offset
        )
        
        records = [_row_to_storyboard_edit_record(row) for row in rows if row]
        return [r for r in records if r], total


async def get_character_edit_records(
    task_id: Optional[str] = None,
    user_action: Optional[str] = None,
    success: Optional[bool] = None,
    page: int = 1,
    page_size: int = 20
) -> tuple[List[CharacterEditRecordResult], int]:
    """分页获取 Character 编辑记录（使用 asyncpg，无需 db）- 返回 msgspec.Struct 列表"""
    
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        where_clauses = []
        params = []
        param_idx = 1
        
        if task_id:
            task_record = await get_task_record_by_run_id(task_id)
            if task_record:
                where_clauses.append(f"task_record_id = ${param_idx}")
                params.append(task_record.uuid)
                param_idx += 1
        if user_action:
            where_clauses.append(f"user_action = ${param_idx}")
            params.append(user_action)
            param_idx += 1
        if success is not None:
            where_clauses.append(f"success = ${param_idx}")
            params.append(success)
            param_idx += 1
        
        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        total = await fetch_val(
            conn,
            f"SELECT COUNT(*) FROM video_character_edit_records WHERE {where_clause}",
            *params
        )
        
        offset = (page - 1) * page_size
        rows = await fetch_all(
            conn,
            f"""
            SELECT * FROM video_character_edit_records
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, page_size, offset
        )
        
        records = [_row_to_character_edit_record(row) for row in rows if row]
        return [r for r in records if r], total


# ==================== 统计 CRUD ====================

async def get_statistics(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    获取错误追踪统计（单次查询返回所有计数，避免 N+1）
    
    Returns:
        Dict: total_tasks, completed_tasks, failed_tasks, processing_tasks,
              total_shot_edits, total_storyboard_edits, total_character_edits
    """
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        # 单条 SQL 用标量子查询一次取齐所有计数，一次 round-trip
        row = await fetch_one(
            conn,
            """
            SELECT
                (SELECT COUNT(*) FROM video_task_records
                 WHERE ($1::timestamptz IS NULL OR task_start_time >= $1)
                   AND ($2::timestamptz IS NULL OR task_start_time <= $2)) AS total_tasks,
                (SELECT COUNT(*) FROM video_task_records
                 WHERE task_status = 'completed'
                   AND ($1::timestamptz IS NULL OR task_start_time >= $1)
                   AND ($2::timestamptz IS NULL OR task_start_time <= $2)) AS completed_tasks,
                (SELECT COUNT(*) FROM video_task_records
                 WHERE task_status = 'failed'
                   AND ($1::timestamptz IS NULL OR task_start_time >= $1)
                   AND ($2::timestamptz IS NULL OR task_start_time <= $2)) AS failed_tasks,
                (SELECT COUNT(*) FROM video_task_records
                 WHERE task_status = 'processing'
                   AND ($1::timestamptz IS NULL OR task_start_time >= $1)
                   AND ($2::timestamptz IS NULL OR task_start_time <= $2)) AS processing_tasks,
                (SELECT COUNT(*) FROM video_shot_edit_records
                 WHERE ($1::timestamptz IS NULL OR created_at >= $1)
                   AND ($2::timestamptz IS NULL OR created_at <= $2)) AS total_shot_edits,
                (SELECT COUNT(*) FROM video_storyboard_edit_records
                 WHERE ($1::timestamptz IS NULL OR created_at >= $1)
                   AND ($2::timestamptz IS NULL OR created_at <= $2)) AS total_storyboard_edits,
                (SELECT COUNT(*) FROM video_character_edit_records
                 WHERE ($1::timestamptz IS NULL OR created_at >= $1)
                   AND ($2::timestamptz IS NULL OR created_at <= $2)) AS total_character_edits
            """,
            start_date,
            end_date
        )
    if not row:
        return {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "processing_tasks": 0,
            "total_shot_edits": 0,
            "total_storyboard_edits": 0,
            "total_character_edits": 0,
        }
    return dict(row)
