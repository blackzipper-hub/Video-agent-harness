"""
视频音频CRUD操作 - 使用msgspec返回类型

包含：
- 音频转录 (video_audio_transcription)
- 音频片段 (video_audio_segment)  
- 音效 (video_audio_effects + versions)
- 背景音乐 (video_music_generations + versions)
- 旁白 (video_narrations + versions)
"""

import logging
from typing import List, Optional, Dict, Any, Union

from ...models.database import get_asyncpg_pool
from ...models.image_result import MusicProvider
from ...utils.asyncpg_utils import (
    fetch_one, fetch_all, fetch_val, execute,
    insert_and_return, generate_uuid, now_utc, to_json, from_json
)
from ...schemas.video.video_audio import (
    VideoAudioTranscriptionDB,
    VideoAudioSegmentDB,
    VideoAudioSectionDB,
    VideoAudioEffectDB,
    VideoAudioEffectVersionDB,
    VideoMusicGenerationDB,
    VideoMusicGenerationVersionDB,
    VideoNarrationDB,
    VideoNarrationVersionDB
)
from ...utils import media_service_client as msc

logger = logging.getLogger(__name__)


# ==================== Helper Functions ====================

from .row_normalize import row_to_struct_safe

_AUDIO_SEGMENT_DICT_FIELDS = ("additional_data",)
_NARRATION_DICT_FIELDS = ("additional_data",)
_NARRATION_VERSION_DICT_FIELDS = ("additional_data",)
_AUDIO_EFFECT_DICT_FIELDS = ("additional_data",)
_AUDIO_EFFECT_VERSION_DICT_FIELDS = ("additional_data",)
_MUSIC_GENERATION_DICT_FIELDS = ("additional_data",)


def _row_to_audio_segment(row: Optional[Dict]) -> Optional[VideoAudioSegmentDB]:
    """将数据库行转换为VideoAudioSegmentDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, VideoAudioSegmentDB, dict_fields=_AUDIO_SEGMENT_DICT_FIELDS)


def _rows_to_audio_segments(rows: List[Dict]) -> List[VideoAudioSegmentDB]:
    """批量转换音频片段"""
    return [x for row in rows for x in [_row_to_audio_segment(row)] if x is not None]


def _row_to_narration(row: Optional[Dict]) -> Optional[VideoNarrationDB]:
    """将数据库行转换为VideoNarrationDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, VideoNarrationDB, dict_fields=_NARRATION_DICT_FIELDS)


def _row_to_narration_version(row: Optional[Dict]) -> Optional[VideoNarrationVersionDB]:
    """将数据库行转换为VideoNarrationVersionDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, VideoNarrationVersionDB, dict_fields=_NARRATION_VERSION_DICT_FIELDS)


def _rows_to_narration_versions(rows: List[Dict]) -> List[VideoNarrationVersionDB]:
    """批量转换旁白版本"""
    return [x for row in rows for x in [_row_to_narration_version(row)] if x is not None]


def _row_to_audio_effect(row: Optional[Dict]) -> Optional[VideoAudioEffectDB]:
    """将数据库行转换为VideoAudioEffectDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, VideoAudioEffectDB, dict_fields=_AUDIO_EFFECT_DICT_FIELDS)


def _row_to_audio_effect_version(row: Optional[Dict]) -> Optional[VideoAudioEffectVersionDB]:
    """将数据库行转换为VideoAudioEffectVersionDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, VideoAudioEffectVersionDB, dict_fields=_AUDIO_EFFECT_VERSION_DICT_FIELDS)


def _rows_to_audio_effect_versions(rows: List[Dict]) -> List[VideoAudioEffectVersionDB]:
    """批量转换音效版本"""
    return [_row_to_audio_effect_version(row) for row in rows if _row_to_audio_effect_version(row) is not None]


_MUSIC_VERSION_LIST_FIELDS = ("audio_segment_ids",)
_MUSIC_VERSION_DICT_FIELDS = ("params", "additional_data")
_TRANSCRIPTION_DICT_FIELDS = ("additional_data",)
_AUDIO_SECTION_DICT_FIELDS = ("additional_data",)


def _row_to_audio_transcription(row: Optional[Dict]) -> Optional[VideoAudioTranscriptionDB]:
    """将数据库行转换为VideoAudioTranscriptionDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, VideoAudioTranscriptionDB, dict_fields=_TRANSCRIPTION_DICT_FIELDS)


def _row_to_audio_section(row: Optional[Dict]) -> Optional[VideoAudioSectionDB]:
    """将数据库行转换为 VideoAudioSectionDB 对象"""
    return row_to_struct_safe(row, VideoAudioSectionDB, dict_fields=_AUDIO_SECTION_DICT_FIELDS)


def _row_to_music_generation(row: Optional[Dict]) -> Optional[VideoMusicGenerationDB]:
    """将数据库行转换为VideoMusicGenerationDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, VideoMusicGenerationDB, dict_fields=_MUSIC_GENERATION_DICT_FIELDS)


def _row_to_music_generation_version(row: Optional[Dict]) -> Optional[VideoMusicGenerationVersionDB]:
    """将数据库行转换为VideoMusicGenerationVersionDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoMusicGenerationVersionDB,
        list_fields=_MUSIC_VERSION_LIST_FIELDS, dict_fields=_MUSIC_VERSION_DICT_FIELDS
    )


def _rows_to_music_generation_versions(rows: List[Dict]) -> List[VideoMusicGenerationVersionDB]:
    """批量转换音乐版本"""
    return [x for row in rows for x in [_row_to_music_generation_version(row)] if x is not None]


# ==================== 音频转录 CRUD ====================

async def create_video_audio_transcription(
    run_id: str,
    task: str,
    language: str,
    duration: float,
    text: str,
    audio_url: str,
    filename: str,
    is_instrumental: bool,
    user_id: str = "",
    conversation_id: str = "",
    thread_id: str = "",
    additional_data: Optional[Dict[str, Any]] = None,
    song_name: Optional[str] = None,
    global_bpm: Optional[float] = None,
    genre: Optional[str] = None,
    global_emotion: Optional[str] = None,
    suggested_global_theme: Optional[str] = None,
    suggested_color_palette: Optional[str] = None,
) -> VideoAudioTranscriptionDB:
    """创建音频转录结果
    
    Returns:
        VideoAudioTranscriptionDB: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            payload = dict(
                uuid=uuid,
                run_id=run_id,
                task=task,
                language=language,
                duration=duration,
                text=text,
                audio_url=audio_url,
                filename=filename,
                is_instrumental=is_instrumental,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at,
            )
            if song_name is not None:
                payload["song_name"] = song_name
            if global_bpm is not None:
                payload["global_bpm"] = global_bpm
            if genre is not None:
                payload["genre"] = genre
            if global_emotion is not None:
                payload["global_emotion"] = global_emotion
            if suggested_global_theme is not None:
                payload["suggested_global_theme"] = suggested_global_theme
            if suggested_color_palette is not None:
                payload["suggested_color_palette"] = suggested_color_palette
            result = await insert_and_return(conn, "video_audio_transcription", **payload)
            return _row_to_audio_transcription(result)
    except Exception as e:
        logger.error(f"创建音频转录结果失败: {e}")
        raise


async def get_video_audio_transcription_by_uuid(uuid: str) -> Optional[VideoAudioTranscriptionDB]:
    """根据UUID获取音频转录结果
    
    Returns:
        Optional[VideoAudioTranscriptionDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_audio_transcription WHERE uuid = $1",
                uuid
            )
            return _row_to_audio_transcription(row)
    except Exception as e:
        logger.error(f"根据UUID获取音频转录结果失败: {e}")
        return None


async def get_video_audio_transcriptions_by_uuids(
    transcription_uuids: List[str],
) -> Dict[str, VideoAudioTranscriptionDB]:
    """批量根据UUID获取音频转录（避免 N+1；读路径 _row_to_audio_transcription 防御 DB 多列）"""
    if not transcription_uuids:
        return {}
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_audio_transcription WHERE uuid = ANY($1)",
                transcription_uuids,
            )
            out = {}
            for row in rows:
                t = _row_to_audio_transcription(row)
                if t and getattr(t, "uuid", None):
                    out[t.uuid] = t
            return out
    except Exception as e:
        logger.error(f"批量获取音频转录失败: {e}")
        return {}


async def get_video_audio_transcription_by_run_id(run_id: str) -> Optional[VideoAudioTranscriptionDB]:
    """根据run_id获取音频转录结果（通常一个run_id对应一个音频转录）
    
    Returns:
        Optional[VideoAudioTranscriptionDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                """
                SELECT * FROM video_audio_transcription 
                WHERE run_id = $1 
                ORDER BY created_at DESC 
                LIMIT 1
                """,
                run_id
            )
            return _row_to_audio_transcription(row)
    except Exception as e:
        logger.error(f"根据run_id获取音频转录结果失败: {e}")
        return None


async def get_video_audio_transcription_by_thread_id(thread_id: str) -> Optional[VideoAudioTranscriptionDB]:
    """根据 thread_id 获取该线程下最新的音频转录（用于按 thread 同步/组装；读路径 _row_to_audio_transcription 防御 DB 多列）
    
    Returns:
        Optional[VideoAudioTranscriptionDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                """
                SELECT * FROM video_audio_transcription
                WHERE thread_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                thread_id
            )
            return _row_to_audio_transcription(row)
    except Exception as e:
        logger.error(f"根据thread_id获取音频转录结果失败: {e}")
        return None


# ==================== 音频片段 CRUD ====================

async def create_video_audio_segment(
    run_id: str,
    transcription_uuid: str,
    segment_id: int,
    start: float,
    end: float,
    duration: float,
    text: str,
    user_id: str = "",
    conversation_id: str = "",
    thread_id: str = "",
    emotion: Optional[str] = None,
    tempo: Optional[str] = None,
    additional_data: Optional[Dict[str, Any]] = None,
    vocal_presence: Optional[bool] = None,
    vocal_gender: Optional[str] = None,
) -> VideoAudioSegmentDB:
    """创建音频片段
    
    Returns:
        VideoAudioSegmentDB: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            payload = dict(
                uuid=uuid,
                run_id=run_id,
                transcription_uuid=transcription_uuid,
                segment_id=segment_id,
                start=start,
                end=end,
                duration=duration,
                text=text,
                emotion=emotion,
                tempo=tempo,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at,
            )
            if vocal_presence is not None:
                payload["vocal_presence"] = vocal_presence
            if vocal_gender is not None:
                payload["vocal_gender"] = vocal_gender
            result = await insert_and_return(
                conn,
                "video_audio_segment",
                **payload,
            )
            out = row_to_struct_safe(result, VideoAudioSegmentDB, dict_fields=_AUDIO_SEGMENT_DICT_FIELDS)
            if out is None:
                raise ValueError("创建音频片段后转换 Struct 失败")
            return out
    except Exception as e:
        logger.error(f"创建音频片段失败: {e}")
        raise


async def get_video_audio_segment_by_transcription_uuid(transcription_uuid: str) -> List[VideoAudioSegmentDB]:
    """根据转录UUID获取所有音频片段
    
    Returns:
        List[VideoAudioSegmentDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_audio_segment 
                WHERE transcription_uuid = $1 
                ORDER BY segment_id
                """,
                transcription_uuid
            )
            return _rows_to_audio_segments(rows)
    except Exception as e:
        logger.error(f"根据转录UUID获取音频片段失败: {e}")
        return []


async def get_audio_segments_by_uuids(segment_uuids: List[str]) -> List[VideoAudioSegmentDB]:
    """批量获取音频片段
    
    Returns:
        List[VideoAudioSegmentDB]: msgspec对象列表
    """
    try:
        if not segment_uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_audio_segment WHERE uuid = ANY($1)",
                segment_uuids
            )
            return _rows_to_audio_segments(rows)
    except Exception as e:
        logger.error(f"批量获取音频片段失败: {e}")
        return []


async def update_video_audio_segment_duration(segment_uuid: str, duration: float) -> None:
    """将音频片段的 duration 更新为音乐生成版本的实际时长（与 music_version.duration 一致，供下游 scene/trim 等使用）。"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                "UPDATE video_audio_segment SET duration = $1, updated_at = $2 WHERE uuid = $3",
                duration,
                now_utc(),
                segment_uuid,
            )
    except Exception as e:
        logger.error(f"更新音频片段 duration 失败: {e}")
        raise


# ==================== 音频段落 Section CRUD ====================

async def create_video_audio_section(
    transcription_uuid: str,
    section_type: str,
    start_time: float,
    end_time: float,
    user_id: str = "",
    conversation_id: str = "",
    thread_id: str = "",
    run_id: str = "",
    musical_features: Optional[str] = None,
    section_emotion: Optional[str] = None,
    suggested_visual_intensity: Optional[str] = None,
    suggested_rhythmic_strategy: Optional[str] = None,
    suggested_visual_theme: Optional[str] = None,
    suggested_context: Optional[str] = None,
    additional_data: Optional[Dict[str, Any]] = None,
) -> VideoAudioSectionDB:
    """创建音频段落（MusicSection）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            result = await insert_and_return(
                conn,
                "video_audio_section",
                uuid=uuid,
                user_id=user_id or None,
                conversation_id=conversation_id or None,
                thread_id=thread_id or None,
                run_id=run_id or None,
                created_at=created_at,
                updated_at=created_at,
                transcription_uuid=transcription_uuid,
                section_type=section_type,
                start_time=start_time,
                end_time=end_time,
                musical_features=musical_features,
                section_emotion=section_emotion,
                suggested_visual_intensity=suggested_visual_intensity,
                suggested_rhythmic_strategy=suggested_rhythmic_strategy,
                suggested_visual_theme=suggested_visual_theme,
                suggested_context=suggested_context,
                additional_data=additional_data,
            )
            out = _row_to_audio_section(result)
            if out is None:
                raise ValueError("创建 audio section 后转换 Struct 失败")
            return out
    except Exception as e:
        logger.error(f"创建音频段落失败: {e}")
        raise


async def get_video_audio_sections_by_transcription_uuid(transcription_uuid: str) -> List[VideoAudioSectionDB]:
    """根据转录 UUID 获取所有段落，按 start_time 排序"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_audio_section WHERE transcription_uuid = $1 ORDER BY start_time",
                transcription_uuid,
            )
            return [x for row in rows for x in [_row_to_audio_section(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据 transcription_uuid 获取 audio sections 失败: {e}")
        return []


async def get_video_audio_section_by_uuid(section_uuid: str) -> Optional[VideoAudioSectionDB]:
    """根据 UUID 获取单个音频段落"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_audio_section WHERE uuid = $1",
                section_uuid,
            )
            return _row_to_audio_section(row)
    except Exception as e:
        logger.error(f"根据 uuid 获取 audio section 失败: {e}")
        return None


# ==================== 旁白 CRUD ====================

async def create_narration(
    run_id: str,
    audio_segment_id: str,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    additional_data: Optional[Dict[str, Any]] = None
) -> str:
    """创建旁白记录
    
    Returns:
        str: 新创建的narration UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            result = await insert_and_return(
                conn,
                "video_narrations",
                uuid=uuid,
                run_id=run_id,
                audio_segment_id=audio_segment_id,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                current_version_index=0,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建旁白失败: {e}")
        raise


async def create_narration_version(
    narration_id: str,
    version_number: int,
    narration_url: str,
    provider: str,
    model: str,
    voice: str,
    text: str,
    duration: float,
    success: bool,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    error_msg: Optional[str] = None,
    raw_error_msg: Optional[str] = None,
    additional_data: Optional[Dict[str, Any]] = None
) -> str:
    """创建旁白版本
    
    Returns:
        str: 新创建的版本UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            result = await insert_and_return(
                conn,
                "video_narration_versions",
                uuid=uuid,
                narration_id=narration_id,
                version_number=version_number,
                narration_url=narration_url,
                provider=provider,
                model=model,
                voice=voice,
                text=text,
                duration=duration,
                success=success,
                error_msg=error_msg,
                raw_error_msg=raw_error_msg,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建旁白版本失败: {e}")
        raise


async def get_narration_by_uuid(narration_uuid: str) -> Optional[VideoNarrationDB]:
    """根据UUID获取旁白
    
    Returns:
        Optional[VideoNarrationDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_narrations WHERE uuid = $1",
                narration_uuid
            )
            return _row_to_narration(row)
    except Exception as e:
        logger.error(f"根据UUID获取旁白失败: {e}")
        return None


async def get_narrations_by_uuids(narration_uuids: List[str]) -> List[VideoNarrationDB]:
    """批量获取旁白 - 避免N+1
    
    Returns:
        List[VideoNarrationDB]: msgspec对象列表
    """
    try:
        if not narration_uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_narrations WHERE uuid = ANY($1)",
                narration_uuids
            )
            return [x for row in rows for x in [_row_to_narration(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取旁白失败: {e}")
        return []


async def get_narrations_by_run_id(run_id: str) -> List[VideoNarrationDB]:
    """根据run_id获取旁白列表
    
    Returns:
        List[VideoNarrationDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_narrations WHERE run_id = $1",
                run_id
            )
            return [x for row in rows for x in [_row_to_narration(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据run_id获取旁白列表失败: {e}")
        return []


async def get_narrations_by_thread_id(thread_id: str) -> List[VideoNarrationDB]:
    """根据thread_id获取旁白列表（跨所有 run）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_narrations WHERE thread_id = $1",
                thread_id
            )
            return [x for row in rows for x in [_row_to_narration(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据thread_id获取旁白列表失败: {e}")
        return []


async def get_narrations_by_conversation(
    conversation_id: str,
    thread_id: str
) -> List[VideoNarrationDB]:
    """根据对话ID和线程ID获取旁白列表
    
    Returns:
        List[VideoNarrationDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_narrations 
                WHERE conversation_id = $1 AND thread_id = $2
                """,
                conversation_id, thread_id
            )
            return [x for row in rows for x in [_row_to_narration(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据对话获取旁白列表失败: {e}")
        return []


async def get_narration_versions_by_narration_ids(narration_ids: List[str]) -> List[VideoNarrationVersionDB]:
    """批量获取旁白版本 - 避免N+1
    
    Returns:
        List[VideoNarrationVersionDB]: msgspec对象列表
    """
    try:
        if not narration_ids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_narration_versions 
                WHERE narration_id = ANY($1)
                ORDER BY narration_id, version_number
                """,
                narration_ids
            )
            return _rows_to_narration_versions(rows)
    except Exception as e:
        logger.error(f"批量获取旁白版本失败: {e}")
        return []


# ==================== 音效 CRUD ====================

async def create_audio_effect(
    run_id: str,
    audio_segment_id: str,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    additional_data: Optional[Dict[str, Any]] = None
) -> str:
    """创建音效记录
    
    Returns:
        str: 新创建的audio_effect UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            result = await insert_and_return(
                conn,
                "video_audio_effects",
                uuid=uuid,
                run_id=run_id,
                audio_segment_id=audio_segment_id,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                current_version_index=0,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建音效失败: {e}")
        raise


async def create_audio_effect_version(
    audio_effect_id: str,
    version_number: int,
    audio_effect_url: str,
    provider: str,
    prompt: str,
    duration: float,
    success: bool,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    error_msg: Optional[str] = None,
    raw_error_msg: Optional[str] = None,
    model: Optional[str] = None,
    additional_data: Optional[Dict[str, Any]] = None
) -> str:
    """创建音效版本
    
    Returns:
        str: 新创建的版本UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            result = await insert_and_return(
                conn,
                "video_audio_effect_versions",
                uuid=uuid,
                audio_effect_id=audio_effect_id,
                version_number=version_number,
                audio_effect_url=audio_effect_url,
                provider=provider,
                prompt=prompt,
                duration=duration,
                success=success,
                error_msg=error_msg,
                raw_error_msg=raw_error_msg,
                model=model,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建音效版本失败: {e}")
        raise


async def get_audio_effect_by_uuid(audio_effect_uuid: str) -> Optional[VideoAudioEffectDB]:
    """根据UUID获取音效
    
    Returns:
        Optional[VideoAudioEffectDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_audio_effects WHERE uuid = $1",
                audio_effect_uuid
            )
            return _row_to_audio_effect(row)
    except Exception as e:
        logger.error(f"根据UUID获取音效失败: {e}")
        return None


async def get_audio_effects_by_uuids(audio_effect_uuids: List[str]) -> List[VideoAudioEffectDB]:
    """批量获取音效 - 避免N+1
    
    Returns:
        List[VideoAudioEffectDB]: msgspec对象列表
    """
    try:
        if not audio_effect_uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_audio_effects WHERE uuid = ANY($1)",
                audio_effect_uuids
            )
            return [x for row in rows for x in [_row_to_audio_effect(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取音效失败: {e}")
        return []


async def get_audio_effects_by_run_id(run_id: str) -> List[VideoAudioEffectDB]:
    """根据run_id获取音效列表
    
    Returns:
        List[VideoAudioEffectDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_audio_effects WHERE run_id = $1",
                run_id
            )
            return [x for row in rows for x in [_row_to_audio_effect(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据run_id获取音效列表失败: {e}")
        return []


async def get_audio_effects_by_conversation(conversation_id: str, thread_id: str) -> List[VideoAudioEffectDB]:
    """根据对话ID和线程ID获取音效列表
    
    Returns:
        List[VideoAudioEffectDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_audio_effects 
                WHERE conversation_id = $1 AND thread_id = $2
                """,
                conversation_id, thread_id
            )
            return [x for row in rows for x in [_row_to_audio_effect(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据对话获取音效列表失败: {e}")
        return []


async def get_audio_effect_versions_by_audio_effect_ids(audio_effect_ids: List[str]) -> List[VideoAudioEffectVersionDB]:
    """批量获取音效版本 - 避免N+1
    
    Returns:
        List[VideoAudioEffectVersionDB]: msgspec对象列表
    """
    try:
        if not audio_effect_ids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_audio_effect_versions 
                WHERE audio_effect_id = ANY($1)
                ORDER BY audio_effect_id, version_number
                """,
                audio_effect_ids
            )
            return _rows_to_audio_effect_versions(rows)
    except Exception as e:
        logger.error(f"批量获取音效版本失败: {e}")
        return []


# ==================== 背景音乐 CRUD ====================

async def create_music_generation(
    run_id: str,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    *,
    is_full_story_music: bool = False,
    is_instrumental: bool = True,
    story_outline_id: Optional[str] = None,
    scene_id: Optional[str] = None,
    shot_number: Optional[int] = None,
    additional_data: Optional[Dict[str, Any]] = None
) -> str:
    """创建背景音乐记录

    is_full_story_music: True 表示整段故事背景音乐（如 Suno BGM），用于后续合成时取 music_url；
        False 表示按片段/上传的音乐，合成时取 original_audio_url。
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()

            result = await insert_and_return(
                conn,
                "video_music_generations",
                uuid=uuid,
                run_id=run_id,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                is_full_story_music=is_full_story_music,
                is_instrumental=is_instrumental,
                story_outline_id=story_outline_id,
                scene_id=scene_id,
                shot_number=shot_number,
                current_version_index=0,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建背景音乐失败: {e}")
        raise


def _normalize_music_provider(provider: Union[str, MusicProvider]) -> str:
    """统一为 DB 存储的字符串（如 suno）。"""
    if hasattr(provider, "value"):
        return provider.value
    return str(provider)


async def create_music_generation_version(
    music_generation_id: str,
    version_number: int,
    music_url: str,
    provider: Union[str, MusicProvider],
    model: str,
    prompt: str,
    duration: float,
    success: bool,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    error_msg: Optional[str] = None,
    raw_error_msg: Optional[str] = None,
    style: Optional[str] = None,
    mood: Optional[str] = None,
    tags: Optional[List[str]] = None,
    additional_data: Optional[Dict[str, Any]] = None,
    *,
    is_instrumental: bool = True,
    original_audio_url: Optional[str] = None,
) -> str:
    """创建背景音乐版本

    provider: 使用 MusicProvider 枚举（如 MusicProvider.SUNO）或字符串。
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            provider_str = _normalize_music_provider(provider)

            # 与 dev 分支 models/video/video_generation.py 表 video_music_generation_versions 列一致：music_prompt（无 prompt 列）；model/style/mood/tags 放入 params
            params_dict = {}
            if model is not None:
                params_dict["model"] = model
            if style is not None:
                params_dict["style"] = style
            if mood is not None:
                params_dict["mood"] = mood
            if tags is not None:
                params_dict["tags"] = tags
            params_to_save = params_dict if params_dict else None
            result = await insert_and_return(
                conn,
                "video_music_generation_versions",
                uuid=uuid,
                music_generation_id=music_generation_id,
                version_number=version_number,
                music_url=music_url,
                provider=provider_str,
                music_prompt=prompt,
                duration=duration,
                is_instrumental=is_instrumental,
                success=success,
                error_msg=error_msg,
                raw_error_msg=raw_error_msg,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                params=params_to_save,
                additional_data=additional_data,
                original_audio_url=original_audio_url,
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建背景音乐版本失败: {e}")
        raise


async def get_music_generation_by_uuid(uuid: str) -> Optional[VideoMusicGenerationDB]:
    """根据UUID获取背景音乐
    
    Returns:
        Optional[VideoMusicGenerationDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_music_generations WHERE uuid = $1",
                uuid
            )
            return _row_to_music_generation(row)
    except Exception as e:
        logger.error(f"根据UUID获取背景音乐失败: {e}")
        return None


async def get_music_generations_by_uuids(uuids: List[str]) -> List[VideoMusicGenerationDB]:
    """批量获取背景音乐 - 避免N+1
    
    Returns:
        List[VideoMusicGenerationDB]: msgspec对象列表
    """
    try:
        if not uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_music_generations WHERE uuid = ANY($1)",
                uuids
            )
            return [x for row in rows for x in [_row_to_music_generation(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取背景音乐失败: {e}")
        return []


async def get_music_generation_by_run_id(run_id: str) -> Optional[VideoMusicGenerationDB]:
    """根据run_id获取背景音乐
    
    Returns:
        Optional[VideoMusicGenerationDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_music_generations WHERE run_id = $1",
                run_id
            )
            return row_to_struct_safe(row, VideoMusicGenerationDB, dict_fields=_MUSIC_GENERATION_DICT_FIELDS)
    except Exception as e:
        logger.error(f"根据run_id获取背景音乐失败: {e}")
        return None


async def get_music_generations_by_run_id(run_id: str) -> List[VideoMusicGenerationDB]:
    """根据run_id获取背景音乐列表（支持多个）
    
    Returns:
        List[VideoMusicGenerationDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_music_generations WHERE run_id = $1 ORDER BY shot_number ASC NULLS LAST",
                run_id
            )
            return [x for row in rows for x in [_row_to_music_generation(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据run_id获取背景音乐列表失败: {e}")
        return []


async def get_music_generations_by_thread_id(thread_id: str) -> List[VideoMusicGenerationDB]:
    """根据thread_id获取背景音乐列表（跨所有 run）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_music_generations WHERE thread_id = $1 ORDER BY shot_number ASC NULLS LAST",
                thread_id
            )
            return [x for row in rows for x in [_row_to_music_generation(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据thread_id获取背景音乐列表失败: {e}")
        return []


async def get_music_generation_by_conversation_id(conversation_id: str) -> Optional[VideoMusicGenerationDB]:
    """根据conversation_id获取背景音乐
    
    Returns:
        Optional[VideoMusicGenerationDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_music_generations WHERE conversation_id = $1",
                conversation_id
            )
            return _row_to_music_generation(row)
    except Exception as e:
        logger.error(f"根据conversation_id获取背景音乐失败: {e}")
        return None


async def get_music_generations_by_conversation(
    conversation_id: str,
    thread_id: str
) -> List[VideoMusicGenerationDB]:
    """根据对话ID和线程ID获取背景音乐列表
    
    读行后经 _row_to_music_generation（row_to_struct_safe），只传 schema 字段，DB 新增列不影响老代码。
    
    Returns:
        List[VideoMusicGenerationDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_music_generations 
                WHERE conversation_id = $1 AND thread_id = $2
                ORDER BY shot_number ASC NULLS LAST
                """,
                conversation_id, thread_id
            )
            return [x for row in rows for x in [_row_to_music_generation(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据对话获取背景音乐列表失败: {e}")
        return []


async def get_music_generations_by_latest_transcription(
    conversation_id: str,
    thread_id: str,
) -> List[VideoMusicGenerationDB]:
    """获取"最新 transcription"关联的背景音乐列表（前端 / video_assembly 应该用这个）。

    每次 smart_clip apply 会基于切片范围创建一条**新** ``video_audio_transcription`` 记录，
    并按新 transcription 的 segments 重建 per-segment mg。``video_music_generation_versions``
    表的 ``audio_transcription_id`` 字段天然标识了每个 mg version 来自哪条 transcription——
    所以"最新 transcription 的 mg 集合" = 当前生效的切片版本，无需 archived 等额外标记。

    判定规则：thread 下按 ``created_at DESC LIMIT 1`` 取最新 transcription，再返回所有 **有任一
    version** 关联到这条 transcription 的 mg（``DISTINCT mg.uuid``）。

    若 thread 下没有 transcription（异常路径），退回到 ``get_music_generations_by_conversation``
    的全集行为，避免空列表。
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            latest_tr_row = await fetch_one(
                conn,
                """
                SELECT uuid FROM video_audio_transcription
                WHERE thread_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                thread_id,
            )
            if not latest_tr_row or not latest_tr_row.get("uuid"):
                logger.info(
                    "get_music_generations_by_latest_transcription: thread=%s 未找到 transcription，回退到全集",
                    thread_id,
                )
                rows = await fetch_all(
                    conn,
                    """
                    SELECT * FROM video_music_generations
                    WHERE conversation_id = $1 AND thread_id = $2
                    ORDER BY shot_number ASC NULLS LAST
                    """,
                    conversation_id, thread_id,
                )
            else:
                latest_tr_uuid = latest_tr_row["uuid"]
                rows = await fetch_all(
                    conn,
                    """
                    SELECT mg.* FROM video_music_generations mg
                    WHERE mg.conversation_id = $1
                      AND mg.thread_id = $2
                      AND EXISTS (
                          SELECT 1 FROM video_music_generation_versions mgv
                          WHERE mgv.music_generation_id = mg.uuid
                            AND mgv.audio_transcription_id = $3
                      )
                    ORDER BY mg.shot_number ASC NULLS LAST
                    """,
                    conversation_id, thread_id, latest_tr_uuid,
                )
            return [x for row in rows for x in [_row_to_music_generation(row)] if x is not None]
    except Exception as e:
        logger.error(f"按最新 transcription 获取背景音乐列表失败: {e}")
        return []


async def get_music_generation_version_by_id(music_generation_version_id: str) -> Optional[VideoMusicGenerationVersionDB]:
    """根据ID获取背景音乐版本
    
    Returns:
        Optional[VideoMusicGenerationVersionDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_music_generation_versions WHERE uuid = $1",
                music_generation_version_id
            )
            return _row_to_music_generation_version(row)
    except Exception as e:
        logger.error(f"根据ID获取背景音乐版本失败: {e}")
        return None


async def get_music_generation_versions_by_ids(music_version_ids: List[str]) -> List[VideoMusicGenerationVersionDB]:
    """批量获取背景音乐版本
    
    Returns:
        List[VideoMusicGenerationVersionDB]: msgspec对象列表
    """
    try:
        if not music_version_ids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_music_generation_versions WHERE uuid = ANY($1)",
                music_version_ids
            )
            return _rows_to_music_generation_versions(rows)
    except Exception as e:
        logger.error(f"批量获取背景音乐版本失败: {e}")
        return []


async def get_music_generation_versions_by_music_generation_ids(
    music_generation_ids: List[str]
) -> List[VideoMusicGenerationVersionDB]:
    """批量获取背景音乐的所有版本 - 避免N+1
    
    Returns:
        List[VideoMusicGenerationVersionDB]: msgspec对象列表
    """
    try:
        if not music_generation_ids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_music_generation_versions 
                WHERE music_generation_id = ANY($1)
                ORDER BY music_generation_id, version_number
                """,
                music_generation_ids
            )
            return _rows_to_music_generation_versions(rows)
    except Exception as e:
        logger.error(f"批量获取背景音乐版本失败: {e}")
        return []


async def get_music_generation_versions(music_generation_id: str) -> List[VideoMusicGenerationVersionDB]:
    """获取背景音乐的所有版本
    
    Returns:
        List[VideoMusicGenerationVersionDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_music_generation_versions 
                WHERE music_generation_id = $1 
                ORDER BY version_number
                """,
                music_generation_id
            )
            return _rows_to_music_generation_versions(rows)
    except Exception as e:
        logger.error(f"获取背景音乐版本失败: {e}")
        return []


async def update_music_generation_version_prompt(
    version_uuid: str,
    new_prompt: str
) -> bool:
    """更新背景音乐版本的prompt
    
    Returns:
        bool: 是否更新成功
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_music_generation_versions
                SET music_prompt = $1, updated_at = $2
                WHERE uuid = $3
                """,
                new_prompt,
                now_utc(),
                version_uuid
            )
            return True
    except Exception as e:
        logger.error(f"更新背景音乐版本prompt失败: {e}")
        return False


async def update_music_generation_additional_data(
    music_generation_id: str,
    additional_data: Optional[Dict[str, Any]],
) -> bool:
    """整体覆盖更新 video_music_generations.additional_data（智能剪辑用）。

    调用方负责 merge：通常先 ``get_music_generation_by_uuid`` 拿旧 dict，再合并 ``smart_clip`` 后传入。
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_music_generations
                SET additional_data = $1, updated_at = $2
                WHERE uuid = $3
                """,
                to_json(additional_data),
                now_utc(),
                music_generation_id,
            )
            return True
    except Exception as e:
        logger.error(f"更新 music_generation additional_data 失败: {e}")
        return False


async def update_music_generation_current_version(
    music_generation_id: str,
    current_version_index: int,
) -> bool:
    """更新 video_music_generations.current_version_index（0-based）。

    用于智能剪辑落 v2 后切到新版本。
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_music_generations
                SET current_version_index = $1, updated_at = $2
                WHERE uuid = $3
                """,
                int(current_version_index),
                now_utc(),
                music_generation_id,
            )
            return True
    except Exception as e:
        logger.error(f"更新 music_generation current_version_index 失败: {e}")
        return False


async def update_music_generation_version_segments(
    version_uuid: str,
    segment_start_time: Optional[float],
    segment_end_time: Optional[float],
) -> bool:
    """更新 music_generation_version 的 segment_start_time / segment_end_time（智能剪辑后写裁切区间）。"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_music_generation_versions
                SET segment_start_time = $1, segment_end_time = $2, updated_at = $3
                WHERE uuid = $4
                """,
                segment_start_time,
                segment_end_time,
                now_utc(),
                version_uuid,
            )
            return True
    except Exception as e:
        logger.error(f"更新 music_generation_version segment 范围失败: {e}")
        return False


# ==================== 特殊音乐生成函数（保持兼容） ====================

async def create_music_from_audio_transcription(
    run_id: str,
    transcription_uuid: str,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    provider: str = "udio",
    additional_data: Optional[Dict[str, Any]] = None
) -> str:
    """基于音频转录创建背景音乐（Udio特有流程）
    
    Returns:
        str: 新创建的music_generation UUID
    """
    try:
        # 创建主记录时添加transcription引用
        if not additional_data:
            additional_data = {}
        additional_data['transcription_uuid'] = transcription_uuid
        additional_data['provider'] = provider
        
        return await create_music_generation(
            run_id=run_id,
            user_id=user_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            additional_data=additional_data
        )
    except Exception as e:
        logger.error(f"基于音频转录创建背景音乐失败: {e}")
        raise


async def create_suno_music_generation(
    run_id: str,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    prompt: str,
    tags: List[str],
    style: Optional[str] = None,
    *,
    is_full_story_music: bool = True,
    additional_data: Optional[Dict[str, Any]] = None
) -> str:
    """创建 Suno 背景音乐（Suno 特有流程）。默认 is_full_story_music=True，便于合成时用 music_url。"""
    try:
        if not additional_data:
            additional_data = {}
        additional_data["provider"] = MusicProvider.SUNO.value
        additional_data["prompt"] = prompt
        additional_data["tags"] = tags
        additional_data["style"] = style

        return await create_music_generation(
            run_id=run_id,
            user_id=user_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            is_full_story_music=is_full_story_music,
            is_instrumental=True,
            additional_data=additional_data
        )
    except Exception as e:
        logger.error(f"创建Suno背景音乐失败: {e}")
        raise


async def get_video_audio_segments_by_transcription_uuid(transcription_uuid: str) -> List[VideoAudioSegmentDB]:
    """根据转录UUID获取所有音频片段（别名函数，兼容旧代码）
    
    Note: 这是 get_video_audio_segment_by_transcription_uuid 的别名
    
    Args:
        transcription_uuid: 音频转录UUID
        
    Returns:
        List[VideoAudioSegmentDB]: msgspec对象列表
    """
    return await get_video_audio_segment_by_transcription_uuid(transcription_uuid)


# ==================== 按镜头/故事板写入的旁白与音效（与 database_utils 等调用保持一致） ====================

async def create_narration_shot_based(
    shot_number: int,
    is_bridge: bool,
    has_narration: bool,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    story_outline_id: str,
    scene_id: str,
    storyboard_detail_id: str,
    detailed_shot_id: str
) -> str:
    """创建旁白记录（按镜头/故事板维度，与原有 video_analysis 行为一致）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            result = await insert_and_return(
                conn,
                """
                INSERT INTO video_narrations (
                    uuid, shot_number, is_bridge, has_narration, conversation_id,
                    thread_id, run_id, user_id, story_outline_id, scene_id,
                    storyboard_detail_id, detailed_shot_id, current_version_index,
                    created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                RETURNING *
                """,
                uuid, shot_number, is_bridge, has_narration, conversation_id,
                thread_id, run_id, user_id, story_outline_id, scene_id,
                storyboard_detail_id, detailed_shot_id, 0,
                created_at, created_at
            )
            logger.info(f"创建旁白记录成功: {result['uuid']}")
            return result['uuid']
    except Exception as e:
        logger.error(f"创建旁白记录失败: {e}")
        raise


async def create_narration_version_shot_based(
    narration_id: str,
    version_number: int,
    shot_number: int,
    narration_text: str,
    enhanced_prompt: str,
    audio_url: Optional[str],
    provider: str,
    params: Optional[Dict[str, Any]],
    duration: Optional[float],
    is_bridge: bool,
    success: bool,
    error_msg: Optional[str],
    raw_error_msg: Optional[str] = None,
    conversation_id: str = None,
    thread_id: str = None,
    run_id: str = None,
    user_id: str = None,
    ai_messages: Optional[str] = None
) -> str:
    """创建旁白版本记录（按镜头维度，与原有 video_analysis 行为一致）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            result = await insert_and_return(
                conn,
                """
                INSERT INTO video_narration_versions (
                    uuid, narration_id, version_number, shot_number, narration_text,
                    enhanced_prompt, audio_url, provider, params, duration,
                    is_bridge, success, error_msg, raw_error_msg, conversation_id,
                    thread_id, run_id, user_id, ai_messages, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21)
                RETURNING *
                """,
                uuid, narration_id, version_number, shot_number, narration_text,
                enhanced_prompt, audio_url, provider, to_json(params), duration,
                is_bridge, success, error_msg, raw_error_msg, conversation_id,
                thread_id, run_id, user_id, ai_messages, created_at, created_at
            )
            logger.info(f"创建旁白版本记录成功: {result['uuid']}")
            return result['uuid']
    except Exception as e:
        logger.error(f"创建旁白版本记录失败: {e}")
        raise


async def create_audio_effect_shot_based(
    shot_number: int,
    is_bridge: bool,
    has_audio_effect: bool,
    video_generation_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    story_outline_id: str,
    scene_id: str,
    storyboard_detail_id: str,
    detailed_shot_id: str
) -> str:
    """创建音效记录（按镜头/故事板维度，与原有 video_analysis 行为一致）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            result = await insert_and_return(
                conn,
                """
                INSERT INTO video_audio_effects (
                    uuid, shot_number, is_bridge, has_audio_effect, video_generation_id,
                    conversation_id, thread_id, run_id, user_id, story_outline_id,
                    scene_id, storyboard_detail_id, detailed_shot_id, current_version_index,
                    created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                RETURNING *
                """,
                uuid, shot_number, is_bridge, has_audio_effect, video_generation_id,
                conversation_id, thread_id, run_id, user_id, story_outline_id,
                scene_id, storyboard_detail_id, detailed_shot_id, 0,
                created_at, created_at
            )
            logger.info(f"创建音效记录成功: {result['uuid']}")
            return result['uuid']
    except Exception as e:
        logger.error(f"创建音效记录失败: {e}")
        raise


async def create_audio_effect_version_shot_based(
    audio_effect_id: str,
    version_number: int,
    shot_number: int,
    video_url: str,
    audio_prompt: str,
    enhanced_prompt: str,
    audio_url: Optional[str],
    video_with_audio_url: Optional[str],
    provider: str,
    params: Optional[Dict[str, Any]],
    duration: Optional[float],
    is_bridge: bool,
    success: bool,
    error_msg: Optional[str],
    raw_error_msg: Optional[str] = None,
    conversation_id: str = None,
    thread_id: str = None,
    run_id: str = None,
    user_id: str = None
) -> str:
    """创建音效版本记录（按镜头维度，与原有 video_analysis 行为一致）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            result = await insert_and_return(
                conn,
                """
                INSERT INTO video_audio_effect_versions (
                    uuid, audio_effect_id, version_number, shot_number, video_url,
                    audio_prompt, enhanced_prompt, audio_url, video_with_audio_url,
                    provider, params, duration, is_bridge, success, error_msg,
                    raw_error_msg, conversation_id, thread_id, run_id, user_id,
                    created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22)
                RETURNING *
                """,
                uuid, audio_effect_id, version_number, shot_number, video_url,
                audio_prompt, enhanced_prompt, audio_url, video_with_audio_url,
                provider, to_json(params), duration, is_bridge, success, error_msg,
                raw_error_msg, conversation_id, thread_id, run_id, user_id,
                created_at, created_at
            )
            logger.info(f"创建音效版本记录成功: {result['uuid']}")
            return result['uuid']
    except Exception as e:
        logger.error(f"创建音效版本记录失败: {e}")
        raise


# ==================== 从音频转录按片段创建音乐（与原有 video_analysis 行为一致） ====================

async def create_music_generation_version_for_transcription(
    music_generation_id: str,
    version_number: int,
    music_url: str,
    provider: str,
    duration: float,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    success: bool = True,
    error_msg: str = None,
    music_prompt: str = None,
    shot_number: int = None,
    is_instrumental: bool = True,
    audio_transcription_id: str = None,
    audio_segment_ids: List[str] = None,
    params: Dict[str, Any] = None,
    ai_messages: str = None,
    additional_data: Optional[Dict[str, Any]] = None,
    original_audio_url: str = None,
    segment_start_time: float = None,
    segment_end_time: float = None
) -> str:
    """创建音乐生成版本记录（转录片段用，含 audio_transcription_id/segment 等列）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            result = await insert_and_return(
                conn,
                """
                INSERT INTO video_music_generation_versions (
                    uuid, music_generation_id, version_number, music_url, provider,
                    duration, conversation_id, thread_id, run_id, user_id,
                    success, error_msg, music_prompt, shot_number, is_instrumental,
                    audio_transcription_id, audio_segment_ids, params, ai_messages,
                    additional_data, original_audio_url, segment_start_time,
                    segment_end_time, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25)
                RETURNING *
                """,
                uuid, music_generation_id, version_number, music_url, provider,
                duration, conversation_id, thread_id, run_id, user_id,
                success, error_msg, music_prompt, shot_number, is_instrumental,
                audio_transcription_id, to_json(audio_segment_ids), to_json(params), ai_messages,
                to_json(additional_data), original_audio_url, segment_start_time,
                segment_end_time, created_at, created_at
            )
            logger.info(f"创建音乐生成版本记录成功(转录): {result['uuid']}")
            return result['uuid']
    except Exception as e:
        logger.error(f"创建音乐生成版本记录失败: {e}")
        raise


async def create_music_from_audio_transcription_segments(
    audio_transcription_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    story_outline_id: Optional[str] = None
) -> List[str]:
    """从音频转录创建音乐生成记录（每个音频片段对应一个场景），与原有 video_analysis 行为一致。返回 music_generation uuid 列表。"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            transcription = await fetch_one(
                conn,
                "SELECT * FROM video_audio_transcription WHERE uuid = $1",
                audio_transcription_id
            )
            if not transcription:
                raise ValueError(f"音频转录记录不存在: {audio_transcription_id}")
            segments = await fetch_all(
                conn,
                """
                SELECT * FROM video_audio_segment
                WHERE transcription_uuid = $1
                ORDER BY segment_id
                """,
                audio_transcription_id
            )

        logger.info(f"开始切割 {len(segments)} 个音频片段")
        segment_data_list = []

        for segment in segments:
            music_generation_id = await create_music_generation(
                run_id=run_id,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                story_outline_id=story_outline_id,
                is_full_story_music=False,
                is_instrumental=not bool((segment.get("text") or "").strip()),
                scene_id=None,
                shot_number=segment["segment_id"] + 1,
                additional_data={
                    "source": "audio_transcription",
                    "original_audio_url": transcription["audio_url"],
                    "whisper_start": segment["start"],
                    "whisper_end": segment["end"],
                },
            )

            actual_duration = segment["end"] - segment["start"]

            trim_result = await msc.audio_trim(
                audio_url=transcription['audio_url'],
                start=segment["start"],
                duration=actual_duration,
                run_id=run_id,
            )
            msc_url = trim_result["result_url"]
            # 以 Gemini 分段的 end-start 为权威时长，不用 probe 值覆盖。
            # FFmpeg trim 产出的 WAV 因 block alignment 会略长 (+0.024~0.044s)，
            # 若用 probe 值，28 段累积 ~1s 漂移且每段末尾出现黑帧。
            logger.info(f"✅ Media service audio trim succeeded for segment {segment['segment_id']}, duration={actual_duration:.3f}s")

            segment_data_list.append((
                music_generation_id,
                msc_url,
                actual_duration,
                segment["start"],
                segment["end"],
            ))

        segment_audio_urls = {}
        for music_gen_id, segment_url, duration, start_time, end_time in segment_data_list:
            segment_audio_urls[music_gen_id] = segment_url

        music_generation_ids = []
        for segment_idx, segment in enumerate(segments):
            music_gen_id, _, actual_duration, actual_start, actual_end = segment_data_list[segment_idx]
            segment_audio_url = segment_audio_urls.get(music_gen_id, transcription["audio_url"])
            formatted_music_prompt = segment.get("text") or ""

            await create_music_generation_version_for_transcription(
                music_generation_id=music_gen_id,
                version_number=1,
                music_url=segment_audio_url,
                provider="uploaded",
                duration=actual_duration,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                success=True,
                music_prompt=formatted_music_prompt,
                shot_number=segment["segment_id"] + 1,
                is_instrumental=not bool(formatted_music_prompt) if formatted_music_prompt else True,
                audio_transcription_id=audio_transcription_id,
                audio_segment_ids=[segment["uuid"]],
                original_audio_url=transcription["audio_url"],
                segment_start_time=actual_start,
                segment_end_time=actual_end,
                additional_data={
                    "actual_start_time": actual_start,
                    "actual_end_time": actual_end,
                    "actual_duration": actual_duration,
                    "whisper_start_time": segment["start"],
                    "whisper_end_time": segment["end"],
                    "whisper_duration": segment["end"] - segment["start"],
                    "text": segment.get("text", ""),
                },
            )
            await update_video_audio_segment_duration(segment["uuid"], actual_duration)
            music_generation_ids.append(music_gen_id)

        logger.info(f"从音频转录创建了 {len(music_generation_ids)} 个音乐生成记录")
        return music_generation_ids
    except Exception as e:
        logger.error(f"从音频转录创建音乐生成记录失败: {e}")
        raise
