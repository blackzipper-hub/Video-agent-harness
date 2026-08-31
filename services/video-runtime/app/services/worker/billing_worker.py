"""
计费 Worker —— 任务完成后按 callback 自算成本扣款
=============================================

流程：
- billing_status=pending       → 优先使用 callback 自算成本（conversation_run.cost）扣款 → completed / failed
                                  若 callback 成本不可用则回退 LangSmith
- langsmith_status=pending     → 异步补写 langsmith_cost（仅做参考，不影响扣款）
- 成本未就绪时本次跳过，下次扫描再试（无重试上限）
- 扣款失败（如余额不足）直接标 failed
- 幂等：已扣款的只补写 run 状态

由 main 启动时一起拉起（与 Task Worker 同进程）。

单独运行（本地测试）：
    cd Cuti-VideoAgent
    ENVIRONMENT=local python -m app.services.worker.billing_worker
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Callable

from ...crud.conversation import (
    async_get_conversation_run_by_run_id,
    async_get_conversation_runs_by_billing_status,
    async_get_conversation_runs_by_langsmith_status,
    async_update_conversation_run_billing,
)
from ...models.task_status import BillingStatus, LangsmithStatus
from ...schemas.user import CreditOperationType
from ...services.agent.agent_router_service import AgentType

logger = logging.getLogger(__name__)

# 超过此时长的 run，LangSmith 查不到就不再重试
LANGSMITH_RETRY_CUTOFF = timedelta(hours=24)


def _is_run_expired(run: Any) -> bool:
    """run 创建时间超过 LANGSMITH_RETRY_CUTOFF（默认 24h）则视为过期，不再重试 LangSmith。"""
    created_at = getattr(run, "created_at", None)
    if created_at is None:
        return False
    now = datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (now - created_at) > LANGSMITH_RETRY_CUTOFF


# ==================== 计费类型映射 ====================

# agent_type（主流程）-> 计费操作类型
_AGENT_TYPE_TO_CREDIT: dict[AgentType, CreditOperationType] = {
    AgentType.VIDEO: CreditOperationType.VIDEO_GENERATION,
    AgentType.STORY: CreditOperationType.STORY_GENERATION,
    AgentType.MUSIC: CreditOperationType.MUSIC_GENERATION,
    AgentType.IMAGE: CreditOperationType.IMAGE_GENERATION,
}
# run_type（video 下 regenerate 等二级操作）-> 计费操作类型
_RUN_TYPE_REGENERATE_TO_CREDIT: dict[str, CreditOperationType] = {
    "regenerate_keyframes": CreditOperationType.REGENERATE_KEYFRAMES,
    "regenerate_videos": CreditOperationType.REGENERATE_VIDEOS,
    "regenerate_timeline": CreditOperationType.USE,
    "regenerate_characters": CreditOperationType.REGENERATE_CHARACTERS,
    "companion_chat": CreditOperationType.USE,
}


def _credit_action_for_agent_type(agent_type: Optional[str]) -> str:
    if not agent_type:
        return CreditOperationType.USE.value
    try:
        at = AgentType(agent_type.strip().lower())
        return _AGENT_TYPE_TO_CREDIT.get(at, CreditOperationType.USE).value
    except ValueError:
        return CreditOperationType.USE.value


def _credit_action_for_run(run: Any) -> str:
    rt = (run.run_type or "").strip().lower()
    if rt in _RUN_TYPE_REGENERATE_TO_CREDIT:
        return _RUN_TYPE_REGENERATE_TO_CREDIT[rt].value
    return _credit_action_for_agent_type(run.agent_type)


# ==================== 统一写 completed / failed ====================

async def _write_run_billing_completed(
    run_id: str,
    run: Any,
    cost_float: Optional[float],
    credits_int: Optional[int],
    redis_service: Any,
    langsmith_cost: Optional[float] = None,
    langsmith_status: Optional[str] = None,
) -> None:
    """写 run / task_record / Redis 为 completed。
    
    cost_float: 扣款使用的成本
    langsmith_cost: 写入 conversation_run.langsmith_cost 字段，与 cost（callback 统计值）区分
    langsmith_status: 可选，设置 langsmith_status（如 pending 用于后续异步补写 langsmith_cost）
    """
    await async_update_conversation_run_billing(
        run_id,
        BillingStatus.COMPLETED.value,
        cost_credits=cost_float,
        langsmith_cost=langsmith_cost,
        langsmith_status=langsmith_status,
        # cost 字段由 callback 在 astream finally 已写入，此处不覆盖
        cost_calculated=True,
        credits_deducted=True,
        credits_amount=credits_int,
    )
    if run.agent_type == "video":
        from ...crud.error_tracking import update_task_record
        task_updates: dict = {
            "billing_status": BillingStatus.COMPLETED.value,
            "cost": cost_float,
            "cost_calculated": True,
            "credits_deducted": True,
            "credits_amount": credits_int,
        }
        if langsmith_cost is not None:
            task_updates["langsmith_cost"] = langsmith_cost
        await update_task_record(run_id, task_updates)
    status = await redis_service.get_task_status(run_id)
    await redis_service.update_task_status(
        run_id, status.get("status", "") if status else "", billing_status=BillingStatus.COMPLETED.value
    )


async def _write_run_billing_failed(
    run_id: str,
    run: Any,
    redis_service: Any,
    reason: str = "",
) -> None:
    """标记 billing_status = failed。"""
    await async_update_conversation_run_billing(
        run_id,
        BillingStatus.FAILED.value,
    )
    if run.agent_type == "video":
        from ...crud.error_tracking import update_task_record
        await update_task_record(run_id, {"billing_status": BillingStatus.FAILED.value})
    status = await redis_service.get_task_status(run_id)
    await redis_service.update_task_status(
        run_id, status.get("status", "") if status else "", billing_status=BillingStatus.FAILED.value
    )
    logger.warning(f"⚠️ 计费标记失败: run_id={run_id}, reason={reason}")


# ==================== 核心处理 ====================

async def process_one_billing_verify(
    run_id: str, user_id: str, redis_service: Any
) -> None:
    """
    对单个 run 按 callback 自算成本扣款，不再依赖 LangSmith。

    优先使用 conversation_run.cost（callback 自算成本）扣款；
    LangSmith 成本由 langsmith_status=pending 的补写循环异步填充，仅做参考。

    image agent 正常路径：task_worker 已直接扣款并标 COMPLETED，不会走到这里。
    仅在 task_worker 扣款异常 fallback 为 PENDING 时才进来，此时走幂等逻辑补写状态。
    """
    try:
        run = await async_get_conversation_run_by_run_id(run_id)
        if not run or run.billing_status != BillingStatus.PENDING.value:
            return

        from ...services.tool_service import CREDITS_PER_DOLLAR

        # 优先使用 callback 自算成本
        if run.cost is not None and run.cost > 0:
            cost_float = float(run.cost)
            actual_credits = int(cost_float * CREDITS_PER_DOLLAR)
            logger.info(
                f"使用 callback 成本扣款: run_id={run_id}, "
                f"callback_cost=${cost_float:.6f}, credits={actual_credits}"
            )
            # langsmith_status=PENDING 让补写循环异步填充 langsmith_cost
            ls_status = None
            if not hasattr(run, 'langsmith_status') or run.langsmith_status != LangsmithStatus.COMPLETED.value:
                ls_status = LangsmithStatus.PENDING.value
            await _process_deduct(
                run_id, user_id, run, actual_credits, cost_float, redis_service,
                langsmith_cost=None, langsmith_status=ls_status,
            )
            return

        # callback 成本不可用（遗留 run 或异常情况）：回退到 LangSmith
        from ...utils.credit_deduction_utils import get_cost_and_credits_for_run
        cost_float, actual_credits = await get_cost_and_credits_for_run(run_id)
        if cost_float is None or actual_credits is None:
            # 超过 24h 仍取不到，放弃并标 failed
            if _is_run_expired(run):
                logger.warning(
                    f"callback 成本为空且 LangSmith 超时放弃: run_id={run_id}, "
                    f"created_at={getattr(run, 'created_at', '?')}"
                )
                await _write_run_billing_failed(run_id, run, redis_service, "callback 成本为空且 LangSmith 超时")
                return
            logger.info(f"callback 成本为空且 LangSmith 成本未就绪，跳过本次: run_id={run_id}")
            return

        logger.info(
            f"callback 成本不可用，回退 LangSmith 扣款: run_id={run_id}, "
            f"langsmith_cost=${cost_float:.6f}, credits={actual_credits}"
        )
        await _process_deduct(run_id, user_id, run, actual_credits, cost_float, redis_service, langsmith_cost=cost_float)

    except Exception as e:
        logger.error(f"计费核实失败: run_id={run_id}, error={e}", exc_info=True)


async def _process_deduct(
    run_id: str,
    user_id: str,
    run: Any,
    actual_credits: int,
    cost_float: float,
    redis_service: Any,
    langsmith_cost: Optional[float] = None,
    langsmith_status: Optional[str] = None,
) -> None:
    """按实际成本扣款，成功写 completed，失败标 failed。
    
    langsmith_cost: 写入 langsmith_cost 字段，与 cost（callback 统计值）区分。
    langsmith_status: 可选，透传给 _write_run_billing_completed。
    """
    from ...utils.credit_deduction_utils import (
        deduct_credits_with_cost,
        already_deducted_for_run,
    )

    action = _credit_action_for_run(run)

    # 幂等：已扣款，只补写 billing_status/langsmith_cost，不覆盖 credits_amount（保留首次扣款写入的值）
    if await already_deducted_for_run(user_id, run_id):
        await _write_run_billing_completed(
            run_id, run,
            cost_float=cost_float,
            credits_int=None,   # 不覆盖 credits_amount，保留首次扣款写入的值
            redis_service=redis_service,
            langsmith_cost=langsmith_cost,
            langsmith_status=langsmith_status,
        )
        logger.info(f"计费补写(已扣过): run_id={run_id}")
        return

    # 扣款（使用已取到的 cost_float/actual_credits）
    success, credits_amount, cost = await deduct_credits_with_cost(
        run_id=run_id, user_id=user_id, action=action,
        cost_float=cost_float, credits_int=actual_credits,
    )
    if success:
        cost_f = float(cost) if cost is not None else cost_float
        cred_i = int(credits_amount) if credits_amount is not None else actual_credits
        await _write_run_billing_completed(
            run_id, run, cost_f, cred_i, redis_service,
            langsmith_cost=langsmith_cost, langsmith_status=langsmith_status,
        )
        logger.info(
            f"✅ 计费扣款完成: run_id={run_id}, action={action}, "
            f"积分={cred_i}, 成本=${cost_f}"
        )
    else:
        await _write_run_billing_failed(
            run_id, run, redis_service,
            "扣款失败（积分不足或其他错误）",
        )


# ==================== 扫描循环 ====================

async def process_one_langsmith_cost_fill(run_id: str) -> None:
    """
    langsmith_status=pending 的 run：补查 LangSmith 成本写入 langsmith_cost，标 langsmith_status=completed。
    billing_status 不变（image agent 已是 completed，扣款流程独立）。
    """
    try:
        run = await async_get_conversation_run_by_run_id(run_id)
        if not run or run.langsmith_status != LangsmithStatus.PENDING.value:
            return

        from ...utils.credit_deduction_utils import get_cost_and_credits_for_run
        cost_float, _ = await get_cost_and_credits_for_run(run_id)
        if cost_float is None:
            # 超过 24h 仍取不到，标 completed 不再重试（langsmith_cost 留空）
            if _is_run_expired(run):
                logger.warning(
                    "LangSmith 超时放弃补写: run_id=%s, created_at=%s",
                    run_id, getattr(run, 'created_at', '?'),
                )
                await async_update_conversation_run_billing(
                    run_id,
                    run.billing_status,
                    langsmith_status=LangsmithStatus.COMPLETED.value,
                )
                return
            logger.info("LangSmith 成本未就绪，跳过本次: run_id=%s", run_id)
            return

        # 只更新 langsmith_cost 和 langsmith_status，billing_status 保持不变
        await async_update_conversation_run_billing(
            run_id,
            run.billing_status,  # 保持原 billing_status 不变
            langsmith_status=LangsmithStatus.COMPLETED.value,
            langsmith_cost=cost_float,
        )
        if run.cost is not None and run.cost > 0:
            callback_cost = float(run.cost)
            logger.info(
                "成本对比: run_id=%s, callback_cost=$%.6f, langsmith_cost=$%.6f, diff=$%.6f",
                run_id, callback_cost, cost_float, abs(callback_cost - cost_float),
            )
        logger.info(
            "langsmith_cost 补写完成: run_id=%s, agent_type=%s, langsmith_cost=$%.6f",
            run_id, run.agent_type, cost_float,
        )
    except Exception as e:
        logger.error("langsmith_cost 补写异常: run_id=%s, error=%s", run_id, e, exc_info=True)


async def run_billing_scanner_loop(
    redis_service: Any,
    interval_seconds: int = 45,
    is_running: Optional[Callable[[], bool]] = None,
) -> None:
    get_running: Callable[[], bool] = is_running if is_running is not None else lambda: True
    logger.info(f"📋 计费扫描已启动: 每 {interval_seconds}s 从 conversation_runs 扫描 pending")
    while get_running():
        try:
            await asyncio.sleep(interval_seconds)
            if not get_running():
                break
            pending = await async_get_conversation_runs_by_billing_status("pending")
            for run_id, user_id in pending:
                await process_one_billing_verify(run_id, user_id, redis_service)
            pending_langsmith = await async_get_conversation_runs_by_langsmith_status("pending")
            for run_id, _ in pending_langsmith:
                await process_one_langsmith_cost_fill(run_id)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"计费扫描异常: {e}", exc_info=True)
    logger.info("📋 计费扫描已退出")


# ==================== Worker 类 ====================

class BillingWorker:
    """计费 Worker，与 TaskWorker 同风格：initialize() + start()，由 main 拉起。"""
    def __init__(self, interval_seconds: int = 45):
        self.redis_service = None
        self.running = False
        self.interval_seconds = interval_seconds

    async def initialize(self):
        from ...services.redis.connection import get_redis_stream_service
        self.redis_service = await get_redis_stream_service()

    async def start(self):
        self.running = True
        await run_billing_scanner_loop(
            self.redis_service,
            self.interval_seconds,
            is_running=lambda: self.running,
        )


# ==================== 独立运行入口 ====================

async def _standalone_main():
    """单独跑 billing worker（不启动整个 FastAPI 应用），用于本地测试计费流程。"""
    import os
    from dotenv import load_dotenv

    env = os.getenv("ENVIRONMENT", "local")
    load_dotenv(f".env.{env}" if env != "development" else ".env.development")

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
    )
    logger.info(f"🚀 Billing Worker 独立启动 (env={env})")

    # 初始化 asyncpg 连接池（billing worker 需要读写 DB）
    from ...models.database import init_asyncpg_pool, close_asyncpg_pool
    await init_asyncpg_pool()

    try:
        worker = BillingWorker(interval_seconds=5)
        await worker.initialize()
        logger.info("📋 按 Ctrl+C 退出")
        await worker.start()
    except KeyboardInterrupt:
        logger.info("⏹️ 收到退出信号")
    finally:
        await close_asyncpg_pool()
        logger.info("✅ 已退出")


if __name__ == "__main__":
    asyncio.run(_standalone_main())
