"""Cooperative cancellation for long provider polls.

DeepSeek / Runtime cancel sets a Redis flag for the current run. Heavy nodes
(research, consistency, provider polling) check that flag at loop boundaries and
raise asyncio.CancelledError so work stops at the next poll interval instead of
after the whole remote batch.

- run_id is stored in a contextvar so nested tool loops can read it
- a short in-process TTL cache avoids hammering Redis
- CancelledError is a BaseException, so `except Exception` in poll loops will
  not swallow it
"""

import asyncio
import logging
import time
from contextvars import ContextVar
from typing import Dict, Optional, Tuple

from ....models.task_status import TaskStatus
from ...redis.connection import get_redis_stream_service

logger = logging.getLogger(__name__)

_current_run_id_cv: ContextVar[Optional[str]] = ContextVar(
    "cuti_cancellation_current_run_id", default=None
)

_CANCEL_CACHE_TTL = 1.0
_cancel_cache: Dict[str, Tuple[float, bool]] = {}


def set_current_run_id(run_id: Optional[str]) -> None:
    """Record the active run so nested tool loops can check cancellation."""
    _current_run_id_cv.set(run_id)


def get_current_run_id() -> Optional[str]:
    return _current_run_id_cv.get()


async def is_run_cancelled(run_id: Optional[str] = None) -> bool:
    """Return whether this run has been marked CANCELLED in Redis."""
    rid = run_id or _current_run_id_cv.get()
    if not rid:
        return False

    now = time.monotonic()
    cached = _cancel_cache.get(rid)
    if cached is not None:
        expire_at, cancelled = cached
        if cancelled or now < expire_at:
            return cancelled

    try:
        redis_service = await get_redis_stream_service()
        status = await redis_service.get_task_status(rid)
    except Exception as e:
        logger.debug("is_run_cancelled failed (ignored): run_id=%s err=%s", rid, e)
        return False

    cancelled = bool(status) and status.get("status") == TaskStatus.CANCELLED.value
    _cancel_cache[rid] = (now + _CANCEL_CACHE_TTL, cancelled)
    return cancelled


async def raise_if_cancelled(run_id: Optional[str] = None) -> None:
    """Raise CancelledError as soon as the run has been cancelled."""
    if await is_run_cancelled(run_id):
        rid = run_id or _current_run_id_cv.get()
        logger.info("cancel signal, interrupting current node: run_id=%s", rid)
        raise asyncio.CancelledError()
