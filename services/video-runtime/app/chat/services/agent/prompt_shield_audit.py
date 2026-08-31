"""Prompt Shield 审计/限流辅助模块。

两类能力（均可独立开关；默认均关闭，开机行为不变）：

1. ``record_shield_event``：把 Rail 命中写入 ``shield_events`` 表。
   - 仅在 ``settings.SHIELD_AUDIT_DB_ENABLED=True`` 时入库；表需先跑 ``init/init_shield_events.py``。
   - 写失败永不抛（``try/except``），避免影响主链路。

2. ``incr_shield_hit`` / ``is_user_throttled``：用 Redis 累计命中次数并判断是否限流。
   - 仅在 ``settings.SHIELD_THROTTLE_ENABLED=True`` 时启用。
   - Redis 不可用时回退为"未限流"（永不阻塞主链路）。

详见 ``Cuti-Agent-Learning/PROMPT_SECURITY_CUTI_ANALYSIS.md`` §7 / §8。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ----------------------------- DB 审计 -----------------------------

def _truncate_snippet(s: Optional[str], limit: int = 500) -> Optional[str]:
    if s is None:
        return None
    if len(s) <= limit:
        return s
    return s[:limit]


async def record_shield_event(
    *,
    layer: str,
    reason: str,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    matched_snippet: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """把一次 shield 命中写入 ``shield_events`` 表（异步、容错）。

    - ``layer``: "input" / "output" / "companion"
    - ``reason``: 命中代码（regex:xxx / canary_leak / raw_uuid ...）
    - 写失败不抛；开关关闭则直接返回。
    """
    try:
        from ...config import get_settings
        if not get_settings().SHIELD_AUDIT_DB_ENABLED:
            return

        from ...models.database import AsyncSessionLocal
        from ...models.shield_event import ShieldEventDB

        async with AsyncSessionLocal() as session:
            evt = ShieldEventDB(
                layer=layer,
                reason=reason,
                user_id=user_id,
                thread_id=thread_id,
                run_id=run_id,
                matched_snippet=_truncate_snippet(matched_snippet),
                extra=extra,
            )
            session.add(evt)
            await session.commit()
    except Exception as e:
        logger.warning(f"[shield] record_shield_event failed (silently swallowed): {e}")


# ----------------------------- Redis 限流 -----------------------------

def _counter_key(user_id: str) -> str:
    return f"shield:{user_id}:count"


def _throttled_key(user_id: str) -> str:
    return f"shield:{user_id}:throttled"


async def incr_shield_hit(user_id: Optional[str]) -> int:
    """累计 user 的 shield 命中次数；超过阈值则写一个限流标记。返回当前计数（失败 0）。"""
    if not user_id:
        return 0
    try:
        from ...config import get_settings
        settings = get_settings()
        if not settings.SHIELD_THROTTLE_ENABLED:
            return 0

        from ..redis.connection import get_redis_client
        redis = await get_redis_client(decode_responses=True)
        key = _counter_key(user_id)
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, settings.SHIELD_THROTTLE_WINDOW_S)
        if count >= settings.SHIELD_THROTTLE_THRESHOLD:
            await redis.set(
                _throttled_key(user_id),
                "1",
                ex=settings.SHIELD_THROTTLE_WINDOW_S,
            )
        return int(count)
    except Exception as e:
        logger.warning(f"[shield] incr_shield_hit failed: {e}")
        return 0


async def is_user_throttled(user_id: Optional[str]) -> bool:
    """查当前 user 是否处于限流期内。Redis 异常时返回 False（不阻塞主链路）。"""
    if not user_id:
        return False
    try:
        from ...config import get_settings
        if not get_settings().SHIELD_THROTTLE_ENABLED:
            return False

        from ..redis.connection import get_redis_client
        redis = await get_redis_client(decode_responses=True)
        val = await redis.get(_throttled_key(user_id))
        return bool(val)
    except Exception as e:
        logger.warning(f"[shield] is_user_throttled failed: {e}")
        return False
