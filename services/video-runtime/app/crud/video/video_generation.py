"""
视频生成CRUD操作 - 使用msgspec返回类型

包含：
- 视频生成主记录 (video_generations)
- 视频生成版本 (video_generation_versions)
"""

import logging
from typing import List, Optional, Dict, Any
from uuid import UUID

from ...models.database import get_asyncpg_pool
from ...utils.asyncpg_utils import (
    fetch_one, fetch_all, fetch_val, execute,
    insert_and_return, generate_uuid, now_utc, to_json, from_json
)
from ...schemas.video.video_generation import (
    VideoGenerationDB,
    VideoGenerationVersionDB
)

logger = logging.getLogger(__name__)


# ==================== Helper Functions ====================

from .row_normalize import row_to_struct_safe

_GENERATION_LIST_FIELDS = ("keyframe_ids",)
_GENERATION_DICT_FIELDS = ("additional_data",)
_GENERATION_VERSION_LIST_FIELDS = ("keyframe_version_ids", "audio_segment_ids")
_GENERATION_VERSION_DICT_FIELDS = ("raw_generation_params", "additional_data", "video_tool_metrics")


def _row_to_video_generation(row: Optional[Dict]) -> Optional[VideoGenerationDB]:
    """将数据库行转换为VideoGenerationDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoGenerationDB,
        list_fields=_GENERATION_LIST_FIELDS, dict_fields=_GENERATION_DICT_FIELDS
    )


def _row_to_video_generation_version(row: Optional[Dict]) -> Optional[VideoGenerationVersionDB]:
    """将数据库行转换为VideoGenerationVersionDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoGenerationVersionDB,
        list_fields=_GENERATION_VERSION_LIST_FIELDS, dict_fields=_GENERATION_VERSION_DICT_FIELDS
    )


# ==================== 视频生成主记录 CRUD ====================

async def create_video_generation(
    shot_number: int,
    is_bridge: bool,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    story_outline_id: str,
    scene_id: str,
    storyboard_detail_id: str,
    detailed_shot_id: str,
    keyframe_id: Optional[str] = None,
    keyframe_ids: Optional[List[str]] = None
) -> str:
    """创建视频生成记录。

    keyframe_id 在 reference_t2v（跳过关键帧）路径可为 None。
    
    Returns:
        str: 新创建的video_generation UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            kf_id = (keyframe_id or "").strip() or None
            kf_ids = [x for x in (keyframe_ids or []) if x] or None
            
            # 空列表转 None（避免 asyncpg 类型推断错误）
            result = await insert_and_return(
                conn,
                "video_generations",
                uuid=uuid,
                shot_number=shot_number,
                is_bridge=is_bridge,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                story_outline_id=story_outline_id,
                scene_id=scene_id,
                storyboard_detail_id=storyboard_detail_id,
                detailed_shot_id=detailed_shot_id,
                keyframe_id=kf_id,
                keyframe_ids=kf_ids,
                current_version_index=0,
                created_at=created_at,
                updated_at=created_at
            )
            logger.info(f"✅ 创建视频生成记录成功: {result['uuid']}")
            return result['uuid']
    except Exception as e:
        logger.error(f"创建视频生成记录失败: {e}")
        raise


async def get_video_generation_by_uuid(uuid: str) -> Optional[VideoGenerationDB]:
    """根据UUID获取单个视频生成主记录
    
    Returns:
        Optional[VideoGenerationDB]: msgspec对象，有完整类型提示
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_generations WHERE uuid = $1",
                uuid
            )
            return _row_to_video_generation(row)
    except Exception as e:
        logger.error(f"根据UUID获取视频生成主记录失败: {e}")
        return None


async def get_video_generations_by_uuids(uuids: List[str]) -> List[VideoGenerationDB]:
    """批量获取视频生成主记录 - 避免N+1
    
    Args:
        uuids: video_generation UUID列表
        
    Returns:
        List[VideoGenerationDB]: msgspec对象列表，按shot_number排序
    """
    try:
        if not uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_generations 
                WHERE uuid = ANY($1)
                ORDER BY shot_number
                """,
                uuids
            )
            return [x for row in rows for x in [_row_to_video_generation(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取视频生成主记录失败: {e}")
        return []


async def get_video_generations_by_run_id(run_id: str) -> List[VideoGenerationDB]:
    """根据run_id获取所有视频生成记录
    
    Returns:
        List[VideoGenerationDB]: 按shot_number排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_generations 
                WHERE run_id = $1
                ORDER BY shot_number
                """,
                run_id
            )
            return [x for row in rows for x in [_row_to_video_generation(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据run_id获取视频生成记录失败: {e}")
        return []


async def get_video_generations_by_thread_id(thread_id: str) -> List[VideoGenerationDB]:
    """根据thread_id获取所有视频生成记录（跨所有 run）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_generations WHERE thread_id = $1 ORDER BY shot_number",
                thread_id
            )
            return [x for row in rows for x in [_row_to_video_generation(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据thread_id获取视频生成记录失败: {e}")
        return []


async def get_video_generations_by_conversation_id(conversation_id: str) -> List[VideoGenerationDB]:
    """根据conversation_id获取所有视频生成记录"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_generations 
                WHERE conversation_id = $1
                ORDER BY shot_number
                """,
                conversation_id
            )
            return [x for row in rows for x in [_row_to_video_generation(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据conversation_id获取视频生成记录失败: {e}")
        return []


async def get_video_generation_by_keyframe_id(keyframe_id: str) -> Optional[VideoGenerationDB]:
    """根据keyframe_id获取视频生成记录"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_generations WHERE keyframe_id = $1",
                keyframe_id
            )
            return _row_to_video_generation(row)
    except Exception as e:
        logger.error(f"根据keyframe_id获取视频生成记录失败: {e}")
        return None


# ==================== 视频生成版本 CRUD ====================

async def create_video_generation_version(
    video_generation_id: str,
    version_number: int,
    shot_number: int,
    video_url: str,
    provider: str,
    is_bridge: bool,
    success: bool,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    error_msg: str = None,
    raw_error_msg: str = None,
    keyframe_url: str = None,
    keyframe_version_ids: Optional[List[str]] = None,
    motion_prompt: str = None,
    duration: float = None,
    fps: int = None,
    ai_messages: str = None,
    audio_segment_ids: List[str] = None,
    additional_data: Optional[Dict[str, Any]] = None,
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    video_generation_tool: Optional[str] = None,
    model: Optional[str] = None,
    generation_mode: Optional[str] = None,
    audio_url: Optional[str] = None,
    video_tool_metrics: Optional[Dict[str, Any]] = None,
    tool_duration_sec: Optional[float] = None,
    tool_cost: Optional[float] = None,
) -> str:
    """创建视频生成版本记录
    
    Returns:
        str: 新创建的version UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            # 空列表转 None（避免 asyncpg 类型推断错误）
            result = await insert_and_return(
                conn,
                "video_generation_versions",
                uuid=uuid,
                video_generation_id=video_generation_id,
                version_number=version_number,
                shot_number=shot_number,
                video_url=video_url,
                provider=provider,
                is_bridge=is_bridge,
                success=success,
                error_msg=error_msg,
                raw_error_msg=raw_error_msg,
                keyframe_url=keyframe_url,
                keyframe_version_ids=keyframe_version_ids if keyframe_version_ids else None,
                motion_prompt=motion_prompt,
                duration=duration,
                fps=fps,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                ai_messages=ai_messages,
                audio_segment_ids=audio_segment_ids if audio_segment_ids else None,
                additional_data=additional_data,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                video_generation_tool=video_generation_tool,
                model=model,
                generation_mode=generation_mode,
                audio_url=audio_url,
                video_tool_metrics=video_tool_metrics,
                tool_duration_sec=tool_duration_sec,
                tool_cost=tool_cost,
                created_at=created_at,
                updated_at=created_at
            )
            logger.info(f"✅ 创建视频生成版本成功: {result['uuid']}")
            return result['uuid']
    except Exception as e:
        logger.error(f"创建视频生成版本失败: {e}")
        raise


async def get_video_generation_version_by_uuid(uuid: str) -> Optional[VideoGenerationVersionDB]:
    """根据UUID获取单个视频生成版本
    
    Returns:
        Optional[VideoGenerationVersionDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_generation_versions WHERE uuid = $1",
                uuid
            )
            return _row_to_video_generation_version(row)
    except Exception as e:
        logger.error(f"根据UUID获取视频生成版本失败: {e}")
        return None


async def update_video_generation_version_video_url(version_uuid: str, video_url: str) -> None:
    """更新指定 video_generation_version 的 video_url（兜底迁移到本 CDN 后写回 DB）。"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                "UPDATE video_generation_versions SET video_url = $2, updated_at = $3 WHERE uuid = $1",
                version_uuid,
                video_url,
                now_utc(),
            )
        logger.info(f"✅ 已更新 video_generation_version {version_uuid[:8]}... 的 video_url")
    except Exception as e:
        logger.error(f"更新 video_generation_version video_url 失败: {e}")
        raise


async def get_video_generation_versions_by_uuids(uuids: List[str]) -> List[VideoGenerationVersionDB]:
    """批量获取视频生成版本 - 避免N+1
    
    Returns:
        List[VideoGenerationVersionDB]: msgspec对象列表
    """
    try:
        if not uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_generation_versions 
                WHERE uuid = ANY($1)
                ORDER BY version_number
                """,
                uuids
            )
            return [x for row in rows for x in [_row_to_video_generation_version(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取视频生成版本失败: {e}")
        return []


async def get_video_generation_versions_by_video_generation_ids(
    video_generation_ids: List[str]
) -> List[VideoGenerationVersionDB]:
    """根据video_generation_id列表获取所有版本 - 批量查询避免N+1
    
    Args:
        video_generation_ids: video_generation UUID列表
        
    Returns:
        List[VideoGenerationVersionDB]: 所有版本，按video_generation_id和version_number排序
    """
    try:
        if not video_generation_ids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_generation_versions 
                WHERE video_generation_id = ANY($1)
                ORDER BY video_generation_id, version_number
                """,
                video_generation_ids
            )
            return [x for row in rows for x in [_row_to_video_generation_version(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取视频生成版本失败: {e}")
        return []


# 别名函数（向后兼容）
get_video_generation_versions_by_video_generation_uuids = get_video_generation_versions_by_video_generation_ids


# 旧的函数别名（向后兼容）
get_video_generations_by_conversation = get_video_generations_by_conversation_id


# ==================== 高级查询函数 ====================

async def get_video_generations_with_latest_versions(
    video_gen_uuids: List[str]
) -> List[Dict[str, Any]]:
    """批量获取视频生成 + 最新版本 - 一次查询，性能优化
    
    Args:
        video_gen_uuids: video_generation UUID列表
        
    Returns:
        List[Dict]: 包含video和latest_version的字典列表
        [
            {
                'video': VideoGenerationDB,
                'latest_version': VideoGenerationVersionDB
            },
            ...
        ]
    """
    try:
        if not video_gen_uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            # 使用JOIN + LATERAL获取最新版本
            rows = await fetch_all(
                conn,
                """
                SELECT 
                    vg.*,
                    v.id as version_id,
                    v.uuid as version_uuid,
                    v.version_number,
                    v.video_url,
                    v.provider as version_provider,
                    v.success as version_success,
                    v.model as version_model,
                    v.video_generation_tool as version_video_generation_tool,
                    v.aspect_ratio as version_aspect_ratio,
                    v.resolution as version_resolution
                FROM video_generations vg
                LEFT JOIN LATERAL (
                    SELECT * FROM video_generation_versions
                    WHERE video_generation_id = vg.uuid
                    ORDER BY version_number DESC
                    LIMIT 1
                ) v ON true
                WHERE vg.uuid = ANY($1)
                ORDER BY vg.shot_number
                """,
                video_gen_uuids
            )
            
            results = []
            for row in rows:
                # 分离video和version字段（只传 schema 字段，DB 多列不崩）
                video_data = {k: v for k, v in row.items() if not k.startswith('version_')}
                video = _row_to_video_generation(video_data)
                if video is None:
                    continue
                # 如果有version数据，构建version对象
                latest_version = None
                if row.get('version_id'):
                    version_data = {
                        'id': row['version_id'],
                        'uuid': row['version_uuid'],
                        'video_generation_id': row['uuid'],
                        'version_number': row['version_number'],
                        'video_url': row['video_url'],
                        'provider': row['version_provider'],
                        'success': row['version_success'],
                        'shot_number': row['shot_number'],
                        'is_bridge': row['is_bridge'],
                        'conversation_id': row['conversation_id'],
                        'thread_id': row['thread_id'],
                        'run_id': row['run_id'],
                        'user_id': row['user_id'],
                        'created_at': row['created_at'],
                        'updated_at': row['updated_at'],
                        'model': row.get('version_model'),
                        'video_generation_tool': row.get('version_video_generation_tool'),
                        'aspect_ratio': row.get('version_aspect_ratio'),
                        'resolution': row.get('version_resolution'),
                    }
                    latest_version = _row_to_video_generation_version(version_data)
                
                results.append({
                    'video': video,
                    'latest_version': latest_version
                })
            
            return results
    except Exception as e:
        logger.error(f"批量获取视频生成+最新版本失败: {e}")
        return []


async def batch_get_video_generations_with_all_versions(
    video_gen_uuids: List[str]
) -> Dict[str, Dict[str, Any]]:
    """批量获取视频生成 + 所有版本 - 两次查询，避免N+1
    
    Args:
        video_gen_uuids: video_generation UUID列表
        
    Returns:
        Dict[str, Dict]: 以uuid为key的字典
        {
            'uuid1': {
                'video': VideoGenerationDB,
                'versions': [VideoGenerationVersionDB, ...]
            },
            ...
        }
    """
    try:
        if not video_gen_uuids:
            return {}
        
        # 1. 批量获取video_generations
        videos = await get_video_generations_by_uuids(video_gen_uuids)
        
        # 2. 批量获取所有versions (一次查询)
        all_versions = await get_video_generation_versions_by_video_generation_ids(video_gen_uuids)
        
        # 3. 在内存中组装数据
        versions_by_video = {}
        for version in all_versions:
            video_uuid = version.video_generation_id
            if video_uuid not in versions_by_video:
                versions_by_video[video_uuid] = []
            versions_by_video[video_uuid].append(version)
        
        # 4. 返回组装结果
        results = {}
        for video in videos:
            results[video.uuid] = {
                'video': video,
                'versions': versions_by_video.get(video.uuid, [])
            }
        
        return results
    except Exception as e:
        logger.error(f"批量获取视频生成+所有版本失败: {e}")
        return {}


async def get_video_generation_versions_by_video_generation_uuids(uuids: List[str]) -> List[VideoGenerationVersionDB]:
    """根据video_generation UUID列表获取所有版本记录
    
    Args:
        uuids: video_generation UUID列表
        
    Returns:
        List[VideoGenerationVersionDB]: msgspec对象列表
    """
    try:
        if not uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_generation_versions 
                WHERE video_generation_id = ANY($1)
                ORDER BY shot_number, version_number
                """,
                uuids
            )
            return [x for row in rows for x in [_row_to_video_generation_version(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据UUID列表获取视频生成版本失败: {e}")
        return []


async def get_video_generations_by_conversation(conversation_id: str, thread_id: str) -> List[VideoGenerationDB]:
    """根据对话ID和线程ID获取所有视频生成记录
    
    Args:
        conversation_id: 对话ID
        thread_id: 线程ID
        
    Returns:
        List[VideoGenerationDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_generations
                WHERE conversation_id = $1 AND thread_id = $2
                ORDER BY shot_number
                """,
                conversation_id, thread_id
            )
            return [x for row in rows for x in [_row_to_video_generation(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据对话获取视频生成列表失败: {e}")
        return []


async def batch_update_video_generation_current_version_index(updates: List[Dict[str, Any]]) -> bool:
    """批量更新 video_generation 的 current_version_index
    
    Args:
        updates: 更新列表，每个元素包含 {'uuid': str, 'current_version_index': int}
        
    Returns:
        bool: 是否全部更新成功
    """
    try:
        if not updates:
            return True
            
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                for update in updates:
                    await execute(
                        conn,
                        """
                        UPDATE video_generations 
                        SET current_version_index = $1, updated_at = $2
                        WHERE uuid = $3
                        """,
                        update['current_version_index'],
                        now_utc(),
                        update['uuid']
                    )
        
        logger.info(f"✅ 批量更新 {len(updates)} 个 video_generation 的 current_version_index")
        return True
    except Exception as e:
        logger.error(f"批量更新 video_generation current_version_index 失败: {e}")
        return False
