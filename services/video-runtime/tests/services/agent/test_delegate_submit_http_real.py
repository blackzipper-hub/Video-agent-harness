"""
真实 HTTP：POST /api/cuti/agent-router/delegate-submit +（可选）DB 回查 conversation_runs。

默认 **跳过**；与 `tests/llm/*_real.py`、`scripts/test_delegate_submit_real.py` 一样需显式打开。

推荐：
  conda activate cuti-video-local3   # 或 cuti-video-local
  # 终端 1: uvicorn 起 API
  RUN_DELEGATE_HTTP_REAL=1 pytest tests/services/agent/test_delegate_submit_http_real.py -v -s -m integration

环境变量：
  VIDEO_AGENT_BASE_URL      默认 http://127.0.0.1:8000
  CUTI_SERVICE_TOKEN        必填（与后端一致）
  DELEGATE_REAL_USER_ID     或依赖 CUTI_SERVICE_DEFAULT_USER_ID
  DATABASE_URL              设置则断言 DB 行存在
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def _require_delegate_real_env():
    if not os.environ.get("RUN_DELEGATE_HTTP_REAL"):
        pytest.skip("真实 HTTP 需 RUN_DELEGATE_HTTP_REAL=1，且 CUTI_SERVICE_TOKEN、已启动 API")


@pytest.mark.asyncio
async def test_delegate_submit_http_returns_run_id_and_db_row(_require_delegate_real_env):
    import httpx

    from app.models.database import init_asyncpg_pool, close_asyncpg_pool
    from app.crud.conversation import async_get_conversation_run_by_run_id

    base = os.environ.get("VIDEO_AGENT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    token = (os.environ.get("CUTI_SERVICE_TOKEN") or "").strip()
    if not token:
        pytest.skip("CUTI_SERVICE_TOKEN 未设置")

    user_id = (
        os.environ.get("DELEGATE_REAL_USER_ID")
        or os.environ.get("CUTI_SERVICE_DEFAULT_USER_ID")
        or ""
    ).strip()
    if not user_id:
        user_id = f"delegate-pytest-{uuid.uuid4().hex[:12]}"

    thread_id = f"th-delegate-pytest-{uuid.uuid4().hex[:16]}"
    url = f"{base}/api/cuti/agent-router/delegate-submit"
    body = {
        "thread_id": thread_id,
        "user_input": os.environ.get("DELEGATE_REAL_USER_INPUT") or "pytest 委托真实测试 story",
        "target_agent": (os.environ.get("DELEGATE_REAL_TARGET_AGENT") or "story").strip().lower(),
        "language": "zh",
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Cuti-Service-User-Id": user_id,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, json=body, headers=headers)

    assert r.status_code == 200, r.text[:2000]
    payload = r.json()
    assert payload.get("code", 0) == 0, payload
    data = payload.get("data") or {}
    assert data.get("run_id"), payload
    run_id = data["run_id"]

    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL 未设置，跳过 DB 断言")

    await init_asyncpg_pool()
    try:
        row = await async_get_conversation_run_by_run_id(run_id)
    finally:
        await close_asyncpg_pool()

    assert row is not None
    assert row.run_id == run_id
    assert row.user_id == user_id
    assert row.agent_type == body["target_agent"]
