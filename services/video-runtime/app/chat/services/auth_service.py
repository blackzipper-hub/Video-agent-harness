"""
认证服务：从 Cookie token 解析 JWT 得到 user_id，供 Chat API 做 user_id 校验。
"""
import logging
import os
from typing import Optional

from fastapi import Security
from fastapi.security import APIKeyCookie
from jose import JWTError, jwt

from app.chat.config import get_settings
from app.chat.exceptions import BusinessException, BusinessExceptionCode

logger = logging.getLogger(__name__)

# auto_error=False：无 token 时不由 FastAPI 直接 401，交给 get_current_user 处理
# （自托管单用户模式下回退到默认 user_id）
security = APIKeyCookie(name="token", auto_error=False)


class AuthService:
    def __init__(self):
        settings = get_settings()
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM

    def decode_token(self, token: str) -> dict:
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except JWTError:
            raise BusinessException(BusinessExceptionCode.INVALID_TOKEN)

    @staticmethod
    def _local_single_user_id() -> Optional[str]:
        """开源/自托管单用户模式：启用时返回回退 user_id，否则 None。
        直接读环境变量（与 app 行为一致，且不依赖 app.chat.config 是否声明该字段）。"""
        if os.getenv("LOCAL_SINGLE_USER_MODE", "false").strip().lower() not in ("1", "true", "yes"):
            return None
        uid = (os.getenv("CUTI_SERVICE_DEFAULT_USER_ID", "") or "").strip()
        return uid or None

    async def get_current_user(self, token: Optional[str] = Security(security)) -> str:
        """从 JWT Cookie 获取当前 user_id；无 token 或无效时抛 BusinessException(INVALID_TOKEN)。
        自托管单用户模式（LOCAL_SINGLE_USER_MODE=true）下，无 token 时回退到默认 user_id。"""
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


auth_service = AuthService()
