"""任务队列 provider：按 QUEUE_BACKEND 选择 SQS（默认/生产）或 Redis（开源自托管）。"""
import logging
from typing import Optional

from ...config import get_settings

logger = logging.getLogger(__name__)


def create_task_queue(queue_url: Optional[str] = None, environment: Optional[str] = None):
    """工厂：返回与 SQSTaskService 接口一致的任务队列实例。

    - QUEUE_BACKEND=redis：RedisTaskQueue（复用现有 Redis，无需 AWS SQS）
    - 其它（默认 sqs）：SQSTaskService（真实 AWS SQS / elasticmq / moto）
    """
    backend = (getattr(get_settings(), "QUEUE_BACKEND", "sqs") or "sqs").lower()
    if backend == "redis":
        from .redis_task_queue import RedisTaskQueue
        return RedisTaskQueue(queue_url=queue_url, environment=environment)
    from ..aws.sqs_service import SQSTaskService
    return SQSTaskService(queue_url=queue_url, environment=environment)


__all__ = ["create_task_queue"]
