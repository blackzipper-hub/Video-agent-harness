"""
视频故事CRUD操作 - 使用msgspec返回类型

包含：
- 故事大纲 (video_story_outline)
- 场景 (video_scenes)
- 详细分镜 (video_storyboard_details)
- 详细镜头 (video_detailed_shots)
- 章节 (video_chapters)
"""

import logging
import math
from typing import List, Optional, Dict, Any

from ...models.database import get_asyncpg_pool
from ...utils.asyncpg_utils import (
    fetch_one, fetch_all, fetch_val, execute,
    insert_and_return, generate_uuid, now_utc, to_json, from_json,
    build_update_query
)
from ...schemas.video.video_story import (
    VideoStoryOutlineDB,
    VideoStoryOutlineVersionDB,
    VideoSceneDB,
    VideoSceneVersionDB,
    VideoStoryboardDetailDB,
    VideoDetailedShotDB,
    VideoChapterDB,
    VideoChapterVersionDB,
)
from ...utils.asyncpg_utils import filter_row_to_struct_keys
from .row_normalize import normalize_row, row_to_struct_safe, set_effective_duration

logger = logging.getLogger(__name__)

_STORY_OUTLINE_LIST_FIELDS = ("themes",)
_STORY_OUTLINE_DICT_FIELDS = ("additional_data",)
_SCENE_LIST_FIELDS = ("character_ids", "audio_segment_ids")
_SCENE_DICT_FIELDS = ("additional_data",)
_STORYBOARD_DICT_FIELDS = ("additional_data",)
_SHOT_LIST_FIELDS = ("character_ids", "audio_segment_ids")
_SHOT_DICT_FIELDS = ("additional_data", "generation_routing")
_CHAPTER_LIST_FIELDS = ("audio_segment_ids",)
_CHAPTER_DICT_FIELDS = ("additional_data",)


# ==================== Helper Functions ====================

def _row_to_story_outline(row: Optional[Dict]) -> Optional[VideoStoryOutlineDB]:
    """将数据库行转换为VideoStoryOutlineDB对象；兼容 DB 列 theme/key_message/style_guide 映射到 schema；只传 schema 字段，DB 多列不崩"""
    row = normalize_row(row, list_fields=_STORY_OUTLINE_LIST_FIELDS, dict_fields=_STORY_OUTLINE_DICT_FIELDS)
    if not row:
        return None
    row = dict(row)
    set_effective_duration(row, "total_duration_sec", "total_duration")
    if row.get("themes") is None:
        row["themes"] = [row["theme"]] if row.get("theme") else []
    if row.get("target_audience") is None:
        row["target_audience"] = row.get("key_message") or ""
    if row.get("narrative_structure") is None:
        row["narrative_structure"] = row.get("style_guide") or ""
    filtered = filter_row_to_struct_keys(row, VideoStoryOutlineDB)
    return VideoStoryOutlineDB(**filtered) if filtered else None


def _row_to_scene(row: Optional[Dict]) -> Optional[VideoSceneDB]:
    """将数据库行转换为VideoSceneDB对象（只传 schema 字段，DB 多列不崩）"""
    if row:
        row = dict(row)
        set_effective_duration(row, "duration_sec", "duration")
    return row_to_struct_safe(
        row, VideoSceneDB,
        list_fields=_SCENE_LIST_FIELDS, dict_fields=_SCENE_DICT_FIELDS
    )


def _row_to_storyboard(row: Optional[Dict]) -> Optional[VideoStoryboardDetailDB]:
    """将数据库行转换为VideoStoryboardDetailDB对象（只传 schema 字段，DB 多列不崩）"""
    if row:
        row = dict(row)
        set_effective_duration(row, "total_duration_sec", "total_duration")
    return row_to_struct_safe(row, VideoStoryboardDetailDB, dict_fields=_STORYBOARD_DICT_FIELDS)


def _row_to_shot(row: Optional[Dict]) -> Optional[VideoDetailedShotDB]:
    """将数据库行转换为VideoDetailedShotDB对象（只传 schema 字段，DB 多列不崩）"""
    if row:
        row = dict(row)
        set_effective_duration(row, "duration_sec", "duration")
    return row_to_struct_safe(
        row, VideoDetailedShotDB,
        list_fields=_SHOT_LIST_FIELDS, dict_fields=_SHOT_DICT_FIELDS
    )


def _row_to_chapter(row: Optional[Dict]) -> Optional[VideoChapterDB]:
    """将数据库行转换为VideoChapterDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoChapterDB,
        list_fields=_CHAPTER_LIST_FIELDS, dict_fields=_CHAPTER_DICT_FIELDS
    )


# ==================== 故事大纲 CRUD ====================

async def create_video_story_outline(
    user_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    title: str,
    description: str,
    theme: str,
    key_message: str,
    total_duration: float,
    style_guide: str,
    analysis_id: Optional[str] = None,
    additional_data: Optional[Dict[str, Any]] = None,
    audio_transcription_uuid: Optional[str] = None,
) -> Optional[VideoStoryOutlineDB]:
    """创建故事大纲记录
    
    与 PostgreSQL 表 video_story_outline 列一致：theme, key_message, style_guide, analysis_id
    （schema 中 themes/target_audience/narrative_structure 由 _row_to_story_outline 从 theme/key_message/style_guide 映射）
    
    Returns:
        VideoStoryOutlineDB: 新创建记录（含 .uuid），供 outline_db.uuid 使用
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid_val = generate_uuid()
            created_at = now_utc()
            payload = dict(
                uuid=uuid_val,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                title=title,
                description=description,
                theme=theme,
                key_message=key_message,
                total_duration_sec=total_duration,
                style_guide=style_guide,
                analysis_id=analysis_id,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at,
            )
            if audio_transcription_uuid is not None:
                payload["audio_transcription_uuid"] = audio_transcription_uuid
            result = await insert_and_return(conn, "video_story_outline", **payload)
            if result:
                set_effective_duration(result, "total_duration_sec", "total_duration")
            return _row_to_story_outline(result) if result else None
    except Exception as e:
        logger.error(f"创建故事大纲失败: {e}")
        raise


async def get_video_story_outline_by_uuid(uuid: str) -> Optional[VideoStoryOutlineDB]:
    """根据UUID获取故事大纲
    
    Returns:
        Optional[VideoStoryOutlineDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_story_outline WHERE uuid = $1",
                uuid
            )
            return _row_to_story_outline(row)
    except Exception as e:
        logger.error(f"根据UUID获取故事大纲失败: {e}")
        return None


async def get_video_story_outline_by_conversation_id(conversation_id: str) -> Optional[VideoStoryOutlineDB]:
    """根据conversation_id获取故事大纲
    
    Returns:
        Optional[VideoStoryOutlineDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_story_outline WHERE conversation_id = $1",
                conversation_id
            )
            return _row_to_story_outline(row)
    except Exception as e:
        logger.error(f"根据conversation_id获取故事大纲失败: {e}")
        return None


async def get_video_story_outline_by_run_id(run_id: str) -> Optional[VideoStoryOutlineDB]:
    """根据run_id获取故事大纲
    
    Returns:
        Optional[VideoStoryOutlineDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_story_outline WHERE run_id = $1",
                run_id
            )
            return _row_to_story_outline(row)
    except Exception as e:
        logger.error(f"根据run_id获取故事大纲失败: {e}")
        return None


async def get_video_story_outline_by_thread_id(thread_id: str) -> Optional[VideoStoryOutlineDB]:
    """根据 thread_id 获取该线程下最新的故事大纲（用于按 thread 组装时取元数据）
    
    一个 thread 下可能有多轮 run，取 created_at 最新的一条。
    读行后经 _row_to_story_outline（normalize_row + filter_row_to_struct_keys），DB 新增列不影响老代码。
    
    Returns:
        Optional[VideoStoryOutlineDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_story_outline WHERE thread_id = $1 ORDER BY created_at DESC LIMIT 1",
                thread_id
            )
            return _row_to_story_outline(row)
    except Exception as e:
        logger.error(f"根据thread_id获取故事大纲失败: {e}")
        return None


async def update_video_story_outline(
    uuid: str,
    update_data: Dict[str, Any]
) -> bool:
    """更新故事大纲
    
    Returns:
        bool: 是否更新成功
    """
    try:
        if not update_data:
            return False
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            update_data['updated_at'] = now_utc()
            
            query, values = build_update_query(
                table="video_story_outline",
                data=update_data,
                where={"uuid": uuid}
            )
            
            result = await conn.fetchrow(query, *values)
            return result is not None
    except Exception as e:
        logger.error(f"更新故事大纲失败: {e}")
        return False


async def create_video_story_outline_version(
    story_outline_id: str,
    version_number: int,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    title: str,
    description: str,
    theme: Optional[str] = None,
    key_message: Optional[str] = None,
    total_duration: Optional[int] = None,
    style_guide: Optional[str] = None,
    analysis_id: Optional[str] = None,
    additional_data: Optional[Dict[str, Any]] = None,
) -> Optional[VideoStoryOutlineVersionDB]:
    """保存故事大纲版本快照（Outline 版本表，无外键）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            result = await insert_and_return(
                conn,
                "video_story_outline_versions",
                uuid=uuid,
                story_outline_id=story_outline_id,
                version_number=version_number,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                title=title,
                description=description,
                theme=theme,
                key_message=key_message,
                total_duration=total_duration,
                style_guide=style_guide,
                analysis_id=analysis_id,
                additional_data=additional_data,
            )
            if not result:
                return None
            return row_to_struct_safe(result, VideoStoryOutlineVersionDB, dict_fields=("additional_data",))
    except Exception as e:
        logger.error(f"创建故事大纲版本失败: {e}")
        return None


# ==================== 场景 CRUD ====================

async def create_scene(scene_data: dict) -> str:
    """创建场景记录
    
    Returns:
        str: 新创建的scene UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            # 处理空列表：转换为 None 让数据库使用默认值
            char_ids = scene_data.get("character_ids") or None
            audio_ids = scene_data.get("audio_segment_ids") or None
            
            result = await insert_and_return(
                conn,
                "video_scenes",
                uuid=uuid,
                user_id=scene_data["user_id"],
                conversation_id=scene_data["conversation_id"],
                thread_id=scene_data["thread_id"],
                run_id=scene_data["run_id"],
                scene_number=scene_data["scene_number"],
                title=scene_data["title"],
                description=scene_data["description"],
                duration_sec=float(scene_data["duration"]) if scene_data.get("duration") is not None else None,
                camera_angle=scene_data["camera_angle"],
                character_action=scene_data["character_action"],
                visual_style=scene_data["visual_style"],
                transition_style=scene_data["transition_style"],
                is_bridge=scene_data.get("is_bridge", False),
                character_ids=char_ids,
                audio_segment_ids=audio_ids,
                chapter_id=scene_data.get("chapter_id"),
                generation_mode=scene_data.get("generation_mode"),
                additional_data=scene_data.get("additional_data"),
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建场景失败: {e}")
        raise


async def get_scene_by_uuid(scene_uuid: str) -> Optional[VideoSceneDB]:
    """根据UUID获取场景
    
    Returns:
        Optional[VideoSceneDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_scenes WHERE uuid = $1",
                scene_uuid
            )
            return _row_to_scene(row)
    except Exception as e:
        logger.error(f"获取场景失败: {e}")
        return None


async def get_scenes_by_uuids(scene_uuids: List[str]) -> List[VideoSceneDB]:
    """批量获取场景 - 避免N+1
    
    Returns:
        List[VideoSceneDB]: msgspec对象列表
    """
    try:
        if not scene_uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_scenes WHERE uuid = ANY($1)",
                scene_uuids
            )
            return [x for row in rows for x in [_row_to_scene(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取场景失败: {e}")
        return []


async def get_scenes_by_run_id(run_id: str) -> List[VideoSceneDB]:
    """根据run_id获取场景列表
    
    Returns:
        List[VideoSceneDB]: 按scene_number排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_scenes 
                WHERE run_id = $1 
                ORDER BY scene_number
                """,
                run_id
            )
            return [x for row in rows for x in [_row_to_scene(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据run_id获取场景列表失败: {e}")
        return []


async def get_scenes_by_thread_id(thread_id: str) -> List[VideoSceneDB]:
    """根据thread_id获取场景列表（跨所有 run）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_scenes WHERE thread_id = $1 ORDER BY scene_number",
                thread_id
            )
            return [x for row in rows for x in [_row_to_scene(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据thread_id获取场景列表失败: {e}")
        return []


async def get_scenes_by_conversation(conversation_id: str, thread_id: str) -> List[VideoSceneDB]:
    """根据 conversation_id 与 thread_id 获取该 thread 下所有场景（多 run 聚合）。
    读路径使用 _row_to_scene（row_to_struct_safe），DB 新增列不影响线上代码。"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_scenes
                WHERE conversation_id = $1 AND thread_id = $2
                ORDER BY scene_number
                """,
                conversation_id,
                thread_id,
            )
            return [x for row in rows for x in [_row_to_scene(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据 conversation/thread 获取场景列表失败: {e}")
        return []


async def update_scene(scene_uuid: str, scene_data: dict) -> bool:
    """更新场景记录（直接改主表，无版本表）。
    
    Returns:
        bool: 是否更新成功
    """
    try:
        if not scene_data:
            return False
        payload = dict(scene_data)
        if payload.get("duration") is not None:
            payload["duration_sec"] = float(payload.pop("duration", None))
        payload["updated_at"] = now_utc()
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            query, values = build_update_query(
                table="video_scenes",
                data=payload,
                where={"uuid": scene_uuid}
            )
            result = await conn.fetchrow(query, *values)
            return result is not None
    except Exception as e:
        logger.error(f"更新场景失败: {e}")
        return False


async def create_video_scene_version(
    scene_id: str,
    version_number: int,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    scene_number: int,
    title: str,
    description: str,
    duration: int,
    camera_angle: str,
    character_action: str,
    visual_style: str,
    transition_style: str,
    is_bridge: bool = False,
    character_ids: Optional[List[str]] = None,
    audio_segment_ids: Optional[List[str]] = None,
    chapter_id: Optional[str] = None,
    generation_mode: Optional[str] = None,
    additional_data: Optional[Dict[str, Any]] = None,
) -> Optional[VideoSceneVersionDB]:
    """保存场景版本快照（Scene 版本表，无外键）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            result = await insert_and_return(
                conn,
                "video_scene_versions",
                uuid=uuid,
                scene_id=scene_id,
                version_number=version_number,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                scene_number=scene_number,
                title=title,
                description=description,
                duration=duration,
                camera_angle=camera_angle,
                character_action=character_action,
                visual_style=visual_style,
                transition_style=transition_style,
                is_bridge=is_bridge,
                character_ids=character_ids,
                audio_segment_ids=audio_segment_ids,
                chapter_id=chapter_id,
                generation_mode=generation_mode,
                additional_data=additional_data,
            )
            if not result:
                return None
            return row_to_struct_safe(
                result, VideoSceneVersionDB,
                list_fields=("character_ids", "audio_segment_ids"),
                dict_fields=("additional_data",),
            )
    except Exception as e:
        logger.error(f"创建场景版本失败: {e}")
        return None


# ==================== 章节 CRUD ====================

async def create_chapter(chapter_data: Dict[str, Any]) -> str:
    """创建章节
    
    Returns:
        str: 新创建的chapter UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            payload = dict(
                uuid=uuid,
                user_id=chapter_data["user_id"],
                conversation_id=chapter_data["conversation_id"],
                thread_id=chapter_data["thread_id"],
                run_id=chapter_data["run_id"],
                story_outline_id=chapter_data["story_outline_id"],
                title=chapter_data["title"],
                description=chapter_data["description"],
                duration=chapter_data["duration"],
                order=chapter_data["order"],
                audio_segment_ids=chapter_data.get("audio_segment_ids"),
                additional_data=chapter_data.get("additional_data"),
                created_at=created_at,
                updated_at=created_at,
            )
            if chapter_data.get("audio_section_uuid") is not None:
                payload["audio_section_uuid"] = chapter_data["audio_section_uuid"]
            result = await insert_and_return(conn, "video_chapters", **payload)
            return result['uuid']
    except Exception as e:
        logger.error(f"创建章节失败: {e}")
        raise


async def get_chapters_by_story_outline_id(story_outline_id: str) -> List[VideoChapterDB]:
    """根据故事大纲ID获取章节列表
    
    Returns:
        List[VideoChapterDB]: 按order排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_chapters 
                WHERE story_outline_id = $1 
                ORDER BY "order"
                """,
                story_outline_id
            )
            return [x for row in rows for x in [_row_to_chapter(row)] if x is not None]
    except Exception as e:
        logger.error(f"获取章节列表失败: {e}")
        return []


async def get_chapter_by_uuid(chapter_uuid: str) -> Optional[VideoChapterDB]:
    """根据UUID获取章节
    
    Returns:
        Optional[VideoChapterDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_chapters WHERE uuid = $1",
                chapter_uuid
            )
            return _row_to_chapter(row)
    except Exception as e:
        logger.error(f"获取章节失败: {e}")
        return None


async def get_chapters_by_uuids(chapter_uuids: List[str]) -> List[VideoChapterDB]:
    """批量根据UUID获取章节（避免 N+1，读路径 _row_to_chapter 防御 DB 多列）
    
    Returns:
        List[VideoChapterDB]: 按 order 排序
    """
    if not chapter_uuids:
        return []
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                'SELECT * FROM video_chapters WHERE uuid = ANY($1) ORDER BY "order"',
                chapter_uuids,
            )
            return [x for row in rows for x in [_row_to_chapter(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取章节失败: {e}")
        return []


async def create_video_chapter_version(
    chapter_id: str,
    version_number: int,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    story_outline_id: str,
    title: str,
    description: str,
    duration: float,
    order: int,
    audio_segment_ids: Optional[List[str]] = None,
    additional_data: Optional[Dict[str, Any]] = None,
) -> Optional[VideoChapterVersionDB]:
    """保存章节版本快照（Chapter 版本表，无外键）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            result = await insert_and_return(
                conn,
                "video_chapter_versions",
                uuid=uuid,
                chapter_id=chapter_id,
                version_number=version_number,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                story_outline_id=story_outline_id,
                title=title,
                description=description,
                duration=duration,
                order=order,
                audio_segment_ids=audio_segment_ids,
                additional_data=additional_data,
            )
            if not result:
                return None
            return row_to_struct_safe(
                result, VideoChapterVersionDB,
                list_fields=("audio_segment_ids",),
                dict_fields=("additional_data",),
            )
    except Exception as e:
        logger.error(f"创建章节版本失败: {e}")
        return None


async def update_chapter(chapter_uuid: str, chapter_data: Dict[str, Any]) -> bool:
    """更新章节记录（直接改主表，无版本表）。
    
    Returns:
        bool: 是否更新成功
    """
    try:
        if not chapter_data:
            return False
        payload = dict(chapter_data)
        payload["updated_at"] = now_utc()
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            query, values = build_update_query(
                table="video_chapters",
                data=payload,
                where={"uuid": chapter_uuid}
            )
            result = await conn.fetchrow(query, *values)
            return result is not None
    except Exception as e:
        logger.error(f"更新章节失败: {e}")
        return False


# ==================== 详细分镜 CRUD ====================

async def create_storyboard_detail(
    story_outline_id: str,
    total_duration: float,
    visual_style: str,
    shots_count: int,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    additional_data: Optional[Dict[str, Any]] = None
) -> str:
    """创建详细分镜记录
    
    Returns:
        str: 新创建的storyboard_detail UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            result = await insert_and_return(
                conn,
                "video_storyboard_details",
                uuid=uuid,
                user_id=user_id,
                story_outline_id=story_outline_id,
                total_duration_sec=total_duration,
                visual_style=visual_style,
                shots_count=shots_count,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建详细分镜失败: {e}")
        raise


async def get_storyboard_detail_by_uuid(storyboard_detail_uuid: str) -> Optional[VideoStoryboardDetailDB]:
    """根据UUID获取详细分镜
    
    Returns:
        Optional[VideoStoryboardDetailDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_storyboard_details WHERE uuid = $1",
                storyboard_detail_uuid
            )
            return _row_to_storyboard(row)
    except Exception as e:
        logger.error(f"获取详细分镜失败: {e}")
        return None


# ==================== 详细镜头 CRUD ====================

async def create_detailed_shot(
    story_outline_id: str,
    storyboard_detail_id: str,
    scene_id: str,
    shot_number: int,
    shot_type: str,
    camera_angle: str,
    duration: float,
    description: str,
    visual_notes: str,
    character_action: str,
    transition: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    character_ids: Optional[List[str]] = None,
    is_bridge: bool = False,
    audio_segment_ids: Optional[List[str]] = None,
    style_guide: Optional[str] = None,
    chapter_id: Optional[str] = None,
    narration: Optional[str] = None,
    additional_data: Optional[Dict[str, Any]] = None,
    generation_mode: Optional[str] = None,
    *,
    camera_position: Optional[str] = None,
    camera_movement: Optional[str] = None,
    lighting: Optional[str] = None,
    subject_angle: Optional[str] = None,
    subject_pose: Optional[str] = None,
    sound_effects: Optional[str] = None,
) -> str:
    """创建详细镜头记录（与 video_detailed_shots 表列一致：含镜头构图、技术执行等）
    
    Returns:
        str: 新创建的detailed_shot UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            # 处理空列表：转换为 None 让数据库使用默认值
            char_ids = character_ids if character_ids else None
            audio_ids = audio_segment_ids if audio_segment_ids else None
            
            # 与 PostgreSQL video_detailed_shots 表列一致：scene_description/visual_effects/dialogue，且表无 story_outline_id
            result = await insert_and_return(
                conn,
                "video_detailed_shots",
                uuid=uuid,
                user_id=user_id,
                storyboard_detail_id=storyboard_detail_id,
                scene_id=scene_id,
                chapter_id=chapter_id,
                shot_number=shot_number,
                duration_sec=duration,
                shot_type=shot_type,
                scene_description=description,
                visual_effects=visual_notes,
                transition=transition,
                dialogue=character_action,
                character_ids=char_ids,
                is_bridge=is_bridge,
                audio_segment_ids=audio_ids,
                style_guide=style_guide,
                narration=narration,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                camera_angle=camera_angle,
                camera_position=camera_position,
                camera_movement=camera_movement,
                lighting=lighting,
                subject_angle=subject_angle,
                subject_pose=subject_pose,
                sound_effects=sound_effects,
                additional_data=additional_data,
                generation_mode=generation_mode,
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建详细镜头失败: {e}")
        raise


async def get_detailed_shot_by_id(shot_id: str) -> Optional[VideoDetailedShotDB]:
    """根据ID获取详细镜头
    
    Returns:
        Optional[VideoDetailedShotDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_detailed_shots WHERE uuid = $1",
                shot_id
            )
            return _row_to_shot(row)
    except Exception as e:
        logger.error(f"获取详细镜头失败: {e}")
        return None


async def get_detailed_shots_by_storyboard_id(storyboard_detail_id: str) -> List[VideoDetailedShotDB]:
    """根据分镜ID获取详细镜头列表
    
    Returns:
        List[VideoDetailedShotDB]: 按shot_number排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_detailed_shots 
                WHERE storyboard_detail_id = $1 
                ORDER BY shot_number
                """,
                storyboard_detail_id
            )
            return [x for row in rows for x in [_row_to_shot(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据分镜ID获取详细镜头失败: {e}")
        return []


async def get_detailed_shots_by_scene_id(scene_id: str) -> List[VideoDetailedShotDB]:
    """根据场景ID获取详细镜头列表
    
    Returns:
        List[VideoDetailedShotDB]: 按shot_number排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_detailed_shots 
                WHERE scene_id = $1 
                ORDER BY shot_number
                """,
                scene_id
            )
            return [x for row in rows for x in [_row_to_shot(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据场景ID获取详细镜头失败: {e}")
        return []


async def get_detailed_shots_by_uuids(shot_uuids: List[str]) -> List[VideoDetailedShotDB]:
    """批量获取详细镜头 - 避免N+1
    
    Returns:
        List[VideoDetailedShotDB]: msgspec对象列表
    """
    try:
        if not shot_uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_detailed_shots 
                WHERE uuid = ANY($1)
                ORDER BY shot_number
                """,
                shot_uuids
            )
            return [x for row in rows for x in [_row_to_shot(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取详细镜头失败: {e}")
        return []


async def get_detailed_shots_by_conversation(conversation_id: str, thread_id: str) -> List[VideoDetailedShotDB]:
    """根据 conversation_id 与 thread_id 获取该 thread 下所有详细镜头（进行中任务也能看到已产生的镜头；读路径 _row_to_shot 防御 DB 多列）
    
    Returns:
        List[VideoDetailedShotDB]: 按 shot_number 排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_detailed_shots
                WHERE conversation_id = $1 AND thread_id = $2
                ORDER BY shot_number
                """,
                conversation_id,
                thread_id,
            )
            return [x for row in rows for x in [_row_to_shot(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据 conversation/thread 获取详细镜头失败: {e}")
        return []


async def update_shot_scene_description(shot_uuid: str, scene_description: str) -> bool:
    """更新详细镜头的 scene_description
    
    Args:
        shot_uuid: 镜头UUID
        scene_description: 新的场景描述
        
    Returns:
        bool: 是否更新成功
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_detailed_shots
                SET scene_description = $1, updated_at = $2
                WHERE uuid = $3
                """,
                scene_description,
                now_utc(),
                shot_uuid
            )
            return True
    except Exception as e:
        logger.error(f"更新镜头描述失败: {e}")
        return False


async def update_detailed_shots_description_by_scene_id(scene_uuid: str, scene_description: str) -> int:
    """当场景 description 更新时，同步更新该场景下所有详细镜头（video_detailed_shots）的 scene_description，供关键帧生成使用。

    Args:
        scene_uuid: 场景 UUID（video_scenes.uuid）
        scene_description: 新的场景描述（与 scene 表一致）

    Returns:
        int: 更新的镜头数量
    """
    try:
        shots = await get_detailed_shots_by_scene_id(scene_uuid)
        if not shots:
            return 0
        count = 0
        for shot in shots:
            ok = await update_shot_scene_description(shot.uuid, scene_description)
            if ok:
                count += 1
        if count:
            logger.info(f"场景 {scene_uuid} 描述已同步到 {count} 个详细镜头")
        return count
    except Exception as e:
        logger.error(f"按场景同步镜头 scene_description 失败: {e}")
        return 0


async def update_detailed_shot_generation_routing(shot_uuid: str, routing: Dict[str, Any]) -> bool:
    """写入 video_detailed_shots.generation_routing（JSONB）。读路径经 _row_to_shot + row_to_struct_safe，列缺失时旧行无该键为 None。"""
    if not shot_uuid or not routing:
        return False
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_detailed_shots
                SET generation_routing = $1, updated_at = $2
                WHERE uuid = $3
                """,
                to_json(routing),
                now_utc(),
                shot_uuid,
            )
        return True
    except Exception as e:
        logger.error(f"update_detailed_shot_generation_routing 失败: {e}")
        return False


async def merge_detailed_shot_additional_data(shot_uuid: str, patch: Dict[str, Any]) -> bool:
    """浅合并 patch 到 video_detailed_shots.additional_data（JSON），用于 per-shot 路由等扩展字段。"""
    if not shot_uuid or not patch:
        return False
    try:
        row = await get_detailed_shot_by_id(shot_uuid)
        if not row:
            return False
        base = row.additional_data if isinstance(row.additional_data, dict) else {}
        merged = {**base, **patch}
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_detailed_shots
                SET additional_data = $1, updated_at = $2
                WHERE uuid = $3
                """,
                to_json(merged),
                now_utc(),
                shot_uuid,
            )
        return True
    except Exception as e:
        logger.error(f"merge_detailed_shot_additional_data 失败: {e}")
        return False


async def update_detailed_shot_duration(shot_uuid: str, duration: float) -> bool:
    """更新详细镜头时长（TTS 旁白实测后写回）。"""
    if not shot_uuid or duration <= 0:
        return False
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_detailed_shots
                SET duration = $1, updated_at = $2
                WHERE uuid = $3
                """,
                round(float(duration), 3),
                now_utc(),
                shot_uuid,
            )
            return True
    except Exception as e:
        logger.error(f"更新镜头 duration 失败: {e}")
        return False


async def update_shot_generation_mode(shot_uuid: str, generation_mode: str) -> bool:
    """更新详细镜头的 generation_mode（normal | lipsync | empty_shot）

    仅更新指定列，表新增列不影响本函数。不读 row，无需 row_to_struct_safe。

    Args:
        shot_uuid: 镜头UUID
        generation_mode: 新的生成模式

    Returns:
        bool: 是否更新成功
    """
    if not shot_uuid:
        logger.warning("update_shot_generation_mode: shot_uuid 为空，跳过")
        return False
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_detailed_shots
                SET generation_mode = $1, updated_at = $2
                WHERE uuid = $3
                """,
                generation_mode,
                now_utc(),
                shot_uuid
            )
            return True
    except Exception as e:
        logger.error(f"更新镜头 generation_mode 失败: {e}")
        return False


async def update_detailed_shot_first_frame_revision(
    shot_uuid: str,
    *,
    scene_description: Optional[str] = None,
    camera_movement: Optional[str] = None,
    shot_type: Optional[str] = None,
    subject_angle: Optional[str] = None,
    subject_pose: Optional[str] = None,
    generation_mode: Optional[str] = None,
) -> bool:
    """更新详细镜头的首帧合规修订字段（仅更新与首帧违规相关的列，及 revised_generation_mode 写回）

    Args:
        shot_uuid: 镜头UUID
        scene_description: 修订后的画面描述（可选）
        camera_movement: 修订后的镜头运动（可选）
        shot_type: 修订后的景别（可选）
        subject_angle: 修订后的主体角度（可选）
        subject_pose: 修订后的主体姿势（可选）
        generation_mode: 修订后的生成模式 normal/lipsync/empty_shot（可选）

    Returns:
        bool: 是否更新成功
    """
    if not shot_uuid:
        logger.warning("update_detailed_shot_first_frame_revision: shot_uuid 为空，跳过")
        return False
    updates = []
    values = []
    idx = 1
    if scene_description is not None:
        updates.append(f"scene_description = ${idx}")
        values.append(scene_description)
        idx += 1
    if camera_movement is not None:
        updates.append(f"camera_movement = ${idx}")
        values.append(camera_movement)
        idx += 1
    if shot_type is not None:
        updates.append(f"shot_type = ${idx}")
        values.append(shot_type)
        idx += 1
    if subject_angle is not None:
        updates.append(f"subject_angle = ${idx}")
        values.append(subject_angle)
        idx += 1
    if subject_pose is not None:
        updates.append(f"subject_pose = ${idx}")
        values.append(subject_pose)
        idx += 1
    if generation_mode is not None:
        updates.append(f"generation_mode = ${idx}")
        values.append(generation_mode)
        idx += 1
    if not updates:
        return True
    updates.append("updated_at = $%d" % idx)
    values.append(now_utc())
    idx += 1
    where_uuid_placeholder = "$%d" % idx
    values.append(shot_uuid)
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                "UPDATE video_detailed_shots SET " + ", ".join(updates) + " WHERE uuid = " + where_uuid_placeholder,
                *values
            )
            return True
    except Exception as e:
        logger.error(f"更新镜头首帧修订字段失败: {e}")
        return False


# ==================== 批量查询优化函数 ====================

async def batch_get_scenes_with_shots(
    scene_uuids: List[str]
) -> Dict[str, Dict[str, Any]]:
    """批量获取场景 + 详细镜头 - 避免N+1
    
    Args:
        scene_uuids: 场景UUID列表
        
    Returns:
        Dict: 以scene_uuid为key的字典
        {
            'uuid1': {
                'scene': VideoSceneDB,
                'shots': [VideoDetailedShotDB, ...]
            },
            ...
        }
    """
    try:
        if not scene_uuids:
            return {}
        
        # 1. 批量获取场景
        scenes = await get_scenes_by_uuids(scene_uuids)
        
        # 2. 批量获取所有镜头
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            shot_rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_detailed_shots
                WHERE scene_id = ANY($1)
                ORDER BY scene_id, shot_number
                """,
                scene_uuids
            )
        
        # 3. 按scene_id分组
        shots_by_scene = {}
        for row in shot_rows:
            scene_id = row['scene_id']
            if scene_id not in shots_by_scene:
                shots_by_scene[scene_id] = []
            shot_obj = _row_to_shot(row)
            if shot_obj is not None:
                shots_by_scene[scene_id].append(shot_obj)
        
        # 4. 组装结果
        results = {}
        for scene in scenes:
            results[scene.uuid] = {
                'scene': scene,
                'shots': shots_by_scene.get(scene.uuid, [])
            }
        
        return results
    except Exception as e:
        logger.error(f"批量获取场景+镜头失败: {e}")
        return {}
