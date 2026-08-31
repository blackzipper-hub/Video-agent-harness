"""
支付流水模型（与 Cuti-backend-go 共用表 paymenttransaction，以 Go 为准）
仅用于视频资格查询：是否曾有一笔成功支付。不用于创建/更新。
Go 写入：succeeded（Stripe webhook）/ completed（packages 回调）。
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class PaymentStatus(str, Enum):
    """以 Go 为准：Stripe 用 succeeded，packages 回调用 completed"""
    PENDING = "pending"
    COMPLETED = "completed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    REFUNDED = "refunded"
    EXPIRED = "expired"


class PaymentTransaction(SQLModel, table=True):
    """仅读：查询用户是否曾成功支付。表名与 Go 一致。"""
    __tablename__ = "paymenttransaction"

    id: Optional[int] = Field(default=None, primary_key=True)
    transaction_id: str = Field(index=True)
    user_id: str = Field(index=True)
    package_id: Optional[int] = Field(default=None, index=True)
    amount: int = 0
    amount_usd: int = 0
    currency: str = ""
    credits: int = 0
    status: str = Field(default=PaymentStatus.PENDING.value, max_length=50)
    payment_method: Optional[str] = Field(default=None, max_length=50)
    stripe_payment_intent_id: Optional[str] = Field(default=None, index=True)
    stripe_checkout_session_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    is_deleted: bool = Field(default=False, index=True)
