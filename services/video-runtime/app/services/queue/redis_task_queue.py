"""
Redis 任务队列后端（开源自托管用，复用现有 Redis，无需 AWS SQS / elasticmq / moto）。

对齐 SQSTaskService 的公开接口，做到 drop-in：
- add_task_to_queue(task_data, delay_seconds) -> message_id
- consume_task(max_messages, wait_time_seconds) -> {message_id, receipt_handle, data} | None
- delete_task(receipt_handle)
- extend_visibility_timeout(receipt_handle, seconds)
- get_queue_attributes() / get_queue_length()

可靠队列语义（at-least-once）：
- pending(LIST)：待消费信封；delayed(ZSET)：延迟可见（对齐 SQS DelaySeconds，用于 15s 自动 continue）
- inflight(HASH)+inflight_deadline(ZSET)：在途消息 + 可见性超时；超时未 delete 则重回 pending
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from ..redis.connection import get_redis_client
from ...config import get_settings

logger = logging.getLogger(__name__)

# 默认可见性超时（秒），对齐 SQS 默认；worker 会周期性 extend
_DEFAULT_VISIBILITY_SECONDS = 300
# 单次阻塞 pop 的粒度（秒）：小粒度以便及时提升 delayed / 回收过期在途
_POP_GRANULARITY_SECONDS = 2


class RedisTaskQueue:
    """基于 Redis 的任务队列（SQSTaskService 兼容实现）。"""

    def __init__(self, queue_url: Optional[str] = None, environment: Optional[str] = None):
        settings = get_settings()
        env = environment or settings.ENVIRONMENT.value
        env_prefix_map = {"local": "local", "development": "dev", "production": "prod"}
        prefix = env_prefix_map.get(env, env)
        base = f"cuti-videoagent:{prefix}:taskq"
        self.queue_url = queue_url or base  # 仅用于日志/监控展示
        self._k_pending = f"{base}:pending"
        self._k_delayed = f"{base}:delayed"
        self._k_inflight = f"{base}:inflight"
        self._k_inflight_deadline = f"{base}:inflight_deadline"
        self._redis = None
        logger.info(f"🔧 Redis Task Queue 初始化: 环境={env}, 前缀={base}")

    async def _client(self):
        if self._redis is None:
            self._redis = await get_redis_client(decode_responses=True)
        return self._redis

    async def add_task_to_queue(self, task_data: dict, delay_seconds: int = 0) -> str:
        """入队。delay_seconds>0 时进入延迟集合（0–900，对齐 SQS）。返回 message_id。"""
        message_id = uuid.uuid4().hex
        envelope = json.dumps(
            {"message_id": message_id, "data": task_data},
            ensure_ascii=False,
            default=str,
        )
        size = len(envelope.encode("utf-8"))
        if size > 256 * 1024:
            raise ValueError(
                f"任务数据太大 ({size} bytes)，超过 256KB。考虑将大文件存 S3/本地存储，消息里只放 URL。"
            )
        r = await self._client()
        if delay_seconds and delay_seconds > 0:
            deliver_at = time.time() + min(900, delay_seconds)
            await r.zadd(self._k_delayed, {envelope: deliver_at})
        else:
            await r.lpush(self._k_pending, envelope)
        logger.info(f"✅ 任务已入 Redis 队列: message_id={message_id}, run_id={task_data.get('run_id')}")
        return message_id

    async def _promote_delayed(self, r) -> None:
        """将到期的延迟消息移入 pending。"""
        now = time.time()
        due = await r.zrangebyscore(self._k_delayed, "-inf", now)
        for envelope in due:
            # zrem 返回 1 才 lpush，避免并发下重复投递
            if await r.zrem(self._k_delayed, envelope):
                await r.lpush(self._k_pending, envelope)

    async def _requeue_expired_inflight(self, r) -> None:
        """将可见性超时仍未 delete 的在途消息重回 pending（at-least-once）。"""
        now = time.time()
        expired = await r.zrangebyscore(self._k_inflight_deadline, "-inf", now)
        for rh in expired:
            if await r.zrem(self._k_inflight_deadline, rh):
                envelope = await r.hget(self._k_inflight, rh)
                await r.hdel(self._k_inflight, rh)
                if envelope:
                    await r.lpush(self._k_pending, envelope)
                    logger.warning(f"♻️ 在途消息可见性超时，重回队列: receipt={rh[:12]}...")

    async def consume_task(self, max_messages: int = 1, wait_time_seconds: int = 20) -> Optional[Dict[str, Any]]:
        """长轮询消费一条消息。返回 {message_id, receipt_handle, data} 或 None。"""
        r = await self._client()
        deadline = time.time() + max(0, wait_time_seconds)
        while True:
            await self._promote_delayed(r)
            await self._requeue_expired_inflight(r)
            remaining = deadline - time.time()
            block = max(0, min(_POP_GRANULARITY_SECONDS, int(remaining) if remaining > 0 else 0))
            # brpop 阻塞 block 秒；block=0 时做一次非阻塞 rpop
            if block > 0:
                popped = await r.brpop(self._k_pending, timeout=block)
                value = popped[1] if popped else None
            else:
                value = await r.rpop(self._k_pending)

            if value is not None:
                try:
                    envelope = json.loads(value)
                except json.JSONDecodeError as e:
                    logger.error(f"❌ 解析任务 JSON 失败: {e}, body: {value[:100]}")
                    continue
                receipt_handle = uuid.uuid4().hex
                await r.hset(self._k_inflight, receipt_handle, value)
                await r.zadd(
                    self._k_inflight_deadline,
                    {receipt_handle: time.time() + _DEFAULT_VISIBILITY_SECONDS},
                )
                return {
                    "message_id": envelope.get("message_id", receipt_handle),
                    "receipt_handle": receipt_handle,
                    "data": envelope.get("data", {}),
                }

            if time.time() >= deadline:
                return None

    async def extend_visibility_timeout(self, receipt_handle: str, visibility_timeout_seconds: int = 300):
        """延长在途消息的可见性超时（长任务用）。receipt 已失效则忽略。"""
        r = await self._client()
        try:
            if await r.hexists(self._k_inflight, receipt_handle):
                await r.zadd(
                    self._k_inflight_deadline,
                    {receipt_handle: time.time() + visibility_timeout_seconds},
                )
        except Exception as e:  # 后台操作，不影响主任务
            logger.warning(f"⚠️ 延长可见性失败: {e}")

    async def delete_task(self, receipt_handle: str):
        """确认完成，删除在途消息。"""
        r = await self._client()
        await r.hdel(self._k_inflight, receipt_handle)
        await r.zrem(self._k_inflight_deadline, receipt_handle)
        logger.debug(f"✅ 任务已从 Redis 队列删除: receipt={receipt_handle[:12]}...")

    async def get_queue_attributes(self) -> Dict[str, Any]:
        r = await self._client()
        pending = await r.llen(self._k_pending)
        delayed = await r.zcard(self._k_delayed)
        inflight = await r.hlen(self._k_inflight)
        return {
            "ApproximateNumberOfMessages": str(pending + delayed),
            "ApproximateNumberOfMessagesNotVisible": str(inflight),
            "ApproximateNumberOfMessagesDelayed": str(delayed),
        }

    async def get_queue_length(self) -> int:
        r = await self._client()
        pending = await r.llen(self._k_pending)
        delayed = await r.zcard(self._k_delayed)
        return int(pending + delayed)
