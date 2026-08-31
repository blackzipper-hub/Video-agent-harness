"""统一的协作式取消（cooperative cancellation）工具。

背景：硬 cancel（task.cancel()）在 LangGraph 里只能在「节点/superstep 边界」生效——
当前正在执行的重节点（角色设计、关键帧、视频分镜等）内部的并行远程生成会先把这一批跑完，
才会在边界处真正抛出取消，导致点了取消后仍要多跑几分钟、多花钱。

解决：用户点取消时 cancel 接口会「立即」把该 run 在 Redis 里的状态置为 CANCELLED
（见 agent_router_endpoints.cancel）。本模块提供一个轻量检查，让重节点的长轮询循环 /
批次边界主动读这个标志位，发现已取消就抛 CancelledError 及时退出，从而把取消延迟从
「整批耗时」降到「一次轮询间隔」。

设计要点：
- 通过 contextvar 透传当前 run_id，调用点无需层层传参；LangGraph 异步节点任务在创建时
  会继承设置好的 contextvar，因此节点内部（含其调用的 llm/工具轮询）都能取到 run_id。
- 进程内带极短 TTL 缓存，避免多个并发轮询每秒高频打 Redis；一旦确认已取消则永久缓存为 True。
- 抛出 asyncio.CancelledError（BaseException）：长轮询里常见的 `except Exception` 不会误吞它，
  会一路向上传播到 task_worker 的取消处理分支，按既有逻辑落 CANCELLED 状态并清理。
"""

import asyncio
import logging
import time
from contextvars import ContextVar
from typing import Dict, Optional, Tuple

from ....models.task_status import TaskStatus
from ...redis.connection import get_redis_stream_service

logger = logging.getLogger(__name__)

# 当前正在执行的 run_id（在 astream 入口设置，供节点内部读取）
_current_run_id_cv: ContextVar[Optional[str]] = ContextVar(
    "cuti_cancellation_current_run_id", default=None
)

# 进程内缓存：run_id -> (到期时间戳, 是否已取消)。降低高并发轮询对 Redis 的压力。
_CANCEL_CACHE_TTL = 1.0
_cancel_cache: Dict[str, Tuple[float, bool]] = {}


def set_current_run_id(run_id: Optional[str]) -> None:
    """在 astream 入口设置当前 run_id，使节点内部可读取（用于协作式取消检查）。"""
    _current_run_id_cv.set(run_id)


def get_current_run_id() -> Optional[str]:
    return _current_run_id_cv.get()


async def is_run_cancelled(run_id: Optional[str] = None) -> bool:
    """读取该 run 是否已被取消（Redis 状态为 CANCELLED）。带极短 TTL 缓存。"""
    rid = run_id or _current_run_id_cv.get()
    if not rid:
        return False

    now = time.monotonic()
    cached = _cancel_cache.get(rid)
    if cached is not None:
        expire_at, cancelled = cached
        # 已取消的结果永久有效；未取消的结果仅在 TTL 内复用
        if cancelled or now < expire_at:
            return cancelled

    try:
        redis_service = await get_redis_stream_service()
        status = await redis_service.get_task_status(rid)
    except Exception as e:
        # 读取失败时不阻断业务，按未取消处理（取消仍会在节点边界由 task.cancel 兜底）
        logger.debug("is_run_cancelled 读取状态失败（忽略）: run_id=%s err=%s", rid, e)
        return False

    cancelled = bool(status) and status.get("status") == TaskStatus.CANCELLED.value
    _cancel_cache[rid] = (now + _CANCEL_CACHE_TTL, cancelled)
    return cancelled


async def raise_if_cancelled(run_id: Optional[str] = None) -> None:
    """若该 run 已被取消，主动抛出 CancelledError 以尽快退出当前重节点。"""
    if await is_run_cancelled(run_id):
        rid = run_id or _current_run_id_cv.get()
        logger.info("⏹️ 检测到取消信号，主动中断当前节点: run_id=%s", rid)
        raise asyncio.CancelledError()
