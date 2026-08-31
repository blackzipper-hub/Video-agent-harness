"""
积分扣除工具（与 Cuti-VideoAgent 对齐）
============
- 任务开始前：check_credits_before_task 仅检查余额门槛，不扣款。
- 任务完成后：billing_worker 优先使用 callback 自算成本（conversation_run.cost）扣款；
  callback 成本不可用时回退 get_cost_and_credits_for_run（LangSmith）。
- 幂等：扣款以 reference_id = run_id 去重。
"""
import logging
from typing import Optional, Tuple

from sqlmodel import select

from ..services.langsmith_cost_service import get_langsmith_cost_service
from ..services.tool_service import CREDITS_PER_DOLLAR
from ..models.database import AsyncSessionLocal
from ..models.user import CreditHistory

logger = logging.getLogger(__name__)

ENTRY_CHECK_CREDITS = 1


async def check_credits_before_task(user_id: str) -> Tuple[bool, int, str]:
    required = ENTRY_CHECK_CREDITS
    try:
        async with AsyncSessionLocal() as session:
            from ..services.user_service import UserService

            user_service = UserService(session)
            user_credit = await user_service.get_user_credits(user_id)
            if not user_credit:
                return False, required, "用户积分账户不存在"
            if user_credit.balance < required:
                return False, required, f"积分不足（需要≥{required}，余额{user_credit.balance}）"
            return True, required, ""
    except Exception as e:
        logger.error(f"积分预检查失败: user_id={user_id}, error={e}")
        return False, required, str(e)


async def already_deducted_for_run(user_id: str, run_id: str) -> bool:
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(CreditHistory).where(
                CreditHistory.user_id == user_id,
                CreditHistory.reference_id == run_id,
            )
            result = await session.execute(stmt)
            return result.scalars().first() is not None
    except Exception as e:
        if "reference_id" in str(e):
            logger.warning("credit_history.reference_id 列不存在（需迁移），视为未扣款")
            return False
        logger.error(f"already_deducted_for_run 异常: {e}")
        return False


async def get_cost_and_credits_for_run(run_id: str) -> Tuple[Optional[float], Optional[int]]:
    try:
        cost_service = get_langsmith_cost_service()
        total_cost = await cost_service.get_total_cost(run_id)
        if total_cost is None:
            return None, None
        cost_float = float(total_cost)
        required_credits = int(cost_float * CREDITS_PER_DOLLAR)
        return cost_float, required_credits
    except Exception as e:
        logger.warning(f"get_cost_and_credits_for_run 失败: run_id={run_id}, error={e}")
        return None, None


async def deduct_credits_with_cost(
    run_id: str,
    user_id: str,
    action: str,
    cost_float: float,
    credits_int: int,
) -> Tuple[bool, int, float]:
    if credits_int <= 0:
        logger.info(f"无需扣除积分: run_id={run_id}")
        return True, 0, cost_float
    try:
        async with AsyncSessionLocal() as session:
            return await _deduct_credits_with_session(
                session, credits_int, user_id, action, cost_float, run_id
            )
    except Exception as e:
        logger.error(
            f"deduct_credits_with_cost 失败: run_id={run_id}, user_id={user_id}, error={e}",
            exc_info=True,
        )
        return False, credits_int, cost_float


async def _deduct_credits_with_session(
    session,
    required_credits: int,
    user_id: str,
    action: str,
    cost: float,
    run_id: str,
) -> Tuple[bool, int, float]:
    from ..services.user_service import UserService
    from ..exceptions import BusinessException, BusinessExceptionCode
    from ..schemas.user import CreditOperationType

    user_service = UserService(session)
    user_credit = await user_service.get_user_credits(user_id)
    if not user_credit:
        logger.warning(f"用户积分账户不存在: user_id={user_id}")
        return False, required_credits, cost

    operation_type = getattr(CreditOperationType, action.upper(), CreditOperationType.USE)
    try:
        updated_credit = await user_service.use_credits(
            user_id=user_id,
            amount=required_credits,
            operation_type=operation_type.value if isinstance(operation_type, CreditOperationType) else action,
            description="AI任务执行扣除",
            reference_id=run_id,
            allow_negative=True,
        )

        logger.info(
            f"✅ 成功扣除积分: user_id={user_id}, "
            f"积分={required_credits}, 余额={updated_credit.balance}"
        )
        return True, required_credits, cost
    except BusinessException as e:
        if e.error_code == BusinessExceptionCode.INSUFFICIENT_CREDITS:
            logger.warning(
                f"积分不足，无法扣除: user_id={user_id}, "
                f"需要={required_credits}, 余额={user_credit.balance}"
            )
        else:
            logger.error(f"扣除积分失败: user_id={user_id}, error={e}")
        return False, required_credits, cost

    except ImportError as e:
        logger.error(f"UserService不存在，无法扣除积分: {e}")
        return False, required_credits, cost
    except Exception as e:
        logger.error(f"扣除积分异常: user_id={user_id}, error={e}", exc_info=True)
        return False, required_credits, cost
