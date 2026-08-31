from __future__ import annotations

import asyncio
import json
import logging
import re

import asyncpg

from app.chat.v2.models import SourceEvent

logger = logging.getLogger(__name__)

TERMINAL_EVENT_TYPES = {
    "story_agent_generated", "image_agent_generated", "music_agent_generated",
    "video_agent_generated", "video_generated", "final_video_generated",
    "error", "failed", "cancelled", "skill_failed",
}
INTERRUPT_EVENT_TYPES = {"interrupt"}
ACTIVE_FOLLOWUP_STATUSES = {
    "queued", "running", "resume_queued", "pending", "completed", "interrupted",
}


def decode_event_data(value) -> dict:
    for _ in range(3):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def generated_content(event_type: str, content: str, payload: dict) -> str:
    key = {
        "story_agent_generated": "story_content",
        "image_agent_generated": "image_content",
        "music_agent_generated": "music_content",
        "video_agent_generated": "video_content",
        "video_generated": "video_content",
        "final_video_generated": "video_content",
    }.get(event_type)
    return str(
        payload.get(key) or payload.get("summary") or payload.get("content")
        or content or ""
    )


def markdown_media(value: str) -> tuple[str | None, str | None]:
    match = re.search(r"\[([^\]]+)\]\((https?://[^)]+)\)", value or "")
    return (match.group(1), match.group(2)) if match else (None, None)


def payload_media_uri(payload: dict) -> str | None:
    for key in ("url", "uri", "video_url", "image_url", "audio_url"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    for collection_name in ("videos", "images", "audio"):
        items = payload.get(collection_name)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("video_url", "image_url", "audio_url", "url", "uri"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


class V2ResultReconciler:
    def __init__(
        self, database_url: str, *, schema: str = "public",
        interval_seconds: float = 2.0,
    ):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
            raise ValueError("invalid VideoAgent result schema")
        self.database_url = database_url
        self.schema = schema
        self.interval_seconds = max(0.25, interval_seconds)
        self.pool: asyncpg.Pool | None = None
        self.task: asyncio.Task | None = None
        self.stopping = asyncio.Event()

    async def start(self, harness) -> None:
        self.pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=3)
        self.stopping.clear()
        self.task = asyncio.create_task(self._loop(harness), name="v2-result-reconciler")

    async def stop(self) -> None:
        self.stopping.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.pool:
            await self.pool.close()

    async def _loop(self, harness) -> None:
        while not self.stopping.is_set():
            try:
                await self.reconcile_once(harness)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("V2 VideoAgent result reconciliation failed")
            try:
                await asyncio.wait_for(self.stopping.wait(), self.interval_seconds)
            except TimeoutError:
                pass

    async def reconcile_once(self, harness) -> int:
        accepted = 0
        for task in await harness.repo.list_waiting_tasks():
            if not task.remote_operation_id:
                continue
            operation_ids = await self._operation_ids_for_task(task)
            for operation_id in operation_ids:
                for event in await self._events(operation_id):
                    if await harness.ingest_source_event(event):
                        accepted += 1
        return accepted

    async def _operation_ids_for_task(self, task) -> list[str]:
        """Poll the mapped remote run and any newer resume run on the same thread."""
        if self.pool is None:
            raise RuntimeError("result reconciler is not started")
        operation_id = task.remote_operation_id
        ids = [operation_id]
        thread_id = task.remote_thread_id
        if not thread_id:
            return ids
        followup = await self.pool.fetchrow(
            f"""SELECT run_id, status FROM {self.schema}.conversation_runs
            WHERE thread_id=$1 AND run_id <> $2
            ORDER BY id DESC LIMIT 1""",
            thread_id,
            operation_id,
        )
        if followup and followup["status"] in ACTIVE_FOLLOWUP_STATUSES:
            ids.append(followup["run_id"])
        return ids

    async def _events(self, operation_id: str) -> list[SourceEvent]:
        if self.pool is None:
            raise RuntimeError("result reconciler is not started")
        rows = await self.pool.fetch(
            f"""SELECT id,event_type,content,event_data
            FROM {self.schema}.conversation_messages
            WHERE run_id=$1 ORDER BY sequence,id""", operation_id,
        )
        events = []
        for row in rows:
            if not row["event_type"]:
                continue
            payload = decode_event_data(row["event_data"])
            payload.setdefault("message_id", row["id"])
            events.append(SourceEvent(
                remote_run_id=operation_id, event_type=row["event_type"],
                source_event_id=f"va-message:{row['id']}",
                content=row["content"] or "",
                payload=payload,
            ))
        if any(item.event_type in TERMINAL_EVENT_TYPES for item in events):
            return events
        run = await self.pool.fetchrow(
            f"""SELECT id,status,error_message FROM {self.schema}.conversation_runs
            WHERE run_id=$1 ORDER BY id DESC LIMIT 1""", operation_id,
        )
        if run and run["status"] in {"failed", "cancelled"}:
            events.append(SourceEvent(
                remote_run_id=operation_id, event_type=run["status"],
                source_event_id=f"va-run:{run['id']}:{run['status']}",
                content=run["error_message"] or f"Downstream run {run['status']}",
                payload={"status": run["status"]},
            ))
        elif run and run["status"] == "interrupted":
            if not any(item.event_type in INTERRUPT_EVENT_TYPES for item in events):
                events.append(SourceEvent(
                    remote_run_id=operation_id, event_type="interrupt",
                    source_event_id=f"va-run:{run['id']}:interrupted",
                    content=run["error_message"] or "Downstream run interrupted",
                    payload={"status": "interrupted"},
                ))
        return events
