"""Interrupt 门闩 full_auto 自动 continue：延迟秒数与 auto_resume_at 写入/推送。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ..crud.conversation import (
    async_get_message_by_id,
    async_update_message_event_data_partial,
)
from ..utils.asyncpg_utils import utc_isoformat

logger = logging.getLogger(__name__)

INTERRUPT_AUTO_RESUME_DELAY_SECONDS = 15
INTERRUPT_SMART_CLIP_AUTO_RESUME_DELAY_SECONDS = 60


def resolve_auto_resume_delay_seconds(event_data: Optional[Dict[str, Any]]) -> int:
    """按 interrupt payload 决定 SQS 延迟：smart_clip ready 给更长阅读时间。"""
    data = event_data or {}
    interrupt_data = data.get("interrupt_data") if isinstance(data.get("interrupt_data"), dict) else data
    if not isinstance(interrupt_data, dict):
        interrupt_data = {}
    smart_clip = interrupt_data.get("smart_clip")
    if isinstance(smart_clip, dict) and smart_clip.get("status") == "ready":
        return INTERRUPT_SMART_CLIP_AUTO_RESUME_DELAY_SECONDS
    return INTERRUPT_AUTO_RESUME_DELAY_SECONDS


async def schedule_interrupt_auto_resume(
    *,
    run_id: str,
    thread_id: str,
    interrupt_msgid: int,
    sqs_service,
    auto_resume_payload: Dict[str, Any],
) -> None:
    """状态已 INTERRUPTED 后：写 auto_resume_at、入队延迟 auto_resume、推送前端同步事件。"""
    msg = await async_get_message_by_id(int(interrupt_msgid))
    if not msg:
        logger.warning("schedule_interrupt_auto_resume: message not found msgid=%s", interrupt_msgid)
        return

    existing_event_data = msg.event_data or {}
    delay_seconds = resolve_auto_resume_delay_seconds(existing_event_data)
    auto_resume_at = utc_isoformat(datetime.utcnow() + timedelta(seconds=delay_seconds))

    existing_interrupt = existing_event_data.get("interrupt_data")
    if not isinstance(existing_interrupt, dict):
        existing_interrupt = {}
    merged_interrupt = {
        **existing_interrupt,
        "auto_resume_at": auto_resume_at,
        "auto_continue_seconds": delay_seconds,
    }
    await async_update_message_event_data_partial(
        int(interrupt_msgid),
        {"interrupt_data": merged_interrupt},
    )

    await sqs_service.add_task_to_queue(auto_resume_payload, delay_seconds=delay_seconds)

    try:
        from ..services.redis.connection import get_redis_stream_service

        redis_service = await get_redis_stream_service()
        await redis_service.add_message(
            run_id,
            {
                "type": "interrupt_auto_resume_scheduled",
                "interrupt_msgid": interrupt_msgid,
                "auto_resume_at": auto_resume_at,
                "auto_continue_seconds": delay_seconds,
                "run_id": run_id,
                "thread_id": thread_id,
                "timestamp": utc_isoformat(datetime.utcnow()),
            },
        )
    except Exception as e:
        logger.warning("推送 interrupt_auto_resume_scheduled 失败（不影响 auto_resume）: %s", e)

    logger.info(
        "⏱️ full_auto 已入队 %ss 延迟自动 continue: run_id=%s, interrupt_msgid=%s, auto_resume_at=%s",
        delay_seconds,
        run_id,
        interrupt_msgid,
        auto_resume_at,
    )
