from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel, Relationship
from enum import Enum


class SubscriptionStatus(str, Enum):
    """Subscription status enum matching Stripe subscription statuses."""
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    TRIALING = "trialing"
    UNPAID = "unpaid"


class SubscriptionPlan(SQLModel, table=True):
    """
    Subscription plan definitions.
    Each plan represents a monthly recurring subscription tier.
    """
    __tablename__ = "subscription_plans"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)  # e.g., "2000 Credits/Month"
    description: Optional[str] = None
    monthly_credits: int = Field(index=True)  # Credits allocated each month
    price_usd_cents: int  # Price in USD cents (e.g., 2000 = $20.00)
    stripe_price_id: str = Field(unique=True, index=True)  # Stripe Price ID for recurring billing
    stripe_product_id: Optional[str] = None  # Stripe Product ID
    is_active: bool = Field(default=True, index=True)
    sort_order: int = Field(default=0)  # For display ordering

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    subscriptions: list["Subscription"] = Relationship(back_populates="plan")


class Subscription(SQLModel, table=True):
    """
    User subscription tracking.
    One active subscription per user.
    """
    __tablename__ = "subscriptions"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, max_length=50, description="用户ID")  # 不使用外键，仅使用索引
    plan_id: int = Field(foreign_key="subscription_plans.id", index=True)

    # Stripe identifiers
    stripe_subscription_id: str = Field(unique=True, index=True)
    stripe_customer_id: str = Field(index=True)

    # Subscription lifecycle
    status: SubscriptionStatus = Field(default=SubscriptionStatus.ACTIVE, index=True)
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool = Field(default=False)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    canceled_at: Optional[datetime] = None

    # Soft delete
    is_deleted: bool = Field(default=False, index=True)

    # Relationships
    plan: SubscriptionPlan = Relationship(back_populates="subscriptions")
    allocations: list["CreditAllocation"] = Relationship(back_populates="subscription")


class CreditAllocation(SQLModel, table=True):
    """
    Tracks monthly credit allocations from subscriptions.
    Each billing period creates a new allocation with expiration date.
    """
    __tablename__ = "credit_allocations"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, max_length=50, description="用户ID") 
    subscription_id: int = Field(foreign_key="subscriptions.id", index=True)

    # Credit tracking
    credits_allocated: int  # Initial credits granted
    remaining_credits: int  # Credits still available

    # Lifecycle
    allocated_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(index=True)  # End of billing period
    is_expired: bool = Field(default=False, index=True)
    expired_at: Optional[datetime] = None

    # Billing reference
    stripe_invoice_id: Optional[str] = Field(index=True)
    billing_period_start: datetime
    billing_period_end: datetime

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    subscription: Subscription = Relationship(back_populates="allocations")
