"""
视频关键帧CRUD操作 - 使用msgspec返回类型

包含：
- 关键帧主记录 (video_keyframes)
- 关键帧版本 (video_keyframe_versions)
- 关键帧反思 (video_keyframe_reflections)
"""

import logging
from typing import List, Optional, Dict, Any

from ...models.database import get_asyncpg_pool
from ...utils.asyncpg_utils import (
    fetch_one, fetch_all, fetch_val, execute,
    insert_and_return, generate_uuid, now_utc, to_json, from_json
)
from ...schemas.video.video_keyframe import (
    VideoKeyframeDB,
    VideoKeyframeVersionDB,
)

logger = logging.getLogger(__name__)


# ==================== Helper Functions ====================

from .row_normalize import row_to_struct_safe

# JSON/JSONB 列：DB 可能返回字符串，在此统一规范为 list/dict
_KEYFRAME_LIST_FIELDS = ("reference_image_urls", "character_ids")
_KEYFRAME_DICT_FIELDS = ("additional_data",)
_KEYFRAME_VERSION_LIST_FIELDS = ("reference_image_urls", "character_version_ids", "audio_segment_ids")
_KEYFRAME_VERSION_DICT_FIELDS = ("additional_data", "image_tool_metrics")


def _row_to_keyframe(row: Optional[Dict]) -> Optional[VideoKeyframeDB]:
    """将数据库行转换为VideoKeyframeDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoKeyframeDB,
        list_fields=_KEYFRAME_LIST_FIELDS, dict_fields=_KEYFRAME_DICT_FIELDS
    )


def _row_to_keyframe_version(row: Optional[Dict]) -> Optional[VideoKeyframeVersionDB]:
    """将数据库行转换为VideoKeyframeVersionDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoKeyframeVersionDB,
        list_fields=_KEYFRAME_VERSION_LIST_FIELDS, dict_fields=_KEYFRAME_VERSION_DICT_FIELDS
    )


# ==================== 关键帧主记录 CRUD ====================

async def create_keyframe(
    shot_number: int,
    is_bridge: bool,
    reference_image_urls: List[str],
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    story_outline_id: str,
    scene_id: str,
    storyboard_detail_id: str,
    detailed_shot_id: str,
    character_ids: Optional[List[str]] = None,
    frame_index: int = 0
) -> str:
    """创建关键帧记录
    
    Returns:
        str: 新创建的keyframe UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            result = await insert_and_return(
                conn,
                "video_keyframes",
                uuid=uuid,
                shot_number=shot_number,
                is_bridge=is_bridge,
                reference_image_urls=reference_image_urls,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                story_outline_id=story_outline_id,
                scene_id=scene_id,
                storyboard_detail_id=storyboard_detail_id,
                detailed_shot_id=detailed_shot_id,
                character_ids=character_ids,
                frame_index=frame_index,
                current_version_index=0,
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建关键帧失败: {e}")
        raise


async def create_keyframe_version(
    keyframe_id: str,
    version_number: int,
    shot_number: int,
    keyframe_url: str,
    t2i_prompt: str,
    provider: str,
    is_bridge: bool,
    reference_image_urls: List[str],
    success: bool,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    error_msg: str = None,
    raw_error_msg: str = None,
    audio_segment_ids: List[str] = None,
    ai_messages: Optional[str] = None,
    character_version_ids: Optional[List[str]] = None,
    additional_data: Optional[Dict[str, Any]] = None,
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    seed: Optional[int] = None,
    model: Optional[str] = None,
    image_generation_tool: Optional[str] = None,
    image_tool_metrics: Optional[Dict[str, Any]] = None,
    tool_duration_sec: Optional[float] = None,
    tool_cost: Optional[float] = None,
) -> str:
    """创建关键帧版本记录
    
    Returns:
        str: 新创建的version UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            # 处理空列表：转换为 None 让数据库使用默认值
            ref_urls = reference_image_urls if reference_image_urls else None
            audio_ids = audio_segment_ids if audio_segment_ids else None
            char_version_ids = character_version_ids if character_version_ids else None
            
            result = await insert_and_return(
                conn,
                "video_keyframe_versions",
                uuid=uuid,
                keyframe_id=keyframe_id,
                version_number=version_number,
                shot_number=shot_number,
                keyframe_url=keyframe_url,
                t2i_prompt=t2i_prompt,
                provider=provider,
                is_bridge=is_bridge,
                reference_image_urls=ref_urls,
                success=success,
                error_msg=error_msg,
                raw_error_msg=raw_error_msg,
                audio_segment_ids=audio_ids,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                ai_messages=ai_messages,
                character_version_ids=char_version_ids,
                additional_data=additional_data,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                seed=seed,
                model=model,
                image_generation_tool=image_generation_tool,
                image_tool_metrics=image_tool_metrics,
                tool_duration_sec=tool_duration_sec,
                tool_cost=tool_cost,
                created_at=created_at,
                updated_at=created_at
            )
            logger.info(f"✅ 创建关键帧版本成功: {result['uuid']}")
            return result['uuid']
    except Exception as e:
        logger.error(f"创建关键帧版本失败: {e}")
        raise


async def get_keyframe_by_uuid(keyframe_uuid: str) -> Optional[VideoKeyframeDB]:
    """根据UUID获取关键帧
    
    Returns:
        Optional[VideoKeyframeDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_keyframes WHERE uuid = $1",
                keyframe_uuid
            )
            return _row_to_keyframe(row)
    except Exception as e:
        logger.error(f"根据UUID获取关键帧失败: {e}")
        return None


async def get_keyframes_by_story_outline_id(story_outline_id: str) -> List[VideoKeyframeDB]:
    """根据故事大纲ID获取所有关键帧
    
    Returns:
        List[VideoKeyframeDB]: 按shot_number排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_keyframes 
                WHERE story_outline_id = $1 
                ORDER BY shot_number
                """,
                story_outline_id
            )
            return [x for row in rows for x in [_row_to_keyframe(row)] if x is not None]
    except Exception as e:
        logger.error(f"获取关键帧列表失败: {e}")
        return []


async def get_keyframes_by_conversation(conversation_id: str, thread_id: str) -> List[VideoKeyframeDB]:
    """根据对话ID和线程ID获取关键帧列表
    
    Returns:
        List[VideoKeyframeDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_keyframes 
                WHERE conversation_id = $1 AND thread_id = $2
                ORDER BY shot_number
                """,
                conversation_id, thread_id
            )
            return [x for row in rows for x in [_row_to_keyframe(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据对话获取关键帧列表失败: {e}")
        return []


# ==================== 关键帧版本 CRUD ====================

async def get_keyframe_versions_by_keyframe_id(keyframe_id: str) -> List[VideoKeyframeVersionDB]:
    """根据关键帧ID获取所有版本
    
    Returns:
        List[VideoKeyframeVersionDB]: 按version_number排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_keyframe_versions 
                WHERE keyframe_id = $1 
                ORDER BY version_number
                """,
                keyframe_id
            )
            return [x for row in rows for x in [_row_to_keyframe_version(row)] if x is not None]
    except Exception as e:
        logger.error(f"获取关键帧版本列表失败: {e}")
        return []


async def get_keyframe_versions_by_keyframe_ids(keyframe_ids: List[str]) -> List[VideoKeyframeVersionDB]:
    """批量获取关键帧版本 - 避免N+1
    
    Args:
        keyframe_ids: 关键帧ID列表
        
    Returns:
        List[VideoKeyframeVersionDB]: 按keyframe_id和version_number排序
    """
    try:
        if not keyframe_ids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_keyframe_versions 
                WHERE keyframe_id = ANY($1)
                ORDER BY keyframe_id, version_number
                """,
                keyframe_ids
            )
            return [x for row in rows for x in [_row_to_keyframe_version(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取关键帧版本失败: {e}")
        return []


async def get_keyframe_version_by_uuid(uuid: str) -> Optional[VideoKeyframeVersionDB]:
    """根据UUID获取关键帧版本记录
    
    Returns:
        Optional[VideoKeyframeVersionDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_keyframe_versions WHERE uuid = $1",
                uuid
            )
            return _row_to_keyframe_version(row)
    except Exception as e:
        logger.error(f"获取关键帧版本记录失败: {e}")
        return None


async def get_keyframe_versions_by_uuids(uuids: List[str]) -> List[VideoKeyframeVersionDB]:
    """批量获取关键帧版本
    
    Returns:
        List[VideoKeyframeVersionDB]: msgspec对象列表
    """
    try:
        if not uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_keyframe_versions WHERE uuid = ANY($1)",
                uuids
            )
            return [x for row in rows for x in [_row_to_keyframe_version(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取关键帧版本失败: {e}")
        return []


async def update_keyframe_current_version_index(keyframe_uuid: str, current_version_index: int) -> bool:
    """更新关键帧的当前版本索引（用户选择版本后持久化）
    
    Returns:
        bool: 是否更新成功
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_keyframes
                SET current_version_index = $1, updated_at = $2
                WHERE uuid = $3
                """,
                current_version_index,
                now_utc(),
                keyframe_uuid
            )
        logger.info(f"✅ 更新关键帧 current_version_index: keyframe_uuid={keyframe_uuid}, index={current_version_index}")
        return True
    except Exception as e:
        logger.error(f"更新关键帧 current_version_index 失败: {e}")
        return False


# ==================== 批量查询优化函数 ====================

async def batch_get_keyframes_with_latest_versions(
    keyframe_uuids: List[str]
) -> List[Dict[str, Any]]:
    """批量获取关键帧 + 最新版本 - 使用JOIN，一次查询
    
    Args:
        keyframe_uuids: 关键帧UUID列表
        
    Returns:
        List[Dict]: 包含keyframe和latest_version的字典列表
    """
    try:
        if not keyframe_uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT 
                    k.*,
                    v.id as version_id,
                    v.uuid as version_uuid,
                    v.version_number,
                    v.keyframe_url,
                    v.t2i_prompt,
                    v.provider as version_provider,
                    v.shot_number as version_shot_number,
                    v.is_bridge as version_is_bridge,
                    v.reference_image_urls as version_reference_image_urls,
                    v.success as version_success,
                    v.created_at as version_created_at,
                    v.updated_at as version_updated_at
                FROM video_keyframes k
                LEFT JOIN LATERAL (
                    SELECT * FROM video_keyframe_versions
                    WHERE keyframe_id = k.uuid
                    ORDER BY version_number DESC
                    LIMIT 1
                ) v ON true
                WHERE k.uuid = ANY($1)
                ORDER BY k.shot_number
                """,
                keyframe_uuids
            )
            
            results = []
            for row in rows:
                keyframe_data = {k: v for k, v in row.items() if not k.startswith('version_')}
                keyframe = _row_to_keyframe(keyframe_data)
                
                latest_version = None
                if row.get('version_uuid'):
                    version_data = {
                        'id': row['version_id'],
                        'uuid': row['version_uuid'],
                        'keyframe_id': row['uuid'],
                        'version_number': row['version_number'],
                        'keyframe_url': row['keyframe_url'],
                        't2i_prompt': row['t2i_prompt'],
                        'provider': row['version_provider'],
                        'shot_number': row['version_shot_number'],
                        'is_bridge': row['version_is_bridge'],
                        'reference_image_urls': row.get('version_reference_image_urls'),
                        'success': row.get('version_success', True),
                        'conversation_id': row['conversation_id'],
                        'thread_id': row['thread_id'],
                        'run_id': row['run_id'],
                        'user_id': row['user_id'],
                        'created_at': row['version_created_at'],
                        'updated_at': row['version_updated_at']
                    }
                    latest_version = row_to_struct_safe(
                        version_data, VideoKeyframeVersionDB,
                        list_fields=_KEYFRAME_VERSION_LIST_FIELDS,
                        dict_fields=_KEYFRAME_VERSION_DICT_FIELDS
                    )
                
                if keyframe is not None:
                    results.append({
                        'keyframe': keyframe,
                        'latest_version': latest_version
                    })
            
            return results
    except Exception as e:
        logger.error(f"批量获取关键帧+最新版本失败: {e}")
        return []


async def get_keyframes_by_run_id(run_id: str) -> List[VideoKeyframeDB]:
    """根据run_id获取所有关键帧
    
    Args:
        run_id: 运行ID
        
    Returns:
        List[VideoKeyframeDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_keyframes 
                WHERE run_id = $1 
                ORDER BY shot_number, frame_index
                """,
                run_id
            )
            return [x for row in rows for x in [_row_to_keyframe(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据run_id获取关键帧记录失败: {e}")
        return []


async def get_keyframes_by_thread_id(thread_id: str) -> List[VideoKeyframeDB]:
    """根据thread_id获取所有关键帧（跨所有 run）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_keyframes WHERE thread_id = $1 ORDER BY shot_number, frame_index",
                thread_id
            )
            return [x for row in rows for x in [_row_to_keyframe(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据thread_id获取关键帧记录失败: {e}")
        return []


async def get_keyframes_by_uuids(keyframe_uuids: List[str]) -> List[VideoKeyframeDB]:
    """根据UUID列表批量获取关键帧
    
    Args:
        keyframe_uuids: 关键帧UUID列表
        
    Returns:
        List[VideoKeyframeDB]: msgspec对象列表
    """
    try:
        if not keyframe_uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_keyframes 
                WHERE uuid = ANY($1)
                ORDER BY shot_number
                """,
                keyframe_uuids
            )
            return [x for row in rows for x in [_row_to_keyframe(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据UUID列表获取关键帧失败: {e}")
        return []


# ==================== 关键帧反思记录 CRUD ====================

async def create_keyframe_reflection(
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    story_outline_id: str,
    iteration_number: int,
    total_keyframes: int,
    analyzed_keyframes: int,
    regenerated_keyframes: int,
    consistency_score: float,
    analysis_duration_seconds: float = 0.0,
    regeneration_duration_seconds: float = 0.0,
    additional_data: Optional[Dict[str, Any]] = None
) -> str:
    """创建关键帧反思主记录
    
    Returns:
        str: reflection UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            # insert_and_return 返回整行；若将来需返回完整实体，用 _row_to_reflection(result) 以兼容 DB 加列
            result = await insert_and_return(
                conn,
                "video_keyframe_reflections",
                uuid=uuid,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                story_outline_id=story_outline_id,
                iteration_number=iteration_number,
                total_keyframes=total_keyframes,
                analyzed_keyframes=analyzed_keyframes,
                regenerated_keyframes=regenerated_keyframes,
                consistency_score=consistency_score,
                analysis_duration_seconds=analysis_duration_seconds,
                regeneration_duration_seconds=regeneration_duration_seconds,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建关键帧反思记录失败: {e}")
        raise


async def create_keyframe_reflection_result(
    reflection_id: str,
    keyframe_id: str,
    keyframe_version_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    shot_number: int,
    shot_uuid: str,
    needs_regeneration: bool,
    issues: Optional[List[Dict[str, Any]]] = None,
    analysis_summary: Optional[str] = None,
    improvement_points: Optional[List[str]] = None,
    original_description: Optional[str] = None,
    improved_description: Optional[str] = None,
    new_keyframe_version_id: Optional[str] = None,
    regeneration_success: Optional[bool] = None,
    regeneration_error: Optional[str] = None,
    additional_data: Optional[Dict[str, Any]] = None
) -> str:
    """创建关键帧反思结果记录
    
    Returns:
        str: reflection_result UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            # insert_and_return 返回整行；若将来需返回完整实体，用 row_to_struct_safe(result, VideoKeyframeReflectionResultDB, list_fields=("issues","improvement_points"), dict_fields=("additional_data",)) 以兼容 DB 加列
            result = await insert_and_return(
                conn,
                "video_keyframe_reflection_results",
                uuid=uuid,
                reflection_id=reflection_id,
                keyframe_id=keyframe_id,
                keyframe_version_id=keyframe_version_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                shot_number=shot_number,
                shot_uuid=shot_uuid,
                needs_regeneration=needs_regeneration,
                issues=issues,
                analysis_summary=analysis_summary,
                improvement_points=improvement_points,
                original_description=original_description,
                improved_description=improved_description,
                new_keyframe_version_id=new_keyframe_version_id,
                regeneration_success=regeneration_success,
                regeneration_error=regeneration_error,
                additional_data=additional_data,
                created_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建关键帧反思结果记录失败: {e}")
        raise


async def get_reflection_results_by_keyframe_version_ids(
    keyframe_version_ids: List[str],
) -> List[Dict[str, Any]]:
    """按关键帧版本 UUID 批量获取反思结果（每个 version 取最新一条反思结果，用于展示 issues）
    
    Returns:
        List[Dict]: 每项含 keyframe_version_id, issues, analysis_summary, needs_regeneration 等
    """
    if not keyframe_version_ids:
        return []
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            # 每个 keyframe_version_id 取最新一条（按 created_at 降序）
            rows = await fetch_all(
                conn,
                """
                SELECT DISTINCT ON (keyframe_version_id)
                    keyframe_version_id, issues, analysis_summary, needs_regeneration
                FROM video_keyframe_reflection_results
                WHERE keyframe_version_id = ANY($1)
                ORDER BY keyframe_version_id, created_at DESC
                """,
                keyframe_version_ids,
            )
            out = []
            for r in rows:
                issues = r.get("issues")
                if isinstance(issues, list):
                    pass
                elif issues:
                    issues = from_json(issues) if isinstance(issues, str) else []
                else:
                    issues = []
                out.append({
                    "keyframe_version_id": r["keyframe_version_id"],
                    "issues": issues,
                    "analysis_summary": r.get("analysis_summary"),
                    "needs_regeneration": r.get("needs_regeneration"),
                })
            return out
    except Exception as e:
        logger.error(f"按 keyframe_version_id 获取反思结果失败: {e}")
        return []
