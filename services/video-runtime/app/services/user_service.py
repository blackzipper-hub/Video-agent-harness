import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert
from sqlmodel import and_
from ..models.user import UserCredit, CreditHistory, CreditOperationType
from ..models.subscription import CreditAllocation
from ..exceptions import BusinessException, BusinessExceptionCode
from datetime import datetime

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_user_credits(self, user_id: str) -> Optional[UserCredit]:
        """获取用户积分信息"""
        statement = select(UserCredit).where(UserCredit.user_id == user_id)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()
    
    async def use_credits(
        self,
        user_id: str,
        amount: int,
        operation_type: str,
        description: str = "",
        reference_id: Optional[str] = None,
        allow_negative: bool = False,
    ) -> UserCredit:
        """
        使用用户积分，优先使用订阅积分（即将过期），然后使用购买积分（永不过期）

        Priority order:
        1. Subscription credits (expiring soon first)
        2. Purchased credits (never expire)

        allow_negative: 若为 True，允许扣款后余额为负（如任务完成后扣款场景）。
        """
        if amount <= 0:
            raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "使用积分量必须为正数")

        credit_query = select(UserCredit).where(UserCredit.user_id == user_id).with_for_update()
        result = await self.db.execute(credit_query)
        credit = result.scalar_one_or_none()

        if not credit:
            raise BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "用户积分账户不存在")

        # 检查积分余额（任务扣款场景可允许扣成负数）
        if not allow_negative and credit.balance < amount:
            raise BusinessException(BusinessExceptionCode.INSUFFICIENT_CREDITS)

        remaining_to_deduct = amount

        # Priority 1: Use subscription credits first (they expire)
        from_subscription = 0
        if hasattr(credit, 'subscription_balance') and credit.subscription_balance > 0 and remaining_to_deduct > 0:
            from_subscription = min(credit.subscription_balance, remaining_to_deduct)
            credit.subscription_balance -= from_subscription
            remaining_to_deduct -= from_subscription

            # Deduct from specific allocations (oldest expiration first)
            if from_subscription > 0:
                await self._deduct_from_allocations(user_id, from_subscription)

        # Priority 2: Use purchased credits (they don't expire)
        from_purchased = 0
        if hasattr(credit, 'purchased_balance') and credit.purchased_balance > 0 and remaining_to_deduct > 0:
            from_purchased = min(credit.purchased_balance, remaining_to_deduct)
            credit.purchased_balance -= from_purchased
            remaining_to_deduct -= from_purchased

        # Update total balance and usage
        credit.balance -= amount
        credit.total_used += amount
        credit.updated_at = datetime.utcnow()

        # Record credit history entries for each source
        history_entries = []

        if from_subscription > 0:
            history_entries.append(CreditHistory(
                user_id=user_id,
                amount=-from_subscription,
                balance=credit.balance + from_purchased,  # Balance after subscription deduction
                operation_type=operation_type,
                description=f"{description} (订阅积分)",
                reference_id=reference_id,
                credit_source="subscription" if hasattr(CreditHistory, 'credit_source') else None
            ))

        if from_purchased > 0:
            history_entries.append(CreditHistory(
                user_id=user_id,
                amount=-from_purchased,
                balance=credit.balance,  # Final balance
                operation_type=operation_type,
                description=f"{description} (购买积分)",
                reference_id=reference_id,
                credit_source="purchased" if hasattr(CreditHistory, 'credit_source') else None
            ))

        # Fallback: if no source tracking, create a single entry
        if not history_entries:
            history_entries.append(CreditHistory(
                user_id=user_id,
                amount=-amount,
                balance=credit.balance,
                operation_type=operation_type,
                description=description,
                reference_id=reference_id,
            ))

        try:
            self.db.add(credit)
            for entry in history_entries:
                self.db.add(entry)
            await self.db.commit()
            await self.db.refresh(credit)
        except Exception as e:
            # 防御：reference_id 列不存在时用 raw insert（不含 reference_id）重试
            if reference_id is not None and "reference_id" in str(e):
                await self.db.rollback()
                logger.warning("credit_history.reference_id 列不存在（请执行 init/migrate_billing_fields.py），本次不写 reference_id")
                self.db.add(credit)
                now = datetime.utcnow()
                for entry in history_entries:
                    await self.db.execute(
                        insert(CreditHistory.__table__).values(
                            user_id=entry.user_id,
                            amount=entry.amount,
                            balance=entry.balance,
                            operation_type=entry.operation_type,
                            description=entry.description,
                            credit_source=getattr(entry, "credit_source", None),
                            allocation_id=getattr(entry, "allocation_id", None),
                            expires_at=getattr(entry, "expires_at", None),
                            created_at=now,
                            updated_at=now,
                        )
                    )
                await self.db.commit()
                await self.db.refresh(credit)
            else:
                await self.db.rollback()
                logger.error(f"使用积分失败: {str(e)}")
                raise BusinessException(BusinessExceptionCode.INTERNAL_SERVER_ERROR, f"使用积分失败: {str(e)}")

        logger.info(
            f"Used {amount} credits for user {user_id}: "
            f"{from_subscription} from subscription, {from_purchased} from purchased"
        )
        try:
            from ..services.redis.connection import get_redis_client
            redis_client = await get_redis_client(decode_responses=True)
            await redis_client.delete(f"user_credit_balance:{user_id}")
        except Exception:
            pass
        return credit

    async def refund_credits(
        self,
        user_id: str,
        amount: int,
        operation_type: str = "settle_refund",
        description: str = "",
        reference_id: Optional[str] = None,
    ) -> Optional[UserCredit]:
        """
        退回积分到用户账户（结算退回 / 失败退回）。
        退回优先加到 purchased_balance（简化处理，不拆回订阅池）。
        """
        if amount <= 0:
            return None
        try:
            credit_query = select(UserCredit).where(UserCredit.user_id == user_id).with_for_update()
            result = await self.db.execute(credit_query)
            credit = result.scalar_one_or_none()
            if not credit:
                logger.warning(f"退回积分: 用户积分账户不存在 user_id={user_id}")
                return None

            credit.balance += amount
            if hasattr(credit, "purchased_balance") and credit.purchased_balance is not None:
                credit.purchased_balance += amount
            if credit.total_used >= amount:
                credit.total_used -= amount
            else:
                credit.total_used = 0
            credit.updated_at = datetime.utcnow()

            history = CreditHistory(
                user_id=user_id,
                amount=amount,  # 正数 = 退回
                balance=credit.balance,
                operation_type=operation_type,
                description=description,
                reference_id=reference_id,
            )
            self.db.add(credit)
            self.db.add(history)
            await self.db.commit()
            await self.db.refresh(credit)

            # 清缓存
            try:
                from ..services.redis.connection import get_redis_client
                redis_client = await get_redis_client(decode_responses=True)
                await redis_client.delete(f"user_credit_balance:{user_id}")
            except Exception:
                pass

            logger.info(f"✅ 退回积分: user_id={user_id}, amount={amount}, 余额={credit.balance}")
            return credit
        except Exception as e:
            await self.db.rollback()
            logger.error(f"退回积分失败: user_id={user_id}, amount={amount}, error={e}")
            return None

    async def _deduct_from_allocations(self, user_id: str, amount: int) -> None:
        """
        从用户的订阅积分分配中扣除，优先扣除即将过期的
        """
        allocations_result = await self.db.execute(
            select(CreditAllocation)
            .where(
                and_(
                    CreditAllocation.user_id == user_id,
                    CreditAllocation.is_expired == False,
                    CreditAllocation.remaining_credits > 0
                )
            )
            .order_by(CreditAllocation.expires_at.asc())
            .with_for_update()
        )
        allocations = allocations_result.scalars().all()

        remaining = amount
        for allocation in allocations:
            if remaining <= 0:
                break

            deduct_from_this = min(allocation.remaining_credits, remaining)
            allocation.remaining_credits -= deduct_from_this
            allocation.updated_at = datetime.utcnow()

            self.db.add(allocation)
            remaining -= deduct_from_this

            logger.debug(
                f"Deducted {deduct_from_this} from allocation {allocation.id}, "
                f"remaining in allocation: {allocation.remaining_credits}"
            )
