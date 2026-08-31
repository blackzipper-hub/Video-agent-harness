"""
视频资格 CRUD（async）
=====================
判断用户是否「曾真实付费/订阅」——仅看 Stripe 支付与订阅，不包含 admin 发放积分。
以 Go（feat_login/dev）为准：paymenttransaction 表，status 为 succeeded 或 completed。
"""
import logging
from typing import Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, and_

from ..models.database import AsyncSessionLocal
from ..models.payment import PaymentTransaction, PaymentStatus
from ..models.subscription import Subscription
from ..models.user import User, InviteCode

logger = logging.getLogger(__name__)


async def is_invite_code_user(user_id: str) -> bool:
    """是否邀请码用户：invite_codes 表存在 used_by_id=user_id 且已使用。
    覆盖「仅邀请码登录」与「邀请码+邮箱注册」两种（Go 里前者 auth_type=invite_code，后者 auth_type=email）。"""
    try:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(1)
                .select_from(InviteCode)
                .where(
                    InviteCode.used_by_id == user_id,
                    InviteCode.is_used == True,
                    InviteCode.is_deleted == False,
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.fetchone() is not None
    except Exception as e:
        logger.warning("video_eligibility: is_invite_code_user query failed: %s", e)
        return False


async def is_admin_user(user_id: str) -> bool:
    """是否管理员用户；管理员在视频资格校验时可跳过「需先付费/订阅」检查。"""
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(User.is_admin).where(
                User.user_id == user_id,
                User.is_deleted == False,
            ).limit(1)
            result = await session.execute(stmt)
            row = result.fetchone()
            return bool(row and row[0])
    except Exception as e:
        logger.warning("video_eligibility: is_admin_user query failed: %s", e)
        return False

_COMPLETED_STATUSES = (PaymentStatus.SUCCEEDED.value, PaymentStatus.COMPLETED.value)


async def has_user_completed_payment(session: AsyncSession, user_id: str) -> bool:
    """是否曾有一笔成功的 Stripe 一次性支付。以 Go 为准：succeeded / completed。"""
    try:
        stmt = (
            select(1)
            .select_from(PaymentTransaction)
            .where(
                and_(
                    PaymentTransaction.user_id == user_id,
                    PaymentTransaction.is_deleted == False,
                    PaymentTransaction.status.in_(_COMPLETED_STATUSES),
                )
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.fetchone() is not None
    except Exception as e:
        logger.warning("video_eligibility: paymenttransaction query failed: %s", e)
        return False


async def has_user_ever_subscribed(session: AsyncSession, user_id: str) -> bool:
    """是否曾有过真实 Stripe 订阅。"""
    try:
        stmt = (
            select(1)
            .select_from(Subscription)
            .where(
                and_(
                    Subscription.user_id == user_id,
                    Subscription.is_deleted == False,
                    Subscription.stripe_subscription_id.isnot(None),
                    Subscription.stripe_subscription_id != "",
                )
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.fetchone() is not None
    except Exception as e:
        logger.warning("video_eligibility: subscriptions query failed: %s", e)
        return False


async def get_has_paid_once(session: AsyncSession, user_id: str) -> bool:
    """是否曾真实付费或订阅（二选一即为 True）。"""
    if await has_user_completed_payment(session, user_id):
        return True
    return await has_user_ever_subscribed(session, user_id)


async def get_video_eligibility_async(user_id: str) -> Tuple[bool, str]:
    """对外便捷方法：所有用户均可使用视频能力。"""
    return True, ""
