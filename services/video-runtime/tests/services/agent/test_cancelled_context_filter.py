"""取消上下文过滤回归测试：

- add_messages_with_run_id：新消息按当前 run_id 打标签
- _filter_cancelled_history：只把「非取消 run」的消息带进 LLM context
"""
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage, AIMessage

from app.services.agent.agent_router_service import (
    AgentRouterService,
    add_messages_with_run_id,
    _current_run_id_cv,
)


def test_reducer_stamps_run_id():
    _current_run_id_cv.set("run1")
    msgs = add_messages_with_run_id([], [HumanMessage(content="a"), AIMessage(content="b")])
    _current_run_id_cv.set("run2")
    msgs = add_messages_with_run_id(msgs, [HumanMessage(content="c")])
    _current_run_id_cv.set(None)

    stamps = [(m.content, m.additional_kwargs.get("run_id")) for m in msgs]
    assert stamps == [("a", "run1"), ("b", "run1"), ("c", "run2")]


def test_reducer_no_run_id_leaves_unstamped():
    _current_run_id_cv.set(None)
    msgs = add_messages_with_run_id([], [HumanMessage(content="x")])
    assert msgs[0].additional_kwargs.get("run_id") is None


@pytest.mark.asyncio
async def test_filter_drops_cancelled_run_messages():
    svc = AgentRouterService.__new__(AgentRouterService)
    history = [
        HumanMessage(content="dog", additional_kwargs={"run_id": "run1"}),
        AIMessage(content="routing-dog", additional_kwargs={"run_id": "run1"}),
        HumanMessage(content="cat", additional_kwargs={"run_id": "run2"}),
    ]
    with patch(
        "app.services.agent.agent_router_service.async_get_runs_by_thread_id_and_status",
        new_callable=AsyncMock,
        return_value=[{"run_id": "run1", "status": "cancelled"}],
    ):
        kept = await svc._filter_cancelled_history(history, "thread-1")
    assert [m.content for m in kept] == ["cat"]


@pytest.mark.asyncio
async def test_filter_keeps_all_when_no_cancelled():
    svc = AgentRouterService.__new__(AgentRouterService)
    history = [
        HumanMessage(content="a", additional_kwargs={"run_id": "run1"}),
        AIMessage(content="b", additional_kwargs={"run_id": "run1"}),
    ]
    with patch(
        "app.services.agent.agent_router_service.async_get_runs_by_thread_id_and_status",
        new_callable=AsyncMock,
        return_value=[],
    ):
        kept = await svc._filter_cancelled_history(history, "thread-1")
    assert len(kept) == 2


@pytest.mark.asyncio
async def test_filter_keeps_untagged_messages():
    svc = AgentRouterService.__new__(AgentRouterService)
    history = [
        HumanMessage(content="legacy"),  # 老 checkpoint，无 run_id
        HumanMessage(content="cancelled", additional_kwargs={"run_id": "run1"}),
    ]
    with patch(
        "app.services.agent.agent_router_service.async_get_runs_by_thread_id_and_status",
        new_callable=AsyncMock,
        return_value=[{"run_id": "run1"}],
    ):
        kept = await svc._filter_cancelled_history(history, "thread-1")
    assert [m.content for m in kept] == ["legacy"]


@pytest.mark.asyncio
async def test_filter_no_thread_id_is_noop():
    svc = AgentRouterService.__new__(AgentRouterService)
    history = [HumanMessage(content="a", additional_kwargs={"run_id": "run1"})]
    kept = await svc._filter_cancelled_history(history, None)
    assert kept == history


@pytest.mark.asyncio
async def test_filter_db_error_falls_back_to_unfiltered():
    svc = AgentRouterService.__new__(AgentRouterService)
    history = [HumanMessage(content="a", additional_kwargs={"run_id": "run1"})]
    with patch(
        "app.services.agent.agent_router_service.async_get_runs_by_thread_id_and_status",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db down"),
    ):
        kept = await svc._filter_cancelled_history(history, "thread-1")
    assert kept == history
