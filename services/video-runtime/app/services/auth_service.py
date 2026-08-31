import logging
import secrets
from datetime import datetime, timedelta
from typing import Callable, Optional, Dict, Any
from fastapi import Depends, Request, Security, HTTPException, Cookie, Response, status, Header
from fastapi.security import APIKeyHeader, SecurityScopes, HTTPBearer, APIKeyCookie, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from app.schemas.user import Token
from app.config import settings
import re
from app.models.user import UserStatus, User
from app.exceptions import BusinessException, BusinessExceptionCode
from app.models.database import get_asyncpg_pool
from app.utils.asyncpg_utils import fetch_one
import json
import time

logger = logging.getLogger(__name__)

security = APIKeyCookie(name='token', auto_error=False)
admin_security = APIKeyCookie(name='admin_token', auto_error=False)
service_security = HTTPBearer(auto_error=False)


class AuthService:
    def __init__(self):
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM
        self.access_token_expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        self.admin_token_expire_minutes = 60 * 12  # 12 hours for admin tokens

    def create_access_token(self, user_id: str, is_admin: bool = False) -> str:
        """Create a new JWT access token"""
        expire = datetime.utcnow() + timedelta(minutes=self.access_token_expire_minutes)
        to_encode = {
            "exp": expire,
            "sub": user_id,  # 使用user_id作为JWT的subject
            "is_admin": is_admin  # 添加管理员标志
        }
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

    def create_admin_token(self, user_id: str) -> str:
        """Create a new JWT admin token with shorter expiry"""
        expire = datetime.utcnow() + timedelta(minutes=self.admin_token_expire_minutes)
        to_encode = {
            "exp": expire,
            "sub": user_id,
            "is_admin": True
        }
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

    def decode_token(self, token: str) -> dict:
        """Decode and validate JWT token"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except JWTError:
            raise BusinessException(BusinessExceptionCode.INVALID_TOKEN)

    @staticmethod
    def _local_single_user_id() -> Optional[str]:
        """开源/自托管单用户模式：启用时返回回退 user_id，否则 None。"""
        if not getattr(settings, "LOCAL_SINGLE_USER_MODE", False):
            return None
        uid = (settings.CUTI_SERVICE_DEFAULT_USER_ID or "").strip()
        return uid or None

    async def get_current_user(self, token: Optional[str] = Security(security)) -> str:
        """Get current user from JWT token"""
        if not token:
            local_uid = self._local_single_user_id()
            if local_uid:
                return local_uid
            raise BusinessException(BusinessExceptionCode.INVALID_TOKEN)
        try:
            payload = self.decode_token(token)
        except BusinessException:
            local_uid = self._local_single_user_id()
            if local_uid:
                logger.info("Ignoring invalid JWT cookie in local single-user mode")
                return local_uid
            raise
        user_id = payload.get("sub")
        if user_id is None:
            raise BusinessException(BusinessExceptionCode.INVALID_TOKEN)
        return user_id

    def _validate_service_credentials(
        self,
        credentials: Optional[HTTPAuthorizationCredentials],
    ) -> None:
        """Validate service-to-service bearer credentials."""
        if not settings.CUTI_SERVICE_ENABLED:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "Service access is disabled"
            )
        if not settings.CUTI_SERVICE_TOKEN:
            raise BusinessException(
                BusinessExceptionCode.CONFIGURATION_ERROR,
                "Service token is not configured"
            )
        if not credentials or credentials.scheme.lower() != "bearer":
            raise BusinessException(
                BusinessExceptionCode.UNAUTHORIZED,
                "Missing bearer service token"
            )
        if not secrets.compare_digest(credentials.credentials, settings.CUTI_SERVICE_TOKEN):
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "Invalid service token"
            )

    async def get_current_user_or_service_user(
        self,
        token: Optional[str] = Security(security),
        credentials: Optional[HTTPAuthorizationCredentials] = Security(service_security),
        service_user_id: Optional[str] = Header(None, alias="X-Cuti-Service-User-Id"),
    ) -> str:
        """Resolve the effective user_id from either user cookie auth or service bearer auth."""
        if token:
            try:
                payload = self.decode_token(token)
            except BusinessException:
                local_uid = self._local_single_user_id()
                if local_uid:
                    logger.info("Ignoring invalid JWT cookie in local single-user mode")
                    return (service_user_id or local_uid).strip()
                raise
            user_id = payload.get("sub")
            if user_id is None:
                raise BusinessException(BusinessExceptionCode.INVALID_TOKEN)
            return user_id

        # 开源/自托管单用户模式：无 cookie 时回退默认用户（可被显式 service_user_id 覆盖）
        local_uid = self._local_single_user_id()
        if local_uid:
            return (service_user_id or local_uid).strip()

        self._validate_service_credentials(credentials)

        resolved_user_id = (service_user_id or settings.CUTI_SERVICE_DEFAULT_USER_ID or "").strip()
        if not resolved_user_id:
            raise BusinessException(
                BusinessExceptionCode.INVALID_REQUEST_PARAMETERS,
                "Service requests must provide X-Cuti-Service-User-Id or configure CUTI_SERVICE_DEFAULT_USER_ID"
            )
        return resolved_user_id

    async def get_current_admin(self, token: Optional[str] = Security(admin_security)) -> str:
        """Get current admin user from JWT token, verifying admin status"""
        if not token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="您没有管理员权限",
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload = self.decode_token(token)
        user_id = payload.get("sub")
        is_admin = payload.get("is_admin", False)
        
        if not user_id or not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="您没有管理员权限",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        # 从数据库验证用户是否确实是管理员 - 使用asyncpg
        try:
            pool = get_asyncpg_pool()
            async with pool.acquire() as conn:
                user = await fetch_one(conn, "SELECT * FROM users WHERE user_id = $1", user_id)
                if not user or not user.get('is_admin'):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="您没有管理员权限",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"验证管理员权限时出错: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="验证管理员权限时出错",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return user_id

    async def admin_login(self, username: str, password: str, response: Response) -> dict:
        """Admin login with username and password"""
        # 验证管理员凭据 - 使用asyncpg
        try:
            pool = get_asyncpg_pool()
            async with pool.acquire() as conn:
                user = await fetch_one(conn, "SELECT * FROM users WHERE username = $1", username)
            
            if not user or not user.get('is_admin'):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="用户名或密码错误",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            # 验证密码 (使用已有的密码哈希验证)
            from app.services.user_service import pwd_context
            if not user.get('password_hash') or not pwd_context.verify(password, user['password_hash']):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="用户名或密码错误",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            # 创建普通用户访问令牌（包含is_admin信息）
            access_token = self.create_access_token(user['user_id'], user.get('is_admin', False))
            
            # 设置普通用户Cookie
            response.set_cookie(
                key="token",
                value=access_token,
                httponly=True,
                secure=True,  # HTTPS 网站必须设置为 True
                samesite="lax",
                max_age=60 * 60 * 24 * 7  # 7天
            )
            
            # 设置logged_in标志cookie用于前端检测
            response.set_cookie(
                key="logged_in",
                value="true",
                secure=True,  # HTTPS 网站必须设置为 True
                samesite="lax",
                max_age=60 * 60 * 24 * 7  # 7天
            )
            
            # 创建管理员令牌
            admin_token = self.create_admin_token(user.user_id)
            
            # 设置管理员Cookie
            response.set_cookie(
                key="admin_token",
                value=admin_token,
                httponly=True,
                secure=True,  # HTTPS 网站必须设置为 True
                samesite="lax",
                max_age=60 * 60 * 12  # 12小时
            )
            
            # 设置管理员登录状态Cookie
            response.set_cookie(
                key="admin_logged_in",
                value="true",
                secure=True,  # HTTPS 网站必须设置为 True
                samesite="lax",
                max_age=60 * 60 * 12  # 12小时
            )
            
            return {
                "username": user.username,
                "user_id": user.user_id,
                "is_admin": user.is_admin
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"管理员登录时出错: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="登录时出错，请稍后再试",
            )

    async def admin_logout(self, response: Response) -> dict:
        """Admin logout"""
        # 清除普通用户Cookie
        response.delete_cookie(key="token")
        response.delete_cookie(key="logged_in")
        
        # 清除管理员Cookie
        response.delete_cookie(key="admin_token")
        response.delete_cookie(key="admin_logged_in")
        
        return {"message": "管理员已成功登出"}



auth_service = AuthService() 
