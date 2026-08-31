"""
视频角色CRUD操作 - 使用msgspec返回类型

包含：
- 角色主记录 (video_characters)  
- 角色版本 (video_character_generation_versions)
- 角色多视角图 (video_character_multi_view_images + versions)
- 角色融合图 (video_character_fusion_images)
"""

import logging
from typing import List, Optional, Dict, Any

from ...models.database import get_asyncpg_pool
from ...utils.asyncpg_utils import (
    fetch_one, fetch_all, fetch_val, execute,
    insert_and_return, generate_uuid, now_utc, to_json, from_json
)
from ...schemas.video.video_character import (
    VideoCharacterDB,
    VideoCharacterGenerationVersionDB,
    VideoCharacterMultiViewImageDB,
    VideoCharacterMultiViewImageVersionDB,
    VideoCharacterFusionImageDB
)

logger = logging.getLogger(__name__)


def pick_selected_character_version(
    versions: List[VideoCharacterGenerationVersionDB],
    character: Optional[VideoCharacterDB],
) -> Optional[VideoCharacterGenerationVersionDB]:
    """关键帧/视频侧取角色参考图：优先 selected_version_id，其次 current_version 索引，否则回退 version_number 最大。"""
    if not versions:
        return None
    sorted_v = sorted(versions, key=lambda v: v.version_number)
    if character is not None:
        sid = getattr(character, "selected_version_id", None)
        if sid:
            for v in sorted_v:
                if v.uuid == sid:
                    return v
        idx = getattr(character, "current_version_index", None)
        if idx is not None and idx >= 0 and idx < len(sorted_v):
            return sorted_v[idx]
    return max(sorted_v, key=lambda v: v.version_number)


# ==================== Helper Functions ====================

from .row_normalize import row_to_struct_safe

_CHARACTER_DICT_FIELDS = ("additional_data",)
_CHARACTER_VERSION_LIST_FIELDS = ("reference_image_urls",)
_CHARACTER_VERSION_DICT_FIELDS = ("additional_data", "image_tool_metrics")
_MULTI_VIEW_DICT_FIELDS = ("additional_data",)
_MULTI_VIEW_VERSION_DICT_FIELDS = ("additional_data",)
_FUSION_LIST_FIELDS = ("character_ids",)
_FUSION_DICT_FIELDS = ("additional_data",)


def _row_to_character(row: Optional[Dict]) -> Optional[VideoCharacterDB]:
    """将数据库行转换为VideoCharacterDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, VideoCharacterDB, dict_fields=_CHARACTER_DICT_FIELDS)


def _row_to_character_version(row: Optional[Dict]) -> Optional[VideoCharacterGenerationVersionDB]:
    """将数据库行转换为VideoCharacterGenerationVersionDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoCharacterGenerationVersionDB,
        list_fields=_CHARACTER_VERSION_LIST_FIELDS, dict_fields=_CHARACTER_VERSION_DICT_FIELDS
    )


def _row_to_multi_view(row: Optional[Dict]) -> Optional[VideoCharacterMultiViewImageDB]:
    """将数据库行转换为VideoCharacterMultiViewImageDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, VideoCharacterMultiViewImageDB, dict_fields=_MULTI_VIEW_DICT_FIELDS)


def _row_to_multi_view_version(row: Optional[Dict]) -> Optional[VideoCharacterMultiViewImageVersionDB]:
    """将数据库行转换为VideoCharacterMultiViewImageVersionDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, VideoCharacterMultiViewImageVersionDB, dict_fields=_MULTI_VIEW_VERSION_DICT_FIELDS)


def _row_to_fusion_image(row: Optional[Dict]) -> Optional[VideoCharacterFusionImageDB]:
    """将数据库行转换为VideoCharacterFusionImageDB对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(
        row, VideoCharacterFusionImageDB,
        list_fields=_FUSION_LIST_FIELDS, dict_fields=_FUSION_DICT_FIELDS
    )


# ==================== 角色主记录 CRUD ====================

async def create_character(character_data: dict) -> str:
    """创建角色记录
    
    Args:
        character_data: 包含角色信息的字典
        
    Returns:
        str: 新创建的character UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            result = await insert_and_return(
                conn,
                "video_characters",
                uuid=uuid,
                user_id=character_data["user_id"],
                conversation_id=character_data["conversation_id"],
                thread_id=character_data["thread_id"],
                run_id=character_data["run_id"],
                type=character_data.get("type", "character"),
                name=character_data["name"],
                description=character_data["description"],
                personality=character_data["personality"],
                appearance=character_data["appearance"],
                role=character_data["role"],
                style=character_data.get("style"),
                body_type=character_data.get("body_type"),
                image_url=character_data.get("image_url", ""),
                current_version_index=0,
                additional_data=character_data.get("additional_data"),
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建角色失败: {e}")
        raise


async def create_character_version(version_data: dict) -> str:
    """创建角色版本记录
    
    Returns:
        str: 新创建的version UUID
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            
            # 处理 reference_image_urls: 空列表转换为 None
            ref_urls = version_data.get("reference_image_urls")
            if ref_urls == []:
                ref_urls = None
            
            image_tool_metrics = version_data.get("image_tool_metrics")
            tool_duration_sec = version_data.get("tool_duration_sec")
            tool_cost = version_data.get("tool_cost")

            result = await insert_and_return(
                conn,
                "video_character_generation_versions",
                uuid=uuid,
                video_character_id=version_data["video_character_id"],
                conversation_id=version_data["conversation_id"],
                thread_id=version_data["thread_id"],
                run_id=version_data["run_id"],
                user_id=version_data["user_id"],
                version_number=version_data["version_number"],
                character_image_url=version_data["character_image_url"],
                t2i_prompt=version_data["t2i_prompt"],
                provider=version_data["provider"],
                reference_image_urls=ref_urls,
                success=version_data.get("success", True),
                error_msg=version_data.get("error_msg"),
                raw_error_msg=version_data.get("raw_error_msg"),
                aspect_ratio=version_data.get("aspect_ratio"),
                resolution=version_data.get("resolution"),
                model=version_data.get("model"),
                seed=version_data.get("seed"),
                image_generation_tool=version_data.get("image_generation_tool"),
                ai_messages=version_data.get("ai_messages"),
                additional_data=version_data.get("additional_data"),
                multi_view_image_version_id=version_data.get("multi_view_image_version_id"),
                image_tool_metrics=image_tool_metrics,
                tool_duration_sec=tool_duration_sec,
                tool_cost=tool_cost,
                created_at=created_at,
                updated_at=created_at
            )
            return result['uuid']
    except Exception as e:
        logger.error(f"创建角色版本失败: {e}")
        raise


async def get_character_by_uuid(character_uuid: str) -> Optional[VideoCharacterDB]:
    """根据UUID获取角色
    
    Returns:
        Optional[VideoCharacterDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_characters WHERE uuid = $1",
                character_uuid
            )
            return _row_to_character(row)
    except Exception as e:
        logger.error(f"根据UUID获取角色失败: {e}")
        return None


async def get_characters_by_uuids(character_uuids: List[str]) -> List[VideoCharacterDB]:
    """批量获取角色 - 避免N+1
    
    Returns:
        List[VideoCharacterDB]: msgspec对象列表
    """
    try:
        if not character_uuids:
            return []
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_characters WHERE uuid = ANY($1)",
                character_uuids
            )
            return [x for row in rows for x in [_row_to_character(row)] if x is not None]
    except Exception as e:
        logger.error(f"批量获取角色失败: {e}")
        return []


async def get_characters_by_run_id(run_id: str, user_id: str) -> List[VideoCharacterDB]:
    """根据run_id获取角色列表
    
    Returns:
        List[VideoCharacterDB]: 按创建时间排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_characters 
                WHERE run_id = $1 AND user_id = $2
                ORDER BY created_at
                """,
                run_id, user_id
            )
            return [x for row in rows for x in [_row_to_character(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据run_id获取角色失败: {e}")
        return []


async def get_characters_by_thread_id(thread_id: str, user_id: str) -> List[VideoCharacterDB]:
    """根据thread_id获取角色列表（跨所有 run）"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_characters WHERE thread_id = $1 AND user_id = $2 ORDER BY created_at",
                thread_id, user_id
            )
            return [x for row in rows for x in [_row_to_character(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据thread_id获取角色失败: {e}")
        return []


async def get_characters_by_conversation(conversation_id: str, thread_id: str) -> List[VideoCharacterDB]:
    """根据对话ID和线程ID获取角色列表
    
    Returns:
        List[VideoCharacterDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_characters 
                WHERE conversation_id = $1 AND thread_id = $2
                ORDER BY created_at
                """,
                conversation_id, thread_id
            )
            return [x for row in rows for x in [_row_to_character(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据conversation获取角色失败: {e}")
        return []


# ==================== 角色版本 CRUD ====================

async def get_character_versions_by_character_id(character_id: str, user_id: str) -> List[VideoCharacterGenerationVersionDB]:
    """根据角色ID获取版本列表
    
    Returns:
        List[VideoCharacterGenerationVersionDB]: 按version_number排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_character_generation_versions 
                WHERE video_character_id = $1 AND user_id = $2
                ORDER BY version_number
                """,
                character_id, user_id
            )
            return [x for row in rows for x in [_row_to_character_version(row)] if x is not None]
    except Exception as e:
        logger.error(f"获取角色版本失败: {e}")
        return []


async def get_character_versions_batch(character_uuids: List[str], user_id: str) -> Dict[str, List[VideoCharacterGenerationVersionDB]]:
    """批量获取角色版本信息 - 一次查询避免N+1
    
    Args:
        character_uuids: 角色UUID列表
        user_id: 用户ID
        
    Returns:
        Dict[str, List]: 以character_uuid为key的版本列表字典
    """
    try:
        if not character_uuids:
            return {}
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_character_generation_versions 
                WHERE video_character_id = ANY($1) AND user_id = $2
                ORDER BY video_character_id, version_number
                """,
                character_uuids, user_id
            )
            
            # 按character_id分组
            result = {}
            for row in rows:
                ver = _row_to_character_version(row)
                if ver is not None:
                    char_id = row["video_character_id"]
                    if char_id not in result:
                        result[char_id] = []
                    result[char_id].append(ver)
            return result
    except Exception as e:
        logger.error(f"批量获取角色版本失败: {e}")
        return {}


async def get_character_version_by_uuid(version_uuid: str) -> Optional[VideoCharacterGenerationVersionDB]:
    """根据UUID获取角色版本
    
    Returns:
        Optional[VideoCharacterGenerationVersionDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_character_generation_versions WHERE uuid = $1",
                version_uuid
            )
            return _row_to_character_version(row)
    except Exception as e:
        logger.error(f"根据UUID获取角色版本失败: {e}")
        return None


async def update_character(character_uuid: str, updates: Dict[str, Any]) -> bool:
    """更新角色信息
    
    Args:
        character_uuid: 角色UUID
        updates: 要更新的字段字典
        
    Returns:
        bool: 是否更新成功
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            updates['updated_at'] = now_utc()
            
            set_clauses = []
            params = []
            for i, (key, value) in enumerate(updates.items(), 1):
                set_clauses.append(f"{key} = ${i}")
                params.append(value)
            
            params.append(character_uuid)
            
            await execute(
                conn,
                f"""
                UPDATE video_characters
                SET {', '.join(set_clauses)}
                WHERE uuid = ${len(params)}
                """,
                *params
            )
            return True
    except Exception as e:
        logger.error(f"更新角色失败: {e}")
        return False


async def update_character_selected_version(character_uuid: str, selected_version_id: str) -> bool:
    """更新角色的选中版本
    
    Returns:
        bool: 是否更新成功
    """
    import re
    _uuid_re = re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    if not _uuid_re.match((selected_version_id or "").strip()):
        logger.error(
            "更新角色选中版本失败: selected_version_id 不是合法 UUID: %r",
            selected_version_id,
        )
        return False
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            # 校验版本属于该角色
            row = await fetch_one(
                conn,
                """
                SELECT uuid FROM video_character_generation_versions
                WHERE uuid = $1 AND video_character_id = $2
                """,
                selected_version_id,
                character_uuid,
            )
            if not row:
                logger.error(
                    "更新角色选中版本失败: version %s 不属于 character %s",
                    selected_version_id,
                    character_uuid,
                )
                return False
            await execute(
                conn,
                """
                UPDATE video_characters
                SET selected_version_id = $1, updated_at = $2
                WHERE uuid = $3
                """,
                selected_version_id,
                now_utc(),
                character_uuid
            )
            return True
    except Exception as e:
        logger.error(f"更新角色选中版本失败: {e}")
        return False


# ==================== 角色多视角图 CRUD ====================

async def get_or_create_character_multi_view_image(
    character_uuid: str,
    user_id: str,
    conversation_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None
) -> str:
    """获取或创建角色的多视角图主记录
    
    Returns:
        str: multi_view_image UUID
    """
    try:
        if conversation_id is not None and not isinstance(conversation_id, str):
            conversation_id = str(conversation_id)
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            # 查找是否已存在
            existing = await fetch_one(
                conn,
                """
                SELECT * FROM video_character_multi_view_images 
                WHERE video_character_id = $1 AND user_id = $2
                """,
                character_uuid, user_id
            )
            
            if existing:
                return existing['uuid']
            
            # 如果不存在，创建新记录
            uuid = generate_uuid()
            created_at = now_utc()
            
            result = await insert_and_return(
                conn,
                "video_character_multi_view_images",
                uuid=uuid,
                video_character_id=character_uuid,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                current_version_index=0,
                created_at=created_at,
                updated_at=created_at
            )
            
            logger.info(f"✅ 创建多视角图主记录: {result['uuid']}")
            return result['uuid']
    except Exception as e:
        logger.error(f"获取或创建多视角图主记录失败: {e}")
        raise


async def get_character_multi_view_image(
    character_uuid: str,
    user_id: str
) -> Optional[VideoCharacterMultiViewImageDB]:
    """获取角色的多视角图主记录
    
    Returns:
        Optional[VideoCharacterMultiViewImageDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                """
                SELECT * FROM video_character_multi_view_images 
                WHERE video_character_id = $1 AND user_id = $2
                """,
                character_uuid, user_id
            )
            return _row_to_multi_view(row)
    except Exception as e:
        logger.error(f"获取多视角图主记录失败: {e}")
        return None


async def get_character_multi_view_images_batch(
    character_uuids: List[str],
    user_id: str
) -> Dict[str, VideoCharacterMultiViewImageVersionDB]:
    """批量获取角色的最新多视角图版本 - 避免N+1
    
    Args:
        character_uuids: 角色UUID列表
        user_id: 用户ID
        
    Returns:
        Dict[str, VideoCharacterMultiViewImageVersionDB]: 以character_uuid为key，最新版本struct为value
    """
    try:
        if not character_uuids:
            return {}
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            # 获取每个角色的最新多视角图版本
            rows = await fetch_all(
                conn,
                """
                SELECT DISTINCT ON (video_character_id) *
                FROM video_character_multi_view_image_versions
                WHERE video_character_id = ANY($1) AND user_id = $2
                ORDER BY video_character_id, version_number DESC
                """,
                character_uuids, user_id
            )
            
            result = {}
            for row in rows:
                char_id = row.get('video_character_id')
                if char_id:
                    version = _row_to_multi_view_version(row)
                    if version:
                        result[char_id] = version
            return result
    except Exception as e:
        logger.error(f"批量获取多视角图版本失败: {e}")
        return {}


async def get_next_multi_view_image_version_number(multi_view_image_id: str) -> int:
    """返回该多视角图主记录的下一个 version_number（调用方在传 create 前计算好）"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        val = await fetch_val(
            conn,
            """
            SELECT COALESCE(MAX(version_number), 0) + 1
            FROM video_character_multi_view_image_versions
            WHERE multi_view_image_id = $1
            """,
            multi_view_image_id
        )
        return val


async def create_character_multi_view_image_version(
    multi_view_image_uuid: str,
    video_character_id: str,
    version_number: int,
    multi_view_image_url: str,
    multi_view_prompt: str,
    provider: str,
    user_id: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    reference_image_url: Optional[str] = None,
    success: bool = True,
    error_msg: Optional[str] = None,
    raw_error_msg: Optional[str] = None,
    model: Optional[str] = None,
    style: Optional[str] = None,
    additional_data: Optional[Dict[str, Any]] = None,
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    seed: Optional[int] = None,
    video_character_version_id: Optional[str] = None,
    ai_messages: Optional[str] = None,
) -> str:
    """创建角色多视角图版本。version_number 由调用方在传之前计算好（如 get_next_multi_view_image_version_number）。
    表有 aspect_ratio/resolution/model/seed/video_character_version_id/ai_messages；无 reference_image_url/style，二者仅保留参数不写入。"""
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            uuid = generate_uuid()
            created_at = now_utc()
            result = await insert_and_return(
                conn,
                "video_character_multi_view_image_versions",
                uuid=uuid,
                multi_view_image_id=multi_view_image_uuid,
                video_character_id=video_character_id,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                run_id=run_id,
                version_number=version_number,
                multi_view_image_url=multi_view_image_url,
                multi_view_prompt=multi_view_prompt,
                provider=provider,
                success=success,
                error_msg=error_msg,
                raw_error_msg=raw_error_msg,
                model=model,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                seed=seed,
                video_character_version_id=video_character_version_id,
                ai_messages=ai_messages,
                additional_data=additional_data,
                created_at=created_at,
                updated_at=created_at
            )
            
            logger.info(f"✅ 创建多视角图版本: {result['uuid']}")
            return result['uuid']
    except Exception as e:
        logger.error(f"创建多视角图版本失败: {e}")
        raise


async def get_character_multi_view_image_version_by_id(version_id: str) -> Optional[VideoCharacterMultiViewImageVersionDB]:
    """根据ID获取多视角图版本
    
    Returns:
        Optional[VideoCharacterMultiViewImageVersionDB]: msgspec对象
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                "SELECT * FROM video_character_multi_view_image_versions WHERE uuid = $1",
                version_id
            )
            return _row_to_multi_view_version(row)
    except Exception as e:
        logger.error(f"获取多视角图版本失败: {e}")
        return None


async def get_character_multi_view_image_versions_by_ids(
    version_ids: List[str],
) -> Dict[str, VideoCharacterMultiViewImageVersionDB]:
    """批量根据ID获取多视角图版本（避免 N+1，读路径 _row_to_multi_view_version 防御 DB 多列）
    
    Returns:
        Dict[uuid, VideoCharacterMultiViewImageVersionDB]: uuid -> 版本对象
    """
    if not version_ids:
        return {}
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                "SELECT * FROM video_character_multi_view_image_versions WHERE uuid = ANY($1)",
                version_ids,
            )
            out = {}
            for row in rows:
                mv = _row_to_multi_view_version(row)
                if mv and getattr(mv, "uuid", None):
                    out[mv.uuid] = mv
            return out
    except Exception as e:
        logger.error(f"批量获取多视角图版本失败: {e}")
        return {}


async def get_character_multi_view_image_versions_by_character_version_id(
    character_version_id: str
) -> List[VideoCharacterMultiViewImageVersionDB]:
    """根据角色版本ID获取关联的多视角图版本列表
    
    Returns:
        List[VideoCharacterMultiViewImageVersionDB]: msgspec对象列表
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT mv.* 
                FROM video_character_multi_view_image_versions mv
                JOIN video_character_generation_versions cv 
                    ON cv.multi_view_image_version_id = mv.uuid
                WHERE cv.uuid = $1
                ORDER BY mv.version_number
                """,
                character_version_id
            )
            return [x for row in rows for x in [_row_to_multi_view_version(row)] if x is not None]
    except Exception as e:
        logger.error(f"根据角色版本ID获取多视角图版本失败: {e}")
        return []


async def get_character_multi_view_image_versions(
    multi_view_image_id: str,
    user_id: str
) -> List[VideoCharacterMultiViewImageVersionDB]:
    """获取多视角图的所有版本
    
    Returns:
        List[VideoCharacterMultiViewImageVersionDB]: 按version_number排序
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_character_multi_view_image_versions
                WHERE multi_view_image_id = $1 AND user_id = $2
                ORDER BY version_number
                """,
                multi_view_image_id, user_id
            )
            return [x for row in rows for x in [_row_to_multi_view_version(row)] if x is not None]
    except Exception as e:
        logger.error(f"获取多视角图版本列表失败: {e}")
        return []


async def update_character_version_multi_view_image(
    character_version_uuid: str,
    multi_view_image_version_uuid: str
) -> bool:
    """更新角色版本的多视角图关联
    
    Returns:
        bool: 是否更新成功
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_character_generation_versions
                SET multi_view_image_version_id = $1, updated_at = $2
                WHERE uuid = $3
                """,
                multi_view_image_version_uuid,
                now_utc(),
                character_version_uuid
            )
            return True
    except Exception as e:
        logger.error(f"更新角色版本多视角图关联失败: {e}")
        return False


async def update_character_version_multi_view_link(
    character_version_uuid: str,
    multi_view_image_version_id: Optional[str]
) -> bool:
    """更新角色版本的multi_view_image_version_id字段
    
    Returns:
        bool: 是否更新成功
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_character_generation_versions
                SET multi_view_image_version_id = $1, updated_at = $2
                WHERE uuid = $3
                """,
                multi_view_image_version_id,
                now_utc(),
                character_version_uuid
            )
            return True
    except Exception as e:
        logger.error(f"更新角色版本multi_view链接失败: {e}")
        return False


async def update_character_version_selected_multi_view(
    character_version_uuid: str,
    multi_view_version_uuid: Optional[str]
) -> bool:
    """更新角色版本选中的多视角图版本
    
    Returns:
        bool: 是否更新成功
    """
    try:
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            await execute(
                conn,
                """
                UPDATE video_character_generation_versions
                SET multi_view_image_version_id = $1, updated_at = $2
                WHERE uuid = $3
                """,
                multi_view_version_uuid,
                now_utc(),
                character_version_uuid
            )
            return True
    except Exception as e:
        logger.error(f"更新角色版本选中多视角图失败: {e}")
        return False


async def update_character_version_multi_view_image_full(
    character_uuid: str,
    version_number: int,
    multi_view_image_url: str,
    multi_view_prompt: Optional[str],
    user_id: str,
    provider: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    model: Optional[str] = None,
    seed: Optional[int] = None,
    success: bool = True,
    error_msg: Optional[str] = None,
    raw_error_msg: Optional[str] = None,
    ai_messages: Optional[str] = None,
    conversation_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None
) -> Optional[str]:
    """更新character version的多视角图（创建新版本并关联）- 完整版本
    
    Returns:
        Optional[str]: 新创建的多视角图版本UUID
    """
    try:
        # 1. 获取或创建多视角图主记录
        multi_view_image_id = await get_or_create_character_multi_view_image(
            character_uuid, user_id, conversation_id, thread_id, run_id
        )
        
        # 2. 获取角色版本UUID
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            character_version = await fetch_one(
                conn,
                """
                SELECT uuid FROM video_character_generation_versions 
                WHERE video_character_id = $1 AND version_number = $2 AND user_id = $3
                """,
                character_uuid, version_number, user_id
            )
            character_version_uuid = character_version['uuid'] if character_version else None
        
        # 3. 在传之前计算好下一版本号，再创建新的多视角图版本
        next_version_number = await get_next_multi_view_image_version_number(multi_view_image_id)
        multi_view_version_id = await create_character_multi_view_image_version(
            multi_view_image_uuid=multi_view_image_id,
            video_character_id=character_uuid,
            version_number=next_version_number,
            multi_view_image_url=multi_view_image_url,
            multi_view_prompt=multi_view_prompt or "",
            provider=provider or "default",
            user_id=user_id,
            conversation_id=conversation_id or "",
            thread_id=thread_id or "",
            run_id=run_id or "",
            reference_image_url=None,
            success=success,
            error_msg=error_msg,
            raw_error_msg=raw_error_msg,
            model=model,
            style=None,
            additional_data={"ai_messages": ai_messages} if ai_messages else None
        )
        
        # 4. 更新character version的关联
        if character_version_uuid and multi_view_version_id:
            await update_character_version_multi_view_image(character_version_uuid, multi_view_version_id)
            logger.info(f"✅ 更新version {version_number}的多视角图成功（新版本: {multi_view_version_id}）")
            return multi_view_version_id
        else:
            logger.warning(f"⚠️ 创建多视角图版本成功，但关联character version失败")
            return multi_view_version_id
    except Exception as e:
        logger.error(f"更新version多视角图失败: {e}")
        return None


# ==================== 角色融合图 CRUD ====================

async def create_character_fusion_image(fusion_data: Dict[str, Any]) -> str:
    """创建或更新角色融合图记录（唯一约束 fusion_key + user_id）。
    
    首次：INSERT；同一 (fusion_key, user_id) 再次调用（如重新生成）：UPDATE 该行（新图 URL、success、error_msg 等），
    避免重复插入且重新生成时能更新为最新结果。
    调用方传入 fusion_data 字典，需含: user_id, conversation_id, thread_id, run_id,
    fusion_key, image_type, character_ids, fusion_image_url；可选: aspect_ratio, resolution,
    model, seed, provider, fusion_prompt, success, error_msg, raw_error_msg, ai_messages, additional_data
    """
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        uuid_val = generate_uuid()
        now = now_utc()
        user_id = fusion_data["user_id"]
        conversation_id = fusion_data["conversation_id"]
        thread_id = fusion_data["thread_id"]
        run_id = fusion_data["run_id"]
        fusion_key = fusion_data["fusion_key"]
        image_type = fusion_data["image_type"]
        raw_ids = fusion_data["character_ids"]
        # jsonb 列需要 JSON 字符串；且保证为字符串列表避免传入对象导致报错
        character_ids = [str(x) for x in (raw_ids or [])]
        character_ids_json = to_json(character_ids)
        fusion_image_url = fusion_data.get("fusion_image_url", "")
        aspect_ratio = fusion_data.get("aspect_ratio")
        resolution = fusion_data.get("resolution")
        model = fusion_data.get("model")
        seed = fusion_data.get("seed")
        provider = fusion_data.get("provider")
        fusion_prompt = fusion_data.get("fusion_prompt")
        success = fusion_data.get("success", True)
        error_msg = fusion_data.get("error_msg")
        raw_error_msg = fusion_data.get("raw_error_msg")
        ai_messages = fusion_data.get("ai_messages")
        # additional_data 为 jsonb，与 character_ids 一致传 JSON 字符串
        additional_data_raw = fusion_data.get("additional_data")
        additional_data_json = to_json(additional_data_raw) if additional_data_raw is not None else None

        row = await conn.fetchrow(
            """
            INSERT INTO video_character_fusion_images (
                uuid, user_id, conversation_id, thread_id, run_id,
                fusion_key, image_type, character_ids, fusion_image_url,
                aspect_ratio, resolution, model, seed, provider, fusion_prompt,
                success, error_msg, raw_error_msg, ai_messages, additional_data,
                created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22)
            ON CONFLICT (fusion_key, user_id)
            DO UPDATE SET
                fusion_image_url = EXCLUDED.fusion_image_url,
                updated_at = EXCLUDED.updated_at,
                aspect_ratio = EXCLUDED.aspect_ratio,
                resolution = EXCLUDED.resolution,
                model = EXCLUDED.model,
                seed = EXCLUDED.seed,
                provider = EXCLUDED.provider,
                fusion_prompt = EXCLUDED.fusion_prompt,
                success = EXCLUDED.success,
                error_msg = EXCLUDED.error_msg,
                raw_error_msg = EXCLUDED.raw_error_msg,
                ai_messages = EXCLUDED.ai_messages,
                additional_data = EXCLUDED.additional_data
            RETURNING uuid
            """,
            uuid_val, user_id, conversation_id, thread_id, run_id,
            fusion_key, image_type, character_ids_json, fusion_image_url,
            aspect_ratio, resolution, model, seed, provider, fusion_prompt,
            success, error_msg, raw_error_msg, ai_messages, additional_data_json,
            now, now
        )
        if not row:
            raise RuntimeError("create_character_fusion_image: RETURNING 无结果")
        out_uuid = row["uuid"]
        logger.info(f"✅ 角色融合图 创建/更新: uuid={out_uuid}, fusion_key={fusion_key}")
        return out_uuid


async def get_character_fusion_image_by_key(
    character_ids: List[str],
    user_id: str
) -> Optional[VideoCharacterFusionImageDB]:
    """根据角色ID列表获取融合图（如果存在）- 返回 msgspec.Struct。character_ids 列为 jsonb，用 jsonb 比较。"""
    try:
        if not character_ids:
            return None

        ids_json = to_json(sorted(character_ids))  # 与 fusion_key 一致：按排序比较
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            row = await fetch_one(
                conn,
                """
                SELECT * FROM video_character_fusion_images
                WHERE character_ids @> $1::jsonb AND character_ids <@ $1::jsonb
                  AND user_id = $2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                ids_json, user_id
            )
        if not row:
            return None
        return _row_to_fusion_image(row)
    except Exception as e:
        logger.error(f"获取角色融合图失败: {e}")
        return None


async def get_character_fusion_images_by_character_ids(
    character_ids: List[str]
) -> List[VideoCharacterFusionImageDB]:
    """根据角色ID列表获取所有相关的融合图（与任一 ID 有交集的记录）。使用 EXISTS + jsonb_array_elements_text 兼容各版本。返回 struct 列表供调用方使用 .character_ids / .fusion_image_url 等。"""
    try:
        if not character_ids:
            return []

        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_character_fusion_images
                WHERE EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(character_ids) AS e
                    WHERE e = ANY($1::text[])
                )
                ORDER BY created_at DESC
                """,
                character_ids
            )
            return [f for r in rows if (f := _row_to_fusion_image(r)) is not None]
    except Exception as e:
        logger.error(f"获取角色融合图列表失败: {e}")
        return []


async def get_character_fusion_images_batch(
    character_ids: List[str]
) -> Dict[str, List[VideoCharacterFusionImageDB]]:
    """批量获取多个角色的融合图 - 避免N+1，按角色ID分组
    
    Args:
        character_ids: 角色ID列表
        
    Returns:
        Dict[str, List[VideoCharacterFusionImageDB]]: 以character_id为key，融合图struct列表为value
    """
    try:
        if not character_ids:
            return {}
        
        pool = get_asyncpg_pool()
        async with pool.acquire() as conn:
            rows = await fetch_all(
                conn,
                """
                SELECT * FROM video_character_fusion_images
                WHERE EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(character_ids) AS e
                    WHERE e = ANY($1::text[])
                )
                ORDER BY created_at DESC
                """,
                character_ids
            )

            # 为每个角色ID构建融合图列表
            result = {char_id: [] for char_id in character_ids}
            for row in rows:
                fusion = _row_to_fusion_image(row)
                if fusion and fusion.character_ids:
                    # 融合图可能包含多个角色，需要添加到所有相关角色的列表中
                    for char_id in fusion.character_ids:
                        if char_id in result:
                            result[char_id].append(fusion)
            return result
    except Exception as e:
        logger.error(f"批量获取角色融合图失败: {e}")
        return {}


# ==================== 批量查询优化函数 ====================

async def batch_get_characters_with_versions(
    character_uuids: List[str],
    user_id: str
) -> Dict[str, Dict[str, Any]]:
    """批量获取角色 + 所有版本 - 避免N+1
    
    Args:
        character_uuids: 角色UUID列表
        user_id: 用户ID
        
    Returns:
        Dict[str, Dict]: 以uuid为key的字典
        {
            'uuid1': {
                'character': VideoCharacterDB,
                'versions': [VideoCharacterGenerationVersionDB, ...]
            },
            ...
        }
    """
    try:
        if not character_uuids:
            return {}
        
        # 1. 批量获取角色
        characters = await get_characters_by_uuids(character_uuids)
        
        # 2. 批量获取所有版本
        versions_dict = await get_character_versions_batch(character_uuids, user_id)
        
        # 3. 组装结果
        results = {}
        for character in characters:
            results[character.uuid] = {
                'character': character,
                'versions': versions_dict.get(character.uuid, [])
            }
        
        return results
    except Exception as e:
        logger.error(f"批量获取角色+版本失败: {e}")
        return {}
