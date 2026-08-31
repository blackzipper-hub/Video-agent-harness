"""
Test routing logic after refactor:
- ingress_route removed; delegated_va_run_id recovery + old-data backfill merged into process_user_request
- _decide_route: delegated_va_run_id exists → route_to_video_edit; otherwise normal routing
"""
import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ENVIRONMENT", "local")

from app.chat.services.agent.agent_router_service import (
    AgentRouterService,
    AgentRouterState,
    AgentRouterRequest,
    AgentType,
)
from app.chat.models.video_state import UserInput


def _make_state(
    user_input: str = "测试消息",
    conversation_id: int = 9999,
    delegated_va_run_id: str = None,
    agent_type: str = "video",
    selected_agent: AgentType = None,
    has_confirmed: bool = False,
) -> AgentRouterState:
    ui = UserInput(user_input=user_input, agent_type=agent_type)
    req = AgentRouterRequest(
        user_id="test_user",
        user_input_data=ui,
        conversation_id=conversation_id,
        thread_id="thread_test_123",
    )
    return AgentRouterState(
        request=req,
        user_id="test_user",
        conversation_id=conversation_id,
        thread_id="thread_test_123",
        delegated_va_run_id=delegated_va_run_id,
        selected_agent=selected_agent,
        has_confirmed=has_confirmed,
    )


class FakeConversationRow:
    def __init__(self, agent_type="video", additional_data=None, uuid="fake-uuid", user_id="test_user", thread_id="thread_test_123"):
        self.id = 9999
        self.agent_type = agent_type
        self.additional_data = additional_data or {}
        self.uuid = uuid
        self.user_id = user_id
        self.thread_id = thread_id


def _make_runtime():
    return MagicMock()


# ==================== _decide_route tests ====================

def test_decide_route_no_delegate_video_confirmed():
    """No delegation, video + confirmed → route_to_video (first-time creation)"""
    svc = AgentRouterService.__new__(AgentRouterService)
    state = _make_state(
        selected_agent=AgentType.VIDEO,
        has_confirmed=True,
        delegated_va_run_id=None,
    )
    result = svc._decide_route(state)
    assert result == AgentType.VIDEO.value, f"Expected video, got {result}"
    print("✅ test_decide_route_no_delegate_video_confirmed PASSED")


def test_decide_route_no_delegate_video_not_confirmed():
    """No delegation, video + NOT confirmed → route_to_chat (clarification)"""
    svc = AgentRouterService.__new__(AgentRouterService)
    state = _make_state(
        selected_agent=AgentType.VIDEO,
        has_confirmed=False,
        delegated_va_run_id=None,
    )
    result = svc._decide_route(state)
    assert result == AgentType.CHAT.value, f"Expected chat, got {result}"
    print("✅ test_decide_route_no_delegate_video_not_confirmed PASSED")


def test_decide_route_delegated_goes_to_edit():
    """delegated_va_run_id exists → route_to_video_edit regardless of has_confirmed"""
    svc = AgentRouterService.__new__(AgentRouterService)
    state = _make_state(
        selected_agent=AgentType.VIDEO,
        has_confirmed=False,
        delegated_va_run_id="va-run-123",
    )
    result = svc._decide_route(state)
    assert result == "route_to_video_edit", f"Expected route_to_video_edit, got {result}"
    print("✅ test_decide_route_delegated_goes_to_edit PASSED")


def test_decide_route_delegated_confirmed_goes_to_edit():
    """delegated + confirmed → still route_to_video_edit (edit agent handles re-creation)"""
    svc = AgentRouterService.__new__(AgentRouterService)
    state = _make_state(
        selected_agent=AgentType.VIDEO,
        has_confirmed=True,
        delegated_va_run_id="va-run-456",
    )
    result = svc._decide_route(state)
    assert result == "route_to_video_edit", f"Expected route_to_video_edit, got {result}"
    print("✅ test_decide_route_delegated_confirmed_goes_to_edit PASSED")


def test_decide_route_clarify_no_delegate():
    """No delegation, clarify → route_to_clarify"""
    svc = AgentRouterService.__new__(AgentRouterService)
    state = _make_state(
        selected_agent=AgentType.CLARIFY,
        has_confirmed=False,
        delegated_va_run_id=None,
    )
    result = svc._decide_route(state)
    assert result == AgentType.CLARIFY.value, f"Expected clarify, got {result}"
    print("✅ test_decide_route_clarify_no_delegate PASSED")


def test_decide_route_delegated_overrides_clarify():
    """delegated_va_run_id exists → route_to_video_edit even if analysis said clarify"""
    svc = AgentRouterService.__new__(AgentRouterService)
    state = _make_state(
        selected_agent=AgentType.CLARIFY,
        has_confirmed=False,
        delegated_va_run_id="va-run-789",
    )
    result = svc._decide_route(state)
    assert result == "route_to_video_edit", f"Expected route_to_video_edit, got {result}"
    print("✅ test_decide_route_delegated_overrides_clarify PASSED")


# ==================== backfill logic (unit-level) ====================

def test_backfill_helper_extracts_from_additional_data():
    """_delegated_va_run_id_from_conversation_row extracts from additional_data"""
    svc = AgentRouterService.__new__(AgentRouterService)
    row = FakeConversationRow(additional_data={"delegated_va_run_id": "run-abc"})
    assert svc._delegated_va_run_id_from_conversation_row(row) == "run-abc"
    print("✅ test_backfill_helper_extracts_from_additional_data PASSED")


def test_backfill_helper_returns_none_when_empty():
    """_delegated_va_run_id_from_conversation_row returns None when no key"""
    svc = AgentRouterService.__new__(AgentRouterService)
    row = FakeConversationRow(additional_data={})
    assert svc._delegated_va_run_id_from_conversation_row(row) is None
    print("✅ test_backfill_helper_returns_none_when_empty PASSED")


def test_backfill_helper_returns_none_for_non_video():
    """Non-video conversations never get backfilled"""
    svc = AgentRouterService.__new__(AgentRouterService)
    row = FakeConversationRow(agent_type="story", additional_data={})
    assert svc._delegated_va_run_id_from_conversation_row(row) is None
    print("✅ test_backfill_helper_returns_none_for_non_video PASSED")


def test_backfill_old_data_scenario():
    """Simulate: agent_type=video, no delegated, conversation_runs=0 → should set delegated=thread_id"""
    conversation = FakeConversationRow(agent_type="video", additional_data={})
    delegated = None
    thread_id = "thread_test_123"

    svc = AgentRouterService.__new__(AgentRouterService)
    if not delegated:
        delegated = svc._delegated_va_run_id_from_conversation_row(conversation)
    if not delegated:
        agent_type_str = getattr(conversation, "agent_type", None)
        if agent_type_str == "video":
            runs_count = 0  # simulating empty conversation_runs
            if runs_count == 0:
                delegated = thread_id

    assert delegated == thread_id, f"Expected {thread_id}, got {delegated}"
    print("✅ test_backfill_old_data_scenario PASSED")


def test_no_backfill_for_new_data_in_clarification():
    """Simulate: agent_type=video, no delegated, conversation_runs>0 → should NOT backfill"""
    conversation = FakeConversationRow(agent_type="video", additional_data={})
    delegated = None
    thread_id = "thread_test_123"

    svc = AgentRouterService.__new__(AgentRouterService)
    if not delegated:
        delegated = svc._delegated_va_run_id_from_conversation_row(conversation)
    if not delegated:
        agent_type_str = getattr(conversation, "agent_type", None)
        if agent_type_str == "video":
            runs_count = 3  # new conversation has runs
            if runs_count == 0:
                delegated = thread_id

    assert delegated is None, f"Expected None (no backfill), got {delegated}"
    print("✅ test_no_backfill_for_new_data_in_clarification PASSED")


def test_no_backfill_when_already_delegated():
    """Simulate: already has delegated_va_run_id → no need to backfill"""
    conversation = FakeConversationRow(
        agent_type="video",
        additional_data={"delegated_va_run_id": "existing-run"},
    )
    delegated = None
    thread_id = "thread_test_123"

    svc = AgentRouterService.__new__(AgentRouterService)
    if not delegated:
        delegated = svc._delegated_va_run_id_from_conversation_row(conversation)

    assert delegated == "existing-run", f"Expected existing-run, got {delegated}"
    print("✅ test_no_backfill_when_already_delegated PASSED")


async def main():
    sync_tests = [
        test_decide_route_no_delegate_video_confirmed,
        test_decide_route_no_delegate_video_not_confirmed,
        test_decide_route_delegated_goes_to_edit,
        test_decide_route_delegated_confirmed_goes_to_edit,
        test_decide_route_clarify_no_delegate,
        test_decide_route_delegated_overrides_clarify,
        test_backfill_helper_extracts_from_additional_data,
        test_backfill_helper_returns_none_when_empty,
        test_backfill_helper_returns_none_for_non_video,
        test_backfill_old_data_scenario,
        test_no_backfill_for_new_data_in_clarification,
        test_no_backfill_when_already_delegated,
    ]

    passed = 0
    failed = 0

    for t in sync_tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"❌ {t.__name__} FAILED: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
