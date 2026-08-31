from pydantic import BaseModel, EmailStr, validator, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum

# 认证类型枚举
class AuthType(str, Enum):
    INVITE_CODE = "invite_code"
    EMAIL = "email"
    PHONE = "phone"

# 用户状态枚举
class UserStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    BANNED = "banned"

# 积分操作类型枚举
class CreditOperationType(str, Enum):
    INIT = "init"
    ADD = "add"
    USE = "use"
    EXPIRE = "expire"
    INVITE = "invite"
    SYSTEM = "system"
    # Agent 主流程（计费 worker 按 run.agent_type 区分）
    VIDEO_GENERATION = "video_generation"  # 视频 agent 主流程
    STORY_GENERATION = "story_generation"  # 故事 agent 主流程
    MUSIC_GENERATION = "music_generation"  # 音乐 agent 主流程
    IMAGE_GENERATION = "image_generation"  # 图像 agent 主流程
    # 视频编辑内 regenerate（单独 API，非 conversation_run 计费）
    REGENERATE_KEYFRAMES = "regenerate_keyframes"  # 重新生成关键帧
    REGENERATE_VIDEOS = "regenerate_videos"  # 重新生成视频
    REGENERATE_CHARACTERS = "regenerate_characters"  # 重新生成角色

# 用户基础模型
class UserBase(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None

# 用户创建请求
class UserCreate(BaseModel):
    """用户创建模型"""
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    email: Optional[EmailStr] = Field(None)
    phone: Optional[str] = Field(None, min_length=6, max_length=20)
    password: Optional[str] = Field(None, min_length=6, max_length=100)
    auth_type: str = Field(AuthType.INVITE_CODE)
    invite_code: Optional[str] = Field(None)
    
    @validator('password')
    def password_required_for_email_and_phone(cls, v, values):
        auth_type = values.get('auth_type')
        if auth_type in [AuthType.EMAIL, AuthType.PHONE] and not v:
            raise ValueError('密码对于邮箱和手机登录是必需的')
        return v
    
    @validator('invite_code')
    def invite_code_required_for_invite_auth(cls, v, values):
        auth_type = values.get('auth_type')
        if auth_type == AuthType.INVITE_CODE and not v:
            raise ValueError('邀请码对于邀请码登录是必需的')
        return v
    
    @validator('email')
    def email_required_for_email_auth(cls, v, values):
        auth_type = values.get('auth_type')
        if auth_type == AuthType.EMAIL and not v:
            raise ValueError('邮箱对于邮箱登录是必需的')
        return v
    
    @validator('phone')
    def phone_required_for_phone_auth(cls, v, values):
        auth_type = values.get('auth_type')
        if auth_type == AuthType.PHONE and not v:
            raise ValueError('手机号对于手机登录是必需的')
        return v

# 用户登录请求
class UserLogin(BaseModel):
    """用户登录模型"""
    auth_type: str = Field(AuthType.INVITE_CODE)
    invite_code: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    
    @validator('invite_code')
    def invite_code_required_for_invite_auth(cls, v, values):
        auth_type = values.get('auth_type')
        if auth_type == AuthType.INVITE_CODE and not v:
            raise ValueError('邀请码对于邀请码登录是必需的')
        return v
    
    @validator('email')
    def email_required_for_email_auth(cls, v, values):
        auth_type = values.get('auth_type')
        if auth_type == AuthType.EMAIL and not v:
            raise ValueError('邮箱对于邮箱登录是必需的')
        return v
    
    @validator('phone')
    def phone_required_for_phone_auth(cls, v, values):
        auth_type = values.get('auth_type')
        if auth_type == AuthType.PHONE and not v:
            raise ValueError('手机号对于手机登录是必需的')
        return v
    
    @validator('username')
    def username_required_for_username_auth(cls, v, values):
        auth_type = values.get('auth_type')
        if auth_type == "username" and not v:
            raise ValueError('用户名对于用户名登录是必需的')
        return v
    
    @validator('password')
    def password_required_for_email_and_phone(cls, v, values):
        auth_type = values.get('auth_type')
        if auth_type in [AuthType.EMAIL, AuthType.PHONE, "username"] and not v:
            raise ValueError('密码对于邮箱、手机和用户名登录是必需的')
        return v

# 用户响应
class UserResponse(BaseModel):
    """用户响应模型"""
    id: int
    user_id: str
    username: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    auth_type: str
    status: str
    is_admin: bool = False
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# 用户积分
class UserCredit(BaseModel):
    """用户积分模型"""
    id: int
    user_id: str
    balance: int
    subscription_balance: int
    purchased_balance: int
    total_earned: int
    total_used: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# 积分历史
class CreditHistory(BaseModel):
    """积分历史模型"""
    id: int
    user_id: str
    amount: int
    balance: int
    operation_type: str
    description: str
    credit_source: Optional[str] = None
    allocation_id: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# 创建邀请码请求
class InviteCodeCreate(BaseModel):
    """邀请码创建模型"""
    expires_at: Optional[datetime] = None

# 邀请码响应
class InviteCodeResponse(BaseModel):
    """邀请码响应模型"""
    id: int
    code: str
    creator_id: str
    is_used: bool
    used_by_id: Optional[str] = None
    used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    
    class Config:
        from_attributes = True

# 令牌响应
class Token(BaseModel):
    """令牌模型"""
    access_token: str
    token_type: str
    user: UserResponse

class AdminLogin(BaseModel):
    """管理员登录模型"""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)

class AdminResponse(BaseModel):
    """管理员响应模型"""
    username: str
    user_id: str
    is_admin: bool = True 