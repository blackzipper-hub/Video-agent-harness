"""
LangSmith 成本获取服务

要点（文档 https://docs.smith.langchain.com/reference/python/client）：
1. 自己设置 run_id：必须在 invoke 时把 run_id 放在 config 顶层（config.run_id），
   不能只放在 configurable.run_id，否则 LangSmith tracer 会生成新 id，read_run(我们的 id) 会 404。
2. 取 cost：read_run(run_id).total_cost；需等 run 终态才稳定。
   LangSmith Run.status 是服务端计算的字符串，SDK 里无 enum（schemas.py 只标
   ``status: Optional[str] = None  # "Status of the run (e.g., 'success')."``）。
   OpenAPI spec 里 Create/Update run 也没有 status 字段——它由服务端根据
   end_time 和 error 推导：
     - 无 end_time           → "pending"（仍在运行；total_cost 可能非空但未聚合完）
     - 有 end_time 且无 error → "success"
     - 有 end_time 且有 error → "error"
   实测（30 条 root runs）也只出现过这三个值。
   **判断终态**：同时检查 end_time 非空 + status != "pending"，双重保险。
3. regenerate 用 get_current_run_tree().id 时能查到，因为那是 tracer 当前上下文给的 id。
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
        """run 已终态：end_time 非空 且 status 不是 pending。
        双重检查确保即使 LangSmith 后续新增状态值也不会误判。
        pending 状态下 total_cost 也可能非空（子 run 不断聚合），但值不稳定。
        """
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
        """
        从 LangSmith 获取 run 的总成本。
        - run 已终态（end_time 非空 + status 非 pending）且 total_cost 非空：返回成本值
        - run 已终态但 total_cost 为 None：返回 Decimal(0)（零成本 run，如纯图片生成无 LLM 调用）
        - run 未终态：重试等待；超过 max_retries 次后返回 None（触发 billing_worker 下次再试）
        """
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
                    # 已终态但无 cost：零成本 run（无 LLM token 计费），直接返回 0
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


# 单例
_cost_service = None

def get_langsmith_cost_service() -> LangSmithCostService:
    global _cost_service
    if _cost_service is None:
        _cost_service = LangSmithCostService()
    return _cost_service
