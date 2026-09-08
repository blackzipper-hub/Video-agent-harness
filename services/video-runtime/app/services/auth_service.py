import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Header, Security
from fastapi.security import HTTPBearer, APIKeyCookie, HTTPAuthorizationCredentials
from jose import JWTError, jwt

from app.config import settings
from app.exceptions import BusinessException, BusinessExceptionCode

logger = logging.getLogger(__name__)

security = APIKeyCookie(name="token", auto_error=False)
service_security = HTTPBearer(auto_error=False)


class AuthService:
    def __init__(self):
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM
        self.access_token_expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES

    def create_access_token(self, user_id: str, is_admin: bool = False) -> str:
        expire = datetime.utcnow() + timedelta(minutes=self.access_token_expire_minutes)
        to_encode = {
            "exp": expire,
            "sub": user_id,
            "is_admin": is_admin,
        }
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

    def decode_token(self, token: str) -> dict:
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
        if not settings.CUTI_SERVICE_ENABLED:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "Service access is disabled",
            )
        if not settings.CUTI_SERVICE_TOKEN:
            raise BusinessException(
                BusinessExceptionCode.CONFIGURATION_ERROR,
                "Service token is not configured",
            )
        if not credentials or credentials.scheme.lower() != "bearer":
            raise BusinessException(
                BusinessExceptionCode.UNAUTHORIZED,
                "Missing bearer service token",
            )
        if not secrets.compare_digest(credentials.credentials, settings.CUTI_SERVICE_TOKEN):
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "Invalid service token",
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

        local_uid = self._local_single_user_id()
        if local_uid:
            return (service_user_id or local_uid).strip()

        self._validate_service_credentials(credentials)

        resolved_user_id = (service_user_id or settings.CUTI_SERVICE_DEFAULT_USER_ID or "").strip()
        if not resolved_user_id:
            raise BusinessException(
                BusinessExceptionCode.INVALID_REQUEST_PARAMETERS,
                "Service requests must provide X-Cuti-Service-User-Id or configure CUTI_SERVICE_DEFAULT_USER_ID",
            )
        return resolved_user_id


auth_service = AuthService()
