"""
精选风格提示词 CRUD
- 读路径：row_to_struct_safe 只传 schema 字段，DB 多列不崩。
- 写路径：build_insert_row 只写 schema 有的列（与 video_other 等一致，未做按 DB 列过滤）。
"""
import logging
import random
from typing import List, Optional, Dict, Any

from ...models.database import get_asyncpg_pool
from ...utils.asyncpg_utils import (
    fetch_one,
    fetch_all,
    insert_and_return,
    execute,
    generate_uuid,
    now_utc,
    build_insert_row,
    build_update_query,
    build_where_clause,
)
from ...schemas.video.curated_style_prompt import CuratedStylePromptDB, CuratedStyleCategoryRow
from .row_normalize import row_to_struct_safe

logger = logging.getLogger(__name__)


def _row_to_curated_style_prompt(row: Optional[Dict]) -> Optional[CuratedStylePromptDB]:
    """将 DB 行转为 CuratedStylePromptDB，只传 schema 字段，DB 多列不崩"""
    return row_to_struct_safe(row, CuratedStylePromptDB)


def _row_to_category(row: Optional[Dict]) -> Optional[str]:
    """将 DB 行转为 category 字符串，走 row_to_struct_safe 防御 DB 多列"""
    s = row_to_struct_safe(row, CuratedStyleCategoryRow)
    return (s.category or "").strip() or None if s else None


async def list_curated_style_prompts(
    category: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[CuratedStylePromptDB]:
    """列表，可选按 category 筛选，按 sort_order 升序。读路径 row_to_struct_safe 防御 DB 多列。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        if category:
            rows = await fetch_all(
                conn,
                'SELECT * FROM curated_style_prompts WHERE category = $1 ORDER BY sort_order ASC NULLS LAST, id ASC LIMIT $2 OFFSET $3',
                category,
                limit,
                offset,
            )
        else:
            rows = await fetch_all(
                conn,
                "SELECT * FROM curated_style_prompts ORDER BY sort_order ASC NULLS LAST, id ASC LIMIT $1 OFFSET $2",
                limit,
                offset,
            )
        return [x for row in rows for x in [_row_to_curated_style_prompt(row)] if x is not None]


async def get_curated_style_prompt_by_uuid(uuid: str) -> Optional[CuratedStylePromptDB]:
    """按 uuid 查询单条。读路径 row_to_struct_safe 防御 DB 多列。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        row = await fetch_one(conn, "SELECT * FROM curated_style_prompts WHERE uuid = $1", uuid)
        return _row_to_curated_style_prompt(row)


async def get_distinct_curated_style_categories() -> List[str]:
    """返回所有可用大类的列表（去重），供分析 LLM 做大类匹配；仅 is_available=true 的参与。无数据时返回空列表。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        rows = await fetch_all(
            conn,
            "SELECT DISTINCT category FROM curated_style_prompts WHERE is_available = true ORDER BY category",
        )
        return [c for row in rows for c in [_row_to_category(row)] if c is not None]


async def get_random_curated_style_prompt_by_category(category: str) -> Optional[CuratedStylePromptDB]:
    """按大类随机取一条（匹配到大类后由程序随机选二类）；仅 is_available=true 的参与。读路径 row_to_struct_safe 防御。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        rows = await fetch_all(
            conn,
            "SELECT * FROM curated_style_prompts WHERE category = $1 AND is_available = true",
            category,
        )
        if not rows:
            return None
        row = random.choice(rows)
        return _row_to_curated_style_prompt(row)


async def create_curated_style_prompt(
    category: str,
    name: str,
    description_en: str,
    name_zh: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    description_zh: Optional[str] = None,
    sort_order: Optional[int] = None,
    is_available: bool = True,
) -> CuratedStylePromptDB:
    """创建一条精选风格提示词。写路径 build_insert_row 只写 schema 有的列。is_available 默认 True。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        uuid = generate_uuid()
        created_at = now_utc()
        updated_at = created_at
        row = build_insert_row(
            CuratedStylePromptDB,
            uuid=uuid,
            created_at=created_at,
            updated_at=updated_at,
            category=category,
            name=name,
            description_en=description_en,
            name_zh=name_zh,
            thumbnail_url=thumbnail_url,
            description_zh=description_zh,
            sort_order=sort_order,
            is_available=is_available,
        )
        result = await insert_and_return(conn, "curated_style_prompts", **row)
        out = _row_to_curated_style_prompt(result)
        if not out:
            raise RuntimeError("insert_and_return did not return a row")
        return out


async def update_curated_style_prompt(
    uuid: str,
    *,
    category: Optional[str] = None,
    name: Optional[str] = None,
    name_zh: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    description_en: Optional[str] = None,
    description_zh: Optional[str] = None,
    sort_order: Optional[int] = None,
    is_available: Optional[bool] = None,
) -> Optional[CuratedStylePromptDB]:
    """按 uuid 更新（只更新传入的非 None 字段）。RETURNING 行用 row_to_struct_safe 防御。"""
    updates: Dict[str, Any] = {"updated_at": now_utc()}
    if category is not None:
        updates["category"] = category
    if name is not None:
        updates["name"] = name
    if name_zh is not None:
        updates["name_zh"] = name_zh
    if thumbnail_url is not None:
        updates["thumbnail_url"] = thumbnail_url
    if description_en is not None:
        updates["description_en"] = description_en
    if description_zh is not None:
        updates["description_zh"] = description_zh
    if sort_order is not None:
        updates["sort_order"] = sort_order
    if is_available is not None:
        updates["is_available"] = is_available

    if len(updates) <= 1:
        return await get_curated_style_prompt_by_uuid(uuid)

    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        query, values = build_update_query("curated_style_prompts", updates, {"uuid": uuid})
        row = await conn.fetchrow(query, *values)
        return _row_to_curated_style_prompt(dict(row) if row else None)


async def delete_curated_style_prompt(uuid: str) -> bool:
    """按 uuid 删除，返回是否删除了记录"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        result = await execute(
            conn,
            "DELETE FROM curated_style_prompts WHERE uuid = $1",
            uuid,
        )
        return result.endswith("1") if result else False
