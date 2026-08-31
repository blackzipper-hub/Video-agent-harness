"""LOCAL_SINGLE_USER_MODE：启用时无 token 请求回退默认用户；默认 False 时鉴权行为不变。"""
import pytest

from app.services.auth_service import AuthService
from app.chat.services.auth_service import AuthService as ChatAuthService
from app.config import settings
from app.exceptions import BusinessException

DEFAULT_UID = "fc3b2037-f9d4-4efd-8b5d-0e1221865b2c"


@pytest.fixture
def auth():
    return AuthService()


@pytest.mark.asyncio
async def test_no_token_raises_when_local_mode_off(auth, monkeypatch):
    """默认（LOCAL_SINGLE_USER_MODE=False）：无 token 仍抛 INVALID_TOKEN。"""
    monkeypatch.setattr(settings, "LOCAL_SINGLE_USER_MODE", False, raising=False)
    with pytest.raises(BusinessException):
        await auth.get_current_user(token=None)


@pytest.mark.asyncio
async def test_no_token_returns_default_user_in_local_mode(auth, monkeypatch):
    """LOCAL_SINGLE_USER_MODE=True：无 token 回退 CUTI_SERVICE_DEFAULT_USER_ID。"""
    monkeypatch.setattr(settings, "LOCAL_SINGLE_USER_MODE", True, raising=False)
    monkeypatch.setattr(settings, "CUTI_SERVICE_DEFAULT_USER_ID", DEFAULT_UID, raising=False)
    uid = await auth.get_current_user(token=None)
    assert uid == DEFAULT_UID


@pytest.mark.asyncio
async def test_invalid_token_returns_default_user_in_local_mode(auth, monkeypatch):
    """本地模式忽略其他环境签发的无效 Cookie，避免阻断免登录访问。"""
    monkeypatch.setattr(settings, "LOCAL_SINGLE_USER_MODE", True, raising=False)
    monkeypatch.setattr(settings, "CUTI_SERVICE_DEFAULT_USER_ID", DEFAULT_UID, raising=False)
    uid = await auth.get_current_user(token="invalid-remote-token")
    assert uid == DEFAULT_UID


@pytest.mark.asyncio
async def test_invalid_token_still_raises_when_local_mode_off(auth, monkeypatch):
    """非本地单用户模式仍严格校验 JWT。"""
    monkeypatch.setattr(settings, "LOCAL_SINGLE_USER_MODE", False, raising=False)
    with pytest.raises(BusinessException):
        await auth.get_current_user(token="invalid-remote-token")


@pytest.mark.asyncio
async def test_chat_invalid_token_returns_default_user_in_local_mode(monkeypatch):
    """进程内 Chat 路由也应忽略本地模式下的无效 Cookie。"""
    monkeypatch.setenv("LOCAL_SINGLE_USER_MODE", "true")
    monkeypatch.setenv("CUTI_SERVICE_DEFAULT_USER_ID", DEFAULT_UID)
    uid = await ChatAuthService().get_current_user(token="invalid-remote-token")
    assert uid == DEFAULT_UID


@pytest.mark.asyncio
async def test_service_resolver_returns_default_in_local_mode(auth, monkeypatch):
    """LOCAL_SINGLE_USER_MODE=True：service 解析器无 token/无凭证也回退默认用户。"""
    monkeypatch.setattr(settings, "LOCAL_SINGLE_USER_MODE", True, raising=False)
    monkeypatch.setattr(settings, "CUTI_SERVICE_DEFAULT_USER_ID", DEFAULT_UID, raising=False)
    uid = await auth.get_current_user_or_service_user(
        token=None, credentials=None, service_user_id=None
    )
    assert uid == DEFAULT_UID


@pytest.mark.asyncio
async def test_service_resolver_honors_explicit_service_user(auth, monkeypatch):
    """本地模式下显式 X-Cuti-Service-User-Id 优先。"""
    monkeypatch.setattr(settings, "LOCAL_SINGLE_USER_MODE", True, raising=False)
    monkeypatch.setattr(settings, "CUTI_SERVICE_DEFAULT_USER_ID", DEFAULT_UID, raising=False)
    uid = await auth.get_current_user_or_service_user(
        token=None, credentials=None, service_user_id="explicit-user"
    )
    assert uid == "explicit-user"
