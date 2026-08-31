from sqlmodel import SQLModel, Field, Column, String
from datetime import datetime
from typing import Optional

# 用户状态
class UserStatus:
    ACTIVE = "active"      # 激活状态
    INACTIVE = "inactive"  # 未激活状态
    LOCKED = "locked"      # 锁定状态
    DELETED = "deleted"    # 删除状态

# 认证类型
class AuthType:
    INVITE_CODE = "invite_code"  # 邀请码注册
    VISITOR = "visitor"          # 游客账号
    EMAIL = "email"              # 邮箱注册
    PHONE = "phone"              # 手机号注册

# 积分操作类型
class CreditOperationType:
    # 基础操作类型
    INIT = "init"                          # 初始积分
    EARN = "earn"                          # 获得积分（通用获得）
    USE = "use"                            # 使用积分（通用消费）
    ADMIN = "admin"                        # 管理员操作（通用管理员操作）
    
    # 具体操作类型（向后兼容）
    ADMIN_ADD = "admin_add"                # 管理员添加积分
    REGISTER = "register"                  # 注册奖励
    TASK_REWARD = "task_reward"            # 任务奖励
    INVITE_REWARD = "invite_reward"        # 邀请奖励
    USE_SERVICE = "use_service"            # 使用服务
    ADD_STORYBOOK = "add_storybook"        # 创建绘本
    GENERATE_STORY = "generate_story"      # 生成故事
    GENERATE_CHARACTER = "generate_character"  # 生成角色
    DOWNLOAD_STORYBOOK = "download_storybook"  # 下载绘本
    REFUND = "refund"                      # 退款

# 用户表
class User(SQLModel, table=True):
    __tablename__ = "users"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(unique=True, index=True, max_length=50, description="用户唯一ID")
    username: Optional[str] = Field(default=None, index=True, max_length=50)
    email: Optional[str] = Field(default=None, sa_column=Column(String(100), unique=True, index=True))
    phone: Optional[str] = Field(default=None, sa_column=Column(String(20), unique=True, index=True))
    password_hash: Optional[str] = Field(default=None, max_length=255)
    password: Optional[str] = Field(default=None, max_length=255, description="明文密码（仅用于临时兼容）")
    auth_type: str = Field(default=AuthType.INVITE_CODE, max_length=20)
    is_admin: bool = Field(default=False, description="是否为管理员用户")
    status: str = Field(default=UserStatus.ACTIVE, max_length=20)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: bool = Field(default=False, index=True, description="是否已删除(软删除标记)")

# 用户积分表
class UserCredit(SQLModel, table=True):
    __tablename__ = "user_credits"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, max_length=50, description="用户ID")  # 不使用外键，仅使用索引
    balance: int = Field(default=0)  # 当前积分余额
    subscription_balance: int = Field(default=0, description="订阅积分余额")  # 订阅积分余额
    purchased_balance: int = Field(default=0, description="购买积分余额")  # 购买积分余额
    total_earned: int = Field(default=0)  # 总共获得的积分
    total_used: int = Field(default=0)  # 总共使用的积分
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: bool = Field(default=False, index=True, description="是否已删除(软删除标记)")

# 积分历史表
class CreditHistory(SQLModel, table=True):
    __tablename__ = "credit_history"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, max_length=50, description="用户ID")  # 不使用外键，仅使用索引
    amount: int = Field()  # 积分变动量，正数为增加，负数为减少
    balance: int = Field()  # 变动后的余额
    operation_type: str = Field(max_length=50)  # 操作类型
    description: str = Field(default="", max_length=255)  # 描述（用户可见，不暴露 run_id）
    reference_id: Optional[str] = Field(default=None, index=True, max_length=64, description="关联ID，如计费 run_id，仅内部幂等用不展示")
    credit_source: Optional[str] = Field(default=None, description="积分来源")  # 积分来源
    allocation_id: Optional[str] = Field(default=None, description="分配ID")  # 分配ID
    expires_at: Optional[datetime] = Field(default=None, description="过期时间")  # 过期时间
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: bool = Field(default=False, index=True, description="是否已删除(软删除标记)")

# 邀请码表
class InviteCode(SQLModel, table=True):
    __tablename__ = "invite_codes"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(unique=True, index=True, max_length=50)
    creator_id: str = Field(index=True, max_length=50, description="创建者用户ID")  # 不使用外键，仅使用索引
    used_by_id: Optional[str] = Field(default=None, index=True, max_length=50, description="使用者用户ID")  # 不使用外键，仅使用索引
    used_at: Optional[datetime] = Field(default=None)
    expires_at: Optional[datetime] = Field(default=None)
    is_used: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: bool = Field(default=False, index=True, description="是否已删除(软删除标记)") 