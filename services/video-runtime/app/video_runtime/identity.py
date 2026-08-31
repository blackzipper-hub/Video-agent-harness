from __future__ import annotations

import hmac
import os
from typing import Protocol

from fastapi import HTTPException, Request


class IdentityResolver(Protocol):
    async def resolve(self, request: Request) -> tuple[str, str | None]: ...


class ServiceOrLocalIdentityResolver:
    """Fail-closed service identity in production, convenient single-user identity locally."""

    async def resolve(self, request: Request) -> tuple[str, str | None]:
        expected = os.getenv("VIDEO_RUNTIME_SERVICE_TOKEN", "").strip()
        environment = os.getenv("VIDEO_RUNTIME_ENVIRONMENT", "development").lower()
        authorization = request.headers.get("Authorization", "")
        user_id = request.headers.get("X-Video-User-Id", "").strip()
        session_id = request.headers.get("X-Video-Session-Id") or None
        if expected and authorization:
            supplied = authorization.removeprefix("Bearer ")
            if not hmac.compare_digest(supplied, expected):
                raise HTTPException(status_code=401, detail="invalid video runtime service token")
            if not user_id:
                raise HTTPException(status_code=400, detail="X-Video-User-Id is required")
            return user_id, session_id
        if expected and environment == "production":
            raise HTTPException(status_code=401, detail="video runtime service token is required")
        if environment == "production":
            raise HTTPException(
                status_code=503,
                detail="VIDEO_RUNTIME_SERVICE_TOKEN or a production identity adapter is required",
            )
        return user_id or os.getenv("VIDEO_RUNTIME_LOCAL_USER_ID", "local-user"), session_id


class CutiIdentityResolver:
    """Adapter for the imported Cuti JWT cookie and service bearer identities."""

    async def resolve(self, request: Request) -> tuple[str, str | None]:
        from app.config import settings
        from app.services.auth_service import auth_service

        requested_user = (
            request.headers.get("X-Video-User-Id")
            or request.headers.get("X-Cuti-Service-User-Id")
            or ""
        ).strip()
        session_id = request.headers.get("X-Video-Session-Id") or None
        token = request.cookies.get("token")
        if token:
            payload = auth_service.decode_token(token)
            user_id = str(payload.get("sub") or "").strip()
            if not user_id:
                raise HTTPException(status_code=401, detail="JWT has no user identity")
            if requested_user and requested_user != user_id:
                raise HTTPException(status_code=403, detail="requested user does not match JWT")
            return user_id, session_id

        local_user = auth_service._local_single_user_id()
        authorization = request.headers.get("Authorization", "")
        service_token = str(getattr(settings, "CUTI_SERVICE_TOKEN", "") or "").strip()
        if service_token:
            supplied = authorization.removeprefix("Bearer ")
            if not hmac.compare_digest(supplied, service_token):
                if local_user:
                    return requested_user or local_user, session_id
                raise HTTPException(status_code=401, detail="invalid Cuti service token")
            user_id = requested_user or str(
                getattr(settings, "CUTI_SERVICE_DEFAULT_USER_ID", "") or ""
            ).strip()
            if not user_id:
                raise HTTPException(status_code=400, detail="service user identity is required")
            return user_id, session_id
        if local_user:
            return requested_user or local_user, session_id
        raise HTTPException(status_code=401, detail="Cuti authentication is required")
