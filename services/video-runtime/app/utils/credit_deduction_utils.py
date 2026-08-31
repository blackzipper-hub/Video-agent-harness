"""
积分扣除工具
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

# ==================== 入口预检查门槛（固定值，不按类型预估） ====================
# 任务开始前仅检查余额 > 0，不扣款；实际扣款由 billing_worker 按成本结算
ENTRY_CHECK_CREDITS = 1


# ==================== 入口预检查（不预扣） ====================

async def check_credits_before_task(user_id: str) -> Tuple[bool, int, str]:
    """
    任务开始前仅检查积分余额是否达到固定门槛，不扣款。
    余额 < ENTRY_CHECK_CREDITS 时拒绝任务。

    Returns:
        (是否通过, 门槛积分数, 失败原因)
    """
    required = ENTRY_CHECK_CREDITS
    # 开源/自托管：DISABLE_BILLING=True 时直接放行，不读取计费表
    from ..config import get_settings
    if get_settings().DISABLE_BILLING:
        return True, 0, ""
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


# ==================== 幂等检查 ====================

async def already_deducted_for_run(user_id: str, run_id: str) -> bool:
    """是否已为该 run 扣过款（查 credit_history.reference_id == run_id）。存在一条即视为已扣（允许多条历史记录）。"""
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(CreditHistory).where(
                CreditHistory.user_id == user_id,
                CreditHistory.reference_id == run_id,
            )
            result = await session.execute(stmt)
            # 用 first() 不要求唯一，多条记录（如历史重试）也视为已扣
            return result.scalars().first() is not None
    except Exception as e:
        if "reference_id" in str(e):
            logger.warning("credit_history.reference_id 列不存在（需迁移），视为未扣款")
            return False
        logger.error(f"already_deducted_for_run 异常: {e}")
        return False


# ==================== LangSmith 成本查询 ====================

async def get_cost_and_credits_for_run(run_id: str) -> Tuple[Optional[float], Optional[int]]:
    """仅查 LangSmith 成本并换算积分数，不扣款。"""
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


# ==================== 任务完成后扣款 ====================

async def deduct_credits_with_cost(
    run_id: str,
    user_id: str,
    action: str,
    cost_float: float,
    credits_int: int,
) -> Tuple[bool, int, float]:
    """
    使用已获取的成本/积分数直接扣款，不再请求 LangSmith。
    供 billing_worker 在 get_cost_and_credits_for_run 之后调用，避免同一 run 请求两次 get_total_cost。
    """
    # 开源/自托管：DISABLE_BILLING=True 时不扣款、不写 credit_history
    from ..config import get_settings
    if get_settings().DISABLE_BILLING:
        logger.info(f"DISABLE_BILLING=True，跳过扣款: run_id={run_id}")
        return True, 0, cost_float
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
    """内部实现：在给定 session 下扣款并写 credit_history，供 deduct_credits_with_cost 使用。"""
    from ..services.user_service import UserService
    from ..exceptions import BusinessException, BusinessExceptionCode
    from ..schemas.user import CreditOperationType

    user_service = UserService(session)
    user_credit = await user_service.get_user_credits(user_id)
    if not user_credit:
        logger.warning(f"用户积分账户不存在: user_id={user_id}")
        return False, required_credits, cost

    # 任务完成后扣款允许余额为负（不在此处拒绝，由 use_credits(allow_negative=True) 执行）
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
