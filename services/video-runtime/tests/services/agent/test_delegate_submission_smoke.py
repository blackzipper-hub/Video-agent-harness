"""
委托入队 / 图入口冒烟：验证 START → delegated_merge_input → route_to_*（不经过 process_user_request），
以及 async_create_conversation_run 与 smarttest/enqueue 一致的 DB 行。

**直接跑方法（无 HTTP、无 mock，真实 DB+SQS+Redis）**：
  scripts/run_delegate_submit_once.py（设 DELEGATE_DIRECT_USER_ID）

**真实 HTTP + 同库 DB 回查**（需起 API、CUTI_SERVICE_TOKEN）见：
  scripts/test_delegate_submit_real.py
  tests/services/agent/test_delegate_submit_http_real.py（RUN_DELEGATE_HTTP_REAL=1）

推荐 conda：`cuti-video-local3` 或 `cuti-video-local`。

运行示例：
  # 快速（默认 ~0.1s 模拟 story 节点）
  pytest tests/services/agent/test_delegate_submission_smoke.py -v -s

  # 手动观察 ~5s（模拟下游耗时）
  DELEGATE_SMOKE_SLEEP_SEC=5 pytest tests/services/agent/test_delegate_submission_smoke.py::test_delegate_graph_skips_process_user_request -v -s

  # 需要 .env.development 里 DATABASE_URL 可用
  pytest tests/services/agent/test_delegate_submission_smoke.py::test_async_create_conversation_run_delegate_like_enqueue -v -s
"""

from __future__ import annotations

import asyncio
import os
import types
import uuid
import logging

import pytest

logger = logging.getLogger(__name__)


def _sleep_sec() -> float:
    return float(os.environ.get("DELEGATE_SMOKE_SLEEP_SEC", "0.1"))


@pytest.mark.asyncio
async def test_delegate_graph_skips_process_user_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """委托态：不进入 process_user_request；进入 delegated_merge_input 后走 story。"""
    import uuid as uuid_lib

    from langchain_core.runnables import RunnableConfig

    from app.services.agent import agent_router_service as ars
    from app.services.agent.agent_router_service import (
        AgentRouterService,
        AgentRouterRequest,
        AgentType,
    )
    from app.models.video_state import UserInput
    from app.models.user_options import UserOption

    async def fast_merge_user_option(user_input: str, default_user_option: UserOption):
        return default_user_option

    monkeypatch.setattr(ars, "merge_user_option_with_input", fast_merge_user_option)

    svc = AgentRouterService()

    async def must_not_process_user_request(self, state, runtime):
        raise AssertionError("process_user_request 不应在委托路径被调用")

    async def fake_route_to_story(self, state, runtime):
        await asyncio.sleep(_sleep_sec())
        return {"messages": []}

    svc.process_user_request = types.MethodType(must_not_process_user_request, svc)
    svc._route_to_story = types.MethodType(fake_route_to_story, svc)

    graph = svc.build_graph_for_langsmith()

    thread_id = f"test-delegate-thread-{uuid_lib.uuid4().hex[:12]}"
    run_id = str(uuid_lib.uuid4())
    user_id = "test-delegate-user"
    conversation_id = 999999001
    conversation_uuid = str(uuid_lib.uuid4())

    user_input_data = UserInput(
        user_input="委托冒烟：仅验证图分支",
        user_option=UserOption.default(),
        images=[],
        audio_files=[],
        video_files=[],
        agent_type=None,
    )

    initial_state = {
        "request": AgentRouterRequest(
            user_id=user_id,
            user_input_data=user_input_data,
            conversation_id=conversation_id,
            thread_id=thread_id,
        ),
        "run_id": run_id,
        "full_auto": False,
        "language": "zh",
        "skip_router_analysis": True,
        "selected_agent": AgentType.STORY,
        "detected_language": "zh",
        "user_id": user_id,
        "conversation_id": conversation_id,
        "thread_id": thread_id,
        "conversation_uuid": conversation_uuid,
    }

    config = RunnableConfig(
        configurable={"thread_id": thread_id, "run_id": run_id},
        recursion_limit=50,
    )

    result = await graph.ainvoke(initial_state, config=config)
    assert result is not None
    msgs = result.get("messages") or []
    assert isinstance(msgs, list)
    logger.info("delegate graph smoke OK: messages count=%s", len(msgs))


@pytest.fixture
async def init_asyncpg_for_delegate():
    from app.models.database import init_asyncpg_pool, close_asyncpg_pool

    await init_asyncpg_pool()
    try:
        yield
    finally:
        await close_asyncpg_pool()


@pytest.mark.asyncio
async def test_async_create_conversation_run_delegate_like_enqueue(init_asyncpg_for_delegate) -> None:
    """
    与 enqueue_video_task(delegate) 一致：先 conversation，再 conversation_runs 一行；
    用于你对照 DB 里 agent_type / user_input / status。
    """
    from app.crud.conversation import (
        async_create_conversation,
        async_create_conversation_run,
        async_get_conversation_run_by_run_id,
    )
    from app.models.task_status import RunType

    user_id = f"test-delegate-db-{uuid.uuid4().hex[:10]}"
    thread_id = f"th-delegate-{uuid.uuid4().hex[:12]}"
    run_id = str(uuid.uuid4())
    user_input = "DB 冒烟：委托 story"

    conv = await async_create_conversation(
        user_id=user_id,
        thread_id=thread_id,
        title=user_input[:50],
    )
    conversation_id = conv.id
    conversation_uuid = conv.uuid

    await async_create_conversation_run(
        conversation_id=conversation_id,
        thread_id=thread_id,
        run_id=run_id,
        user_id=user_id,
        agent_type="story",
        run_type=RunType.MAIN.value,
        user_option={"full_auto": False},
        user_input=user_input,
        user_input_files=None,
        status="queued",
        conversation_uuid=conversation_uuid,
        billing_status=None,
        additional_data=None,
    )

    row = await async_get_conversation_run_by_run_id(run_id)
    assert row is not None
    assert row.run_id == run_id
    assert row.user_id == user_id
    assert row.agent_type == "story"
    assert row.user_input == user_input
    assert str(row.thread_id) == thread_id
    logger.info(
        "conversation_run OK: run_id=%s conversation_id=%s agent_type=%s status=%s",
        run_id,
        conversation_id,
        row.agent_type,
        getattr(row, "status", None),
    )
