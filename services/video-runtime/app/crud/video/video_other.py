"""
视频其他组件CRUD操作 - 使用msgspec返回类型

包含：
- 视频分析 (video_analysis)
- 视频合成 (video_assemblies)
- 唇形同步 (video_lipsync_generations + versions)
"""

import logging
from typing import List, Optional, Dict, Any

from ...models.database import get_asyncpg_pool
from ...utils.asyncpg_utils import (
    fetch_one, fetch_all, fetch_val, execute,
    insert_and_return, generate_uuid, now_utc, to_json, from_json,
    build_insert_row,
)
from ...schemas.video.video_other import (
    VideoLipsyncGenerationDB,
    VideoLipsyncGenerationVersionDB,
    VideoAnalysisDB,
    VideoAssemblyDB
)

logger = logging.getLogger(__name__)


# ==================== Helper Functions ====================

from .row_normalize import row_to_struct_safe, set_effective_duration

_LIPSYNC_VERSION_DICT_FIELDS = ("params", "additional_data")
_VIDEO_ASSEMBLY_DICT_FIELDS = (
    "source_video_versions",
    "source_narration_versions",
    "source_audio_effect_versions",
    "source_music_versions",
    "source_video_urls",
    "source_narration_urls",
    "source_audio_effect_urls",
    "source_music_urls",
    "additional_data"
)
_VIDEO_ASSEMBLY_LIST_FIELDS = ("uploaded_audio_files",)


def _row_to_lipsync_version(row: Optional[Dict]) -> Optional[VideoLipsyncGenerationVersionDB]:
    """将数据库行转换为VideoLipsyncGenerationVersionDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoLipsyncGenerationVersionDB,
        dict_fields=_LIPSYNC_VERSION_DICT_FIELDS
    )


def _row_to_video_assembly(row: Optional[Dict]) -> Optional[VideoAssemblyDB]:
    """将数据库行转换为VideoAssemblyDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoAssemblyDB,
        list_fields=_VIDEO_ASSEMBLY_LIST_FIELDS, dict_fields=_VIDEO_ASSEMBLY_DICT_FIELDS
    )


def _rows_to_lipsync_versions(rows: List[Dict]) -> List[VideoLipsyncGenerationVersionDB]:
    """批量转换唇形同步版本"""
    return [x for row in rows for x in [_row_to_lipsync_version(row)] if x is not None]


# ==================== 视频分析 CRUD ====================

async def create_video_analysis(
    run_id: str,
    video_type: str,
    duration: float,
    main_character: str,
    purpose: str,
    next_action: str,
    key_elements: Optional[List[str]] = None,
    style_preferences: Optional[List[str]] = None,
    target_audience: Optional[str] = None,
    user_id: str = "",
    conversation_id: str = "",
    thread_id: str = "",
    additional_data: Optional[Dict[str, Any]] = None,
    content_category: Optional[str] = None,
    hidden_style_description: Optional[str] = None,
    curated_style_prompt_id: Optional[str] = None,
) -> VideoAnalysisDB:
    """创建视频分析结果。hidden_style_description/curated_style_prompt_id 为可选，DB 未加列时 build_insert_row 会过滤，不写入。"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            row = build_insert_row(
                VideoAnalysisDB,
                uuid=uuid,
                run_id=run_id,
                video_type=video_type,
                duration=duration,
                main_character=main_character,
                purpose=purpose,
                next_action=next_action,
                key_elements=to_json(key_elements) if key_elements else None,
                style_preferences=to_json(style_preferences) if style_preferences else None,
                target_audience=target_audience,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                additional_data=additional_data,
                content_category=content_category,
                hidden_style_description=hidden_style_description,
                curated_style_prompt_id=curated_style_prompt_id,
                created_at=created_at,
                updated_at=created_at,
            )
            row["duration_sec"] = float(row.pop("duration", 0))
            result = await insert_and_return(conn, "video_analysis", **row)
            set_effective_duration(result, "duration_sec", "duration")
            return row_to_struct_safe(result, VideoAnalysisDB, dict_fields=("additional_data",))
    except Exception as e:
        logger.error(f"创建视频分析结果失败: {e}")
        raise


async def get_video_analysis_by_uuid(uuid: str) -> Optional[VideoAnalysisDB]:
    """根据UUID获取视频分析结果（只传 schema 字段，DB 多列不崩）。"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_analysis WHERE uuid = $1",
                uuid
            )
            set_effective_duration(row, "duration_sec", "duration")
            return row_to_struct_safe(row, VideoAnalysisDB, dict_fields=("additional_data",))
    except Exception as e:
        logger.error(f"根据UUID获取视频分析结果失败: {e}")
        return None


async def get_video_analysis_by_thread_id(thread_id: str) -> Optional[VideoAnalysisDB]:
    """根据 thread_id 获取该 thread 下最新一条视频分析（按 created_at 降序取第一条）。"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_analysis WHERE thread_id = $1 ORDER BY created_at DESC LIMIT 1",
                thread_id
            )
            if not row:
                return None
            set_effective_duration(row, "duration_sec", "duration")
            return row_to_struct_safe(row, VideoAnalysisDB, dict_fields=("additional_data",))
    except Exception as e:
        logger.error(f"根据 thread_id 获取视频分析结果失败: {e}")
        return None


async def update_video_analysis(uuid: str, update_data: Dict[str, Any]) -> bool:
    """更新视频分析结果（如 style_preferences）。"""
    try:
        if not update_data:
            return False

        data = dict(update_data)
        if "style_preferences" in data and isinstance(data["style_preferences"], list):
            data["style_preferences"] = to_json(data["style_preferences"])

        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            data["updated_at"] = now_utc()
            from ...utils.asyncpg_utils import build_update_query
            query, values = build_update_query(
                table="video_analysis",
                data=data,
                where={"uuid": uuid},
            )
            result = await conn.fetchrow(query, *values)
            return result is not None
    except Exception as e:
        logger.error(f"更新视频分析结果失败: {e}")
        return False


# ==================== 视频合成 CRUD ====================

async def create_video_assembly(
    final_video_url: str,
    total_duration: float,
    success: bool,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    story_outline_id: str,
    error_msg: str = None,
    final_video_url_no_subtitle: str = None,
    source_video_versions: Dict[int, str] = None,
    source_narration_versions: Dict[int, str] = None,
    source_audio_effect_versions: Dict[int, str] = None,
    source_music_versions: Dict[int, str] = None,
    source_video_urls: Dict[int, str] = None,
    source_narration_urls: Dict[int, str] = None,
    source_audio_effect_urls: Dict[int, str] = None,
    source_music_urls: Dict[int, str] = None,
    uploaded_audio_files: List[str] = None,
    assembly_mode: str = None,
    title: str = None,
    additional_data: Optional[Dict[str, Any]] = None
) -> str:
    """创建视频合成记录
    
    Returns:
        str: 新创建的assembly UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            # asyncpg 会自动将 dict/list 转换为 PostgreSQL json 类型
            # 但需要将 int 键转换为字符串键（JSON 标准要求键为字符串）
            def normalize_dict_keys(d):
                if d is None:
                    return None
                return {str(k): v for k, v in d.items()}
            
            result = await insert_and_return(
                conn,
                "video_assemblies",
                uuid=uuid,
                final_video_url=final_video_url,
                final_video_url_no_subtitle=final_video_url_no_subtitle,
                total_duration=total_duration,
                success=success,
                error_msg=error_msg,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                story_outline_id=story_outline_id,
                source_video_versions=normalize_dict_keys(source_video_versions),
                source_narration_versions=normalize_dict_keys(source_narration_versions),
                source_audio_effect_versions=normalize_dict_keys(source_audio_effect_versions),
                source_music_versions=normalize_dict_keys(source_music_versions),
                source_video_urls=normalize_dict_keys(source_video_urls),
                source_narration_urls=normalize_dict_keys(source_narration_urls),
                source_audio_effect_urls=normalize_dict_keys(source_audio_effect_urls),
                source_music_urls=normalize_dict_keys(source_music_urls),
                uploaded_audio_files=uploaded_audio_files,
                assembly_mode=assembly_mode,
                title=title,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at
            )
            logger.info(f"✅ 创建视频合成记录成功: {result['uuid']}")
            return result['uuid']
    except Exception as e:
        logger.error(f"创建视频合成记录失败: {e}")
        raise


async def get_video_assembly_by_uuid(uuid: str) -> Optional[VideoAssemblyDB]:
    """根据UUID获取视频合成记录
    
    Returns:
        Optional[VideoAssemblyDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_assemblies WHERE uuid = $1",
                uuid
            )
            return _row_to_video_assembly(row)
    except Exception as e:
        logger.error(f"获取视频合成记录失败: {e}")
        return None


# 仅查询所需列，避免 SELECT *；DB 新增列不影响本逻辑（与 row_to_struct_safe 读路径防御思路一致）
_LATEST_FINAL_VIDEO_KEYS = ("thread_id", "final_video_url")


async def get_video_assemblies_by_thread_id(thread_id: str) -> List[VideoAssemblyDB]:
    """根据 thread_id 获取所有视频合成记录"""
    if not thread_id:
        return []
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_assemblies WHERE thread_id = $1 ORDER BY created_at DESC",
                thread_id
            )
            return [x for row in rows for x in [_row_to_video_assembly(row)] if x is not None]
    except Exception as e:
        logger.error(f"获取 thread 视频合成记录失败: {e}")
        return []


def _escape_ilike(term: str) -> str:
    """转义 ILIKE 特殊字符（\\ % _），按默认转义符 \\ 处理，避免用户输入被当作通配符。"""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# 作品检索返回的列（避免 SELECT *，DB 新增列不影响本逻辑）
_CREATION_SEARCH_KEYS = ("uuid", "thread_id", "final_video_url", "title", "total_duration", "created_at", "prompt")


async def async_search_user_creations(
    user_id: str,
    page: int = 1,
    size: int = 20,
    q: Optional[str] = None,
    start_time=None,
    end_time=None,
    success_only: bool = True,
) -> tuple[List[Dict[str, Any]], int]:
    """检索用户的成片作品（video_assemblies）。

    q 按视频标题 / 该会话首条用户输入(prompt) 做 ILIKE 子串匹配；start_time/end_time 按
    created_at 时间范围过滤。返回 (列表, 总数)，按 created_at DESC 分页。
    """
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        # 关联 conversations + 首条 user_input 取 prompt（与 Go vault 查询口径一致）
        join_sql = """
            FROM video_assemblies v
            LEFT JOIN conversations c ON c.thread_id = v.thread_id
            LEFT JOIN LATERAL (
                SELECT content
                FROM conversation_messages m
                WHERE m.conversation_id = c.id AND m.event_type = 'user_input'
                ORDER BY m.created_at ASC
                LIMIT 1
            ) um ON true
        """
        conditions = ["v.user_id = $1"]
        params: List[Any] = [user_id]

        if success_only:
            conditions.append("v.success = true")

        if q and q.strip():
            params.append(f"%{_escape_ilike(q.strip())}%")
            idx = len(params)
            conditions.append(f"(v.title ILIKE ${idx} OR um.content ILIKE ${idx})")

        if start_time is not None:
            params.append(start_time)
            conditions.append(f"v.created_at >= ${len(params)}")

        if end_time is not None:
            params.append(end_time)
            conditions.append(f"v.created_at < ${len(params)}")

        where_clause = " AND ".join(conditions)

        total = await fetch_val(
            conn,
            f"SELECT COUNT(*) {join_sql} WHERE {where_clause}",
            *params
        )

        offset = (page - 1) * size
        params.append(size)
        size_idx = len(params)
        params.append(offset)
        offset_idx = len(params)
        rows = await fetch_all(
            conn,
            f"""
            SELECT v.uuid, v.thread_id, v.final_video_url, v.title, v.total_duration,
                   v.created_at, coalesce(um.content, '') AS prompt
            {join_sql}
            WHERE {where_clause}
            ORDER BY v.created_at DESC
            LIMIT ${size_idx} OFFSET ${offset_idx}
            """,
            *params
        )
        results = [{k: row.get(k) for k in _CREATION_SEARCH_KEYS if k in row} for row in rows if row]
        return results, total


async def get_latest_assembly_mode_by_thread(thread_id: str) -> Optional[str]:
    """根据 thread_id 获取该会话最新一次视频合成的 assembly_mode（audio_driven / video_driven 等）。"""
    if not thread_id:
        return None
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT assembly_mode FROM video_assemblies WHERE thread_id = $1 ORDER BY created_at DESC LIMIT 1",
                thread_id
            )
            if not row:
                return None
            return row.get("assembly_mode")
    except Exception as e:
        logger.error(f"获取 thread 最新 assembly_mode 失败: {e}")
        return None


async def get_latest_final_video_url_by_thread(thread_id: str) -> Optional[str]:
    """根据 thread_id 获取该会话最新一次视频合成的 final_video_url（与用户端展示的最新视频一致）。
    只 SELECT final_video_url，避免 DB 新增列对老代码造成影响。"""
    if not thread_id:
        return None
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT final_video_url FROM video_assemblies WHERE thread_id = $1 ORDER BY created_at DESC LIMIT 1",
                thread_id
            )
            if not row:
                return None
            return row.get("final_video_url")
    except Exception as e:
        logger.error(f"获取 thread 最新 final_video_url 失败: {e}")
        return None


async def batch_get_latest_final_video_url_by_threads(thread_ids: List[str]) -> Dict[str, Optional[str]]:
    """批量根据 thread_id 获取各会话最新 final_video_url。返回 thread_id -> final_video_url 映射。
    只取 row 中 thread_id、final_video_url 两列，与 filter_row_to_struct_keys 防御思路一致。"""
    out = {tid: None for tid in thread_ids if tid}
    if not out:
        return out
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT DISTINCT ON (thread_id) thread_id, final_video_url
                FROM video_assemblies
                WHERE thread_id = ANY($1)
                ORDER BY thread_id, created_at DESC
                """,
                list(out.keys())
            )
            for row in rows:
                if not row:
                    continue
                r = {k: row.get(k) for k in _LATEST_FINAL_VIDEO_KEYS if k in row}
                tid = r.get("thread_id")
                if tid:
                    out[tid] = r.get("final_video_url")
            return out
    except Exception as e:
        logger.error(f"批量获取 thread 最新 final_video_url 失败: {e}")
        return out


# ==================== 唇形同步 CRUD ====================

async def create_video_lipsync_generation(
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    video_segment_id: str,
    music_generation_id: str,
    segment_number: int
) -> VideoLipsyncGenerationDB:
    """创建唇形同步生成记录
    
    Returns:
        VideoLipsyncGenerationDB: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            result = await insert_and_return(
                conn,
                "video_lipsync_generations",
                uuid=uuid,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                video_segment_id=video_segment_id,
                music_generation_id=music_generation_id,
                segment_number=segment_number,
                current_version_index=0,
                created_at=created_at,
                updated_at=created_at
            )
            logger.info(f"✅ 唇形同步生成创建成功: {result['uuid']}")
            out = row_to_struct_safe(row=result, struct_cls=VideoLipsyncGenerationDB)
            if out is None:
                raise ValueError("创建唇形同步生成后转换 Struct 失败")
            return out
    except Exception as e:
        logger.error(f"❌ 创建唇形同步生成失败: {e}")
        raise


async def create_video_lipsync_generation_version(
    lipsync_generation_id: str,
    video_segment_id: str,
    video_segment_version_id: str,
    music_generation_version_id: str,
    version_number: int,
    segment_number: int,
    video_url: str,
    audio_url: str,
    original_video_url: str,
    provider: str,
    model: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    success: bool = True,
    error_msg: Optional[str] = None,
    duration: Optional[float] = None
) -> VideoLipsyncGenerationVersionDB:
    """创建唇形同步生成版本记录
    
    Returns:
        VideoLipsyncGenerationVersionDB: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            result = await insert_and_return(
                conn,
                "video_lipsync_generation_versions",
                uuid=uuid,
                lipsync_generation_id=lipsync_generation_id,
                video_segment_id=video_segment_id,
                video_segment_version_id=video_segment_version_id,
                music_generation_version_id=music_generation_version_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                version_number=version_number,
                segment_number=segment_number,
                video_url=video_url,
                audio_url=audio_url,
                original_video_url=original_video_url,
                provider=provider,
                model=model,
                success=success,
                error_msg=error_msg,
                duration=duration,
                created_at=created_at,
                updated_at=created_at
            )
            logger.info(f"✅ 唇形同步版本创建成功: {result['uuid']}")
            out = row_to_struct_safe(result, VideoLipsyncGenerationVersionDB, dict_fields=_LIPSYNC_VERSION_DICT_FIELDS)
            if out is None:
                raise ValueError("创建唇形同步版本后转换 Struct 失败")
            return out
    except Exception as e:
        logger.error(f"❌ 创建唇形同步版本失败: {e}")
        raise


async def get_lipsync_version_by_id(lipsync_version_id: str) -> Optional[VideoLipsyncGenerationVersionDB]:
    """根据ID获取唇形同步版本
    
    Returns:
        Optional[VideoLipsyncGenerationVersionDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_lipsync_generation_versions WHERE uuid = $1",
                lipsync_version_id
            )
            return _row_to_lipsync_version(row)
    except Exception as e:
        logger.error(f"❌ 获取唇形同步版本失败: {e}")
        return None


async def get_lipsync_versions_by_ids(lipsync_version_ids: List[str]) -> List[VideoLipsyncGenerationVersionDB]:
    """批量根据ID获取唇形同步版本 - 避免N+1
    
    Returns:
        List[VideoLipsyncGenerationVersionDB]: msgspec对象列表
    """
    try:
        if not lipsync_version_ids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_lipsync_generation_versions 
                WHERE uuid = ANY($1)
                """,
                lipsync_version_ids
            )
            return _rows_to_lipsync_versions(rows)
    except Exception as e:
        logger.error(f"❌ 批量获取唇形同步版本失败: {e}")
        return []


async def get_lipsync_generation_by_uuid(uuid: str) -> Optional[VideoLipsyncGenerationDB]:
    """根据UUID获取唇形同步生成记录
    
    Returns:
        Optional[VideoLipsyncGenerationDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_lipsync_generations WHERE uuid = $1",
                uuid
            )
            return row_to_struct_safe(row, VideoLipsyncGenerationDB)
    except Exception as e:
        logger.error(f"获取唇形同步生成记录失败: {e}")
        return None
