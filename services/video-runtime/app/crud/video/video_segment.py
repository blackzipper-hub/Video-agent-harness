"""
视频片段CRUD操作 - 使用msgspec返回类型

包含：
- 视频片段主记录 (video_segments)
- 视频片段版本 (video_segment_versions)
"""

import logging
from typing import List, Optional, Dict, Any

from ...models.database import get_asyncpg_pool
from ...utils.asyncpg_utils import (
    fetch_one, fetch_all, fetch_val, execute,
    insert_and_return, generate_uuid, now_utc, to_json, from_json
)
from ...schemas.video.video_segment import (
    VideoSegmentDB,
    VideoSegmentVersionDB
)
from .row_normalize import row_to_struct_safe

logger = logging.getLogger(__name__)

_SEGMENT_LIST_FIELDS = ("video_generation_ids", "narration_ids", "keyframe_ids", "scene_ids", "storyboard_detail_ids")
_SEGMENT_DICT_FIELDS = ("additional_data",)
_SEGMENT_VERSION_LIST_FIELDS = ("video_generation_version_ids", "narration_version_ids", "keyframe_version_ids")
_SEGMENT_VERSION_DICT_FIELDS = ("additional_data",)


# ==================== Helper Functions ====================

def _row_to_segment(row: Optional[Dict]) -> Optional[VideoSegmentDB]:
    """将数据库行转换为VideoSegmentDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoSegmentDB,
        list_fields=_SEGMENT_LIST_FIELDS,
        dict_fields=_SEGMENT_DICT_FIELDS,
        list_default_empty=True,
    )


def _row_to_segment_version(row: Optional[Dict]) -> Optional[VideoSegmentVersionDB]:
    """将数据库行转换为VideoSegmentVersionDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoSegmentVersionDB,
        list_fields=_SEGMENT_VERSION_LIST_FIELDS,
        dict_fields=_SEGMENT_VERSION_DICT_FIELDS,
        list_default_empty=True,
    )


# ==================== 视频片段主记录 CRUD ====================

async def create_video_segment(
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    story_outline_id: str,
    music_generation_id: Optional[str],
    video_generation_ids: List[str],
    narration_ids: List[str],
    keyframe_ids: List[str],
    scene_ids: List[str],
    storyboard_detail_ids: List[str],
    segment_number: int
) -> VideoSegmentDB:
    """创建视频片段记录
    
    Returns:
        VideoSegmentDB: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            # 空列表转 None（避免 asyncpg 类型推断错误）
            result = await insert_and_return(
                conn,
                "video_segments",
                uuid=uuid,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                story_outline_id=story_outline_id,
                music_generation_id=music_generation_id if music_generation_id else None,
                segment_number=segment_number,
                video_generation_ids=video_generation_ids if video_generation_ids else None,
                narration_ids=narration_ids if narration_ids else None,
                keyframe_ids=keyframe_ids if keyframe_ids else None,
                scene_ids=scene_ids if scene_ids else None,
                storyboard_detail_ids=storyboard_detail_ids if storyboard_detail_ids else None,
                current_version_index=0,
                created_at=created_at,
                updated_at=created_at
            )
            logger.info(f"✅ 视频片段创建成功: {result['uuid']}")
            out = row_to_struct_safe(
                result, VideoSegmentDB,
                list_fields=_SEGMENT_LIST_FIELDS, dict_fields=_SEGMENT_DICT_FIELDS, list_default_empty=True
            )
            if out is None:
                raise ValueError("创建视频片段后转换 Struct 失败")
            return out
    except Exception as e:
        logger.error(f"创建视频片段失败: {e}")
        raise


async def create_video_segment_version(
    video_segment_id: str,
    segment_result: 'VideoSegmentResult',
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str
) -> VideoSegmentVersionDB:
    """创建视频片段版本记录
    
    这个函数需要从其他CRUD模块获取版本ID
    
    Returns:
        VideoSegmentVersionDB: msgspec对象
    """
    try:
        # 导入需要的函数（避免循环导入）
        from .video_generation import get_video_generation_versions_by_video_generation_ids
        from .video_audio import (
            get_narration_versions_by_narration_ids,
            get_music_generation_versions_by_music_generation_ids,
        )
        from .video_keyframe import get_keyframe_versions_by_keyframe_ids
        
        # 获取现有版本以确定新版本号
        existing_versions = await get_video_segment_versions(video_segment_id)
        version_number = len(existing_versions) + 1
        
        # 获取video_generation版本ID
        video_generation_version_ids = []
        if segment_result.video_generation_ids:
            for video_gen_id in segment_result.video_generation_ids:
                try:
                    latest_versions = await get_video_generation_versions_by_video_generation_ids([video_gen_id])
                    if latest_versions:
                        sorted_versions = sorted(latest_versions, key=lambda v: v.version_number, reverse=True)
                        video_generation_version_ids.append(sorted_versions[0].uuid)
                except Exception as e:
                    logger.warning(f"获取video_generation版本ID失败: {video_gen_id}, {e}")
        
        # 获取narration版本ID
        narration_version_ids = []
        if segment_result.narration_ids:
            try:
                narration_versions = await get_narration_versions_by_narration_ids(segment_result.narration_ids)
                narration_version_map = {}
                for version in narration_versions:
                    narration_id = version.narration_id
                    if narration_id not in narration_version_map or version.version_number > narration_version_map[narration_id].version_number:
                        narration_version_map[narration_id] = version
                narration_version_ids = [v.uuid for v in narration_version_map.values()]
            except Exception as e:
                logger.warning(f"获取narration版本ID失败: {e}")
        
        # 获取keyframe版本ID
        keyframe_version_ids = []
        if segment_result.keyframe_ids:
            try:
                keyframe_versions = await get_keyframe_versions_by_keyframe_ids(segment_result.keyframe_ids)
                keyframe_version_map = {}
                for version in keyframe_versions:
                    keyframe_id = version.keyframe_id
                    if keyframe_id not in keyframe_version_map or version.version_number > keyframe_version_map[keyframe_id].version_number:
                        keyframe_version_map[keyframe_id] = version
                keyframe_version_ids = [v.uuid for v in keyframe_version_map.values()]
            except Exception as e:
                logger.warning(f"获取keyframe版本ID失败: {e}")
        
        # 获取music版本ID
        music_generation_version_id = None
        if segment_result.music_generation_id:
            try:
                music_versions = await get_music_generation_versions_by_music_generation_ids([segment_result.music_generation_id])
                if music_versions:
                    sorted_music = sorted(music_versions, key=lambda v: v.version_number, reverse=True)
                    music_generation_version_id = sorted_music[0].uuid
            except Exception as e:
                logger.warning(f"获取music_generation版本ID失败: {segment_result.music_generation_id}, {e}")
        
        # 创建版本记录
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            # 空列表转 None（避免 asyncpg 类型推断错误）
            result = await insert_and_return(
                conn,
                "video_segment_versions",
                uuid=uuid,
                video_segment_id=video_segment_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                version_number=version_number,
                segment_number=segment_result.segment_number,
                video_url=segment_result.merged_video_url or '',
                success=segment_result.success,
                error_msg=segment_result.error,
                video_generation_version_ids=video_generation_version_ids if video_generation_version_ids else None,
                narration_version_ids=narration_version_ids if narration_version_ids else None,
                keyframe_version_ids=keyframe_version_ids if keyframe_version_ids else None,
                music_generation_version_id=music_generation_version_id,
                lipsync_version_id=None,
                duration=segment_result.duration,
                additional_data={
                    'original_video_urls': segment_result.original_video_urls or [],
                    'shot_numbers': segment_result.shot_numbers or [],
                    'music_generation_id': segment_result.music_generation_id,
                    'video_generation_ids': segment_result.video_generation_ids,
                    'narration_ids': segment_result.narration_ids or [],
                    'keyframe_ids': segment_result.keyframe_ids or [],
                    'scene_ids': segment_result.scene_ids or [],
                    'storyboard_detail_ids': segment_result.storyboard_detail_ids or []
                },
                created_at=created_at,
                updated_at=created_at
            )
            
            logger.info(f"✅ 视频片段版本创建成功: {result['uuid']}")
            out = _row_to_segment_version(result)
            if out is None:
                raise ValueError("创建视频片段版本后转换行失败")
            return out
    except Exception as e:
        logger.error(f"❌ 创建视频片段版本失败: {e}")
        raise


async def get_video_segment_by_uuid(uuid: str) -> Optional[VideoSegmentDB]:
    """根据UUID获取视频片段
    
    Returns:
        Optional[VideoSegmentDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_segments WHERE uuid = $1",
                uuid
            )
            return _row_to_segment(row)
    except Exception as e:
        logger.error(f"❌ 获取视频片段失败: {e}")
        return None


async def get_video_segments_by_run_id(run_id: str) -> List[str]:
    """根据run_id获取视频片段UUID列表
    
    Returns:
        List[str]: UUID列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT uuid FROM video_segments WHERE run_id = $1 ORDER BY segment_number",
                run_id
            )
            return [row['uuid'] for row in rows]
    except Exception as e:
        logger.error(f"根据run_id获取视频片段UUID列表失败: {e}")
        return []


async def get_video_segments_with_data_by_run_id(run_id: str) -> List[VideoSegmentDB]:
    """根据run_id获取视频片段完整数据
    
    Returns:
        List[VideoSegmentDB]: msgspec对象列表，按segment_number排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_segments WHERE run_id = $1 ORDER BY segment_number",
                run_id
            )
            return [x for row in rows for x in [_row_to_segment(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据run_id获取视频片段完整数据失败: {e}")
        return []


async def get_video_segments_with_data_by_thread_id(thread_id: str) -> List[VideoSegmentDB]:
    """根据thread_id获取视频片段完整数据（跨所有 run）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_segments WHERE thread_id = $1 ORDER BY segment_number",
                thread_id
            )
            return [x for row in rows for x in [_row_to_segment(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据thread_id获取视频片段完整数据失败: {e}")
        return []


async def get_video_segments_with_data_by_conversation_and_thread(
    conversation_id: str, thread_id: str
) -> List[VideoSegmentDB]:
    """根据 conversation_id + thread_id 获取该 thread 下视频片段完整数据（用于按 thread 组装）
    
    读行后经 _row_to_segment（row_to_struct_safe），只传 schema 字段，DB 新增列不影响老代码。
    
    Returns:
        List[VideoSegmentDB]: msgspec对象列表，按segment_number排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_segments WHERE conversation_id = $1 AND thread_id = $2 ORDER BY segment_number",
                conversation_id, thread_id
            )
            return [x for row in rows for x in [_row_to_segment(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据conversation_id+thread_id获取视频片段完整数据失败: {e}")
        return []


async def get_video_segments_by_uuids(segment_uuids: List[str]) -> Dict[str, VideoSegmentDB]:
    """批量获取视频片段 - 避免N+1
    
    Args:
        segment_uuids: 片段UUID列表
        
    Returns:
        Dict[str, VideoSegmentDB]: 以uuid为key的字典
    """
    try:
        if not segment_uuids:
            return {}
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            all_segments = await fetch_all(
                conn,
                """
                SELECT * FROM video_segments 
                WHERE uuid = ANY($1)
                ORDER BY segment_number
                """,
                segment_uuids
            )
            
            # 构建映射：segment_uuid -> VideoSegmentDB
            segment_map = {x.uuid: x for row in all_segments for x in [_row_to_segment(row)] if x is not None}
            
            logger.info(f"📦 批量获取 {len(all_segments)} 个 video segment 数据")
            return segment_map
    except Exception as e:
        logger.error(f"❌ 批量获取视频片段失败: {e}")
        return {}


# ==================== 视频片段版本 CRUD ====================

async def get_video_segment_versions(video_segment_id: str) -> List[VideoSegmentVersionDB]:
    """获取视频片段的所有版本
    
    Returns:
        List[VideoSegmentVersionDB]: 按version_number排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_segment_versions 
                WHERE video_segment_id = $1 
                ORDER BY version_number
                """,
                video_segment_id
            )
            return [x for row in rows for x in [_row_to_segment_version(row)] if x is not None]
    except Exception as e:
        logger.error(f"❌ 获取视频片段版本失败: {e}")
        return []


async def get_video_segment_versions_by_segment_ids(segment_ids: List[str]) -> Dict[str, List[VideoSegmentVersionDB]]:
    """批量获取多个视频片段的所有版本 - 避免N+1
    
    Args:
        segment_ids: 片段ID列表
        
    Returns:
        Dict[str, List[VideoSegmentVersionDB]]: segment_id -> 版本列表的映射
    """
    try:
        if not segment_ids:
            return {}
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            all_versions = await fetch_all(
                conn,
                """
                SELECT * FROM video_segment_versions 
                WHERE video_segment_id = ANY($1)
                ORDER BY video_segment_id, version_number
                """,
                segment_ids
            )
            
            # 构建映射：segment_id -> [VideoSegmentVersionDB, ...]
            version_map = {}
            for row in all_versions:
                segment_id = row['video_segment_id']
                if segment_id not in version_map:
                    version_map[segment_id] = []
                obj = _row_to_segment_version(row)
                if obj is not None:
                    version_map[segment_id].append(obj)
            
            logger.info(f"📦 批量获取 {len(all_versions)} 个版本，来自 {len(version_map)} 个片段")
            return version_map
    except Exception as e:
        logger.error(f"❌ 批量获取视频片段版本失败: {e}")
        return {}


async def get_video_segment_version_by_uuid(version_uuid: str) -> Optional[VideoSegmentVersionDB]:
    """根据UUID获取视频片段版本
    
    Returns:
        Optional[VideoSegmentVersionDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_segment_versions WHERE uuid = $1",
                version_uuid
            )
            return _row_to_segment_version(row)
    except Exception as e:
        logger.error(f"❌ 获取视频片段版本失败: {e}")
        return None


# ==================== 更新操作 ====================

async def update_video_segment_lipsync(
    video_segment_uuid: str,
    lipsync_generation_uuid: str
) -> bool:
    """更新视频片段的唇形同步ID
    
    Returns:
        bool: 是否更新成功
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                UPDATE video_segments 
                SET lipsync_id = $1, updated_at = $2 
                WHERE uuid = $3 
                RETURNING uuid
                """,
                lipsync_generation_uuid, now_utc(), video_segment_uuid
            )
            
            if result:
                logger.info(f"✅ 更新视频片段唇形同步ID成功: {video_segment_uuid} -> {lipsync_generation_uuid}")
                return True
            else:
                logger.error(f"❌ 视频片段不存在: {video_segment_uuid}")
                return False
    except Exception as e:
        logger.error(f"❌ 更新视频片段唇形同步ID失败: {e}")
        return False


async def update_video_segment_version_lipsync(
    video_segment_version_uuid: str,
    lipsync_version_uuid: str
) -> bool:
    """更新视频片段版本的唇形同步版本ID
    
    Returns:
        bool: 是否更新成功
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                UPDATE video_segment_versions 
                SET lipsync_version_id = $1, updated_at = $2 
                WHERE uuid = $3 
                RETURNING uuid
                """,
                lipsync_version_uuid, now_utc(), video_segment_version_uuid
            )
            
            if result:
                logger.info(f"✅ 更新视频片段版本唇形同步版本ID成功: {video_segment_version_uuid} -> {lipsync_version_uuid}")
                return True
            else:
                logger.error(f"❌ 视频片段版本不存在: {video_segment_version_uuid}")
                return False
    except Exception as e:
        logger.error(f"❌ 更新视频片段版本唇形同步版本ID失败: {e}")
        return False


async def batch_update_video_segment_current_version_index(updates: List[Dict[str, Any]]) -> bool:
    """批量更新 video_segments 的 current_version_index（用户选择片段版本后持久化）

    Args:
        updates: 列表，每项为 {'uuid': str, 'current_version_index': int}

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
                        UPDATE video_segments
                        SET current_version_index = $1, updated_at = $2
                        WHERE uuid = $3
                        """,
                        update["current_version_index"],
                        now_utc(),
                        update["uuid"],
                    )
        logger.info(f"✅ 批量更新 {len(updates)} 个 video_segment 的 current_version_index")
        return True
    except Exception as e:
        logger.error(f"批量更新 video_segment current_version_index 失败: {e}")
        return False


# ==================== 批量查询优化函数 ====================

async def batch_get_segments_with_latest_versions(
    segment_uuids: List[str]
) -> List[Dict[str, Any]]:
    """批量获取片段 + 最新版本 - 使用JOIN，一次查询
    
    Args:
        segment_uuids: 片段UUID列表
        
    Returns:
        List[Dict]: 包含segment和latest_version的字典列表
    """
    try:
        if not segment_uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT 
                    s.*,
                    v.uuid as version_uuid,
                    v.version_number,
                    v.video_url,
                    v.duration as version_duration,
                    v.success as version_success
                FROM video_segments s
                LEFT JOIN LATERAL (
                    SELECT * FROM video_segment_versions
                    WHERE video_segment_id = s.uuid
                    ORDER BY version_number DESC
                    LIMIT 1
                ) v ON true
                WHERE s.uuid = ANY($1)
                ORDER BY s.segment_number
                """,
                segment_uuids
            )
            
            results = []
            for row in rows:
                # 分离segment和version字段（只传 schema 字段，DB 多列不崩）
                segment_data = {k: v for k, v in row.items()
                               if not k.startswith('version_') and k != 'version_uuid'}
                segment = _row_to_segment(segment_data)
                
                # 构建version对象（如果存在）
                latest_version = None
                if row.get('version_uuid'):
                    # 需要构建完整的version对象，暂时返回简化版本
                    latest_version = {
                        'uuid': row['version_uuid'],
                        'version_number': row['version_number'],
                        'video_url': row['video_url'],
                        'duration': row.get('version_duration'),
                        'success': row.get('version_success')
                    }
                
                if segment is not None:
                    results.append({
                        'segment': segment,
                        'latest_version': latest_version
                    })
            
            return results
    except Exception as e:
        logger.error(f"批量获取片段+最新版本失败: {e}")
        return []
