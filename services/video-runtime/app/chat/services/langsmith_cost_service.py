"""
LangSmith 成本获取服务（与 Cuti-VideoAgent 对齐）
"""
import logging
import asyncio
from decimal import Decimal
from typing import Optional
from langsmith import Client

logger = logging.getLogger(__name__)


class LangSmithCostService:
    """LangSmith 成本获取：read_run(run_id)，仅在 run 已终态时才返回 total_cost。"""

    def __init__(self):
        self.client = Client()

    def _run_cost(self, run) -> Optional[Decimal]:
        if run and getattr(run, "total_cost", None) is not None:
            return run.total_cost
        return None

    def _run_finished(self, run) -> bool:
        if not run:
            return False
        if getattr(run, "end_time", None) is None:
            return False
        status = (getattr(run, "status", None) or "").lower()
        return status != "pending"

    async def get_total_cost(
        self,
        run_id: str,
        max_retries: int = 6,
        retry_delay: int = 15,
    ) -> Optional[Decimal]:
        for attempt in range(max_retries):
            try:
                run = await asyncio.to_thread(self.client.read_run, run_id=run_id)
                if self._run_finished(run):
                    cost = self._run_cost(run)
                    if cost is not None:
                        logger.info(
                            f"✅ 成功获取LangSmith成本: run_id={run_id}, "
                            f"status={getattr(run, 'status', '?')}, cost=${cost}"
                        )
                        return cost
                    logger.info(
                        f"✅ LangSmith run 已终态但无成本（零成本）: run_id={run_id}, "
                        f"status={getattr(run, 'status', '?')}"
                    )
                    return Decimal(0)
                status = getattr(run, "status", None) or "?"
                end = getattr(run, "end_time", None)
                if attempt < max_retries - 1:
                    logger.info(
                        f"⏳ LangSmith 成本未就绪（status={status}, end_time={'Y' if end else 'N'}），"
                        f"{retry_delay}s 后重试 ({attempt + 1}/{max_retries}): run_id={run_id}"
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    break
            except Exception as e:
                logger.error(f"❌ 获取LangSmith成本失败: run_id={run_id}, error={e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                else:
                    return None
        logger.warning(f"⚠️ 无法获取LangSmith成本（run 未终态，已重试{max_retries}次）: run_id={run_id}")
        return None


_cost_service = None


def get_langsmith_cost_service() -> LangSmithCostService:
    global _cost_service
    if _cost_service is None:
        _cost_service = LangSmithCostService()
    return _cost_service
