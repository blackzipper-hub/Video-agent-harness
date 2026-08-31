"""shield_events 表分页查询（Admin）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.shield_event import ShieldEventDB


async def list_shield_events(
    session: AsyncSession,
    *,
    page: int = 1,
    size: int = 50,
    user_id: Optional[str] = None,
    layer: Optional[str] = None,
) -> Tuple[List[ShieldEventDB], int]:
    page = max(1, page)
    size = min(max(1, size), 200)
    offset = (page - 1) * size

    conditions = []
    if user_id:
        conditions.append(ShieldEventDB.user_id == user_id)
    if layer:
        conditions.append(ShieldEventDB.layer == layer)

    count_stmt = select(func.count()).select_from(ShieldEventDB)
    stmt = select(ShieldEventDB).order_by(ShieldEventDB.created_at.desc())
    if conditions:
        for c in conditions:
            count_stmt = count_stmt.where(c)
            stmt = stmt.where(c)

    total = int((await session.execute(count_stmt)).scalar_one())
    rows = (await session.execute(stmt.offset(offset).limit(size))).scalars().all()
    return list(rows), total
