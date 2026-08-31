"""Middleware that repairs orphaned tool_calls / tool responses before calling the LLM.

When a tool execution is interrupted (crash, timeout, server restart, etc.),
the checkpoint may end up with an AIMessage containing tool_calls but no
matching ToolMessage response.  OpenAI rejects such histories with:

    "An assistant message with 'tool_calls' must be followed by tool messages
     responding to each 'tool_call_id'."

This middleware runs in ``before_model`` and silently strips:
  - AIMessage.tool_calls whose tool_call_id has no ToolMessage
  - ToolMessages whose tool_call_id has no AIMessage.tool_calls entry
  - AIMessages left with zero tool_calls after stripping (and no text content)

Inspired by OpenHands ``ConversationMemory._filter_unmatched_tool_calls``.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


class SanitizeToolCallsMiddleware(AgentMiddleware):  # type: ignore[type-arg]
    """Strip orphaned tool_calls / tool responses so the LLM never sees invalid history."""

    tools = ()

    def before_model(
        self, state: AgentState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        return self._sanitize(state)

    async def abefore_model(
        self, state: AgentState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        return self._sanitize(state)

    @staticmethod
    def _sanitize(state: AgentState) -> dict[str, Any] | None:  # type: ignore[type-arg]
        messages = state.get("messages")
        if not messages:
            return None

        tool_call_ids: set[str] = set()
        tool_response_ids: set[str] = set()

        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    if tc_id:
                        tool_call_ids.add(tc_id)
            elif isinstance(msg, ToolMessage) and msg.tool_call_id:
                tool_response_ids.add(msg.tool_call_id)

        orphan_calls = tool_call_ids - tool_response_ids
        orphan_responses = tool_response_ids - tool_call_ids

        if not orphan_calls and not orphan_responses:
            return None

        if orphan_calls:
            logger.warning(
                "SanitizeToolCalls: %d orphaned tool_call(s) without response — %s",
                len(orphan_calls), orphan_calls,
            )
        if orphan_responses:
            logger.warning(
                "SanitizeToolCalls: %d orphaned tool response(s) without call — %s",
                len(orphan_responses), orphan_responses,
            )

        cleaned: list[Any] = []
        for msg in messages:
            if isinstance(msg, ToolMessage) and msg.tool_call_id:
                if msg.tool_call_id in orphan_responses:
                    continue
                cleaned.append(msg)

            elif isinstance(msg, AIMessage) and msg.tool_calls:
                kept = [
                    tc for tc in msg.tool_calls
                    if (tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None))
                       not in orphan_calls
                ]
                if len(kept) == len(msg.tool_calls):
                    cleaned.append(msg)
                elif kept:
                    cleaned.append(msg.model_copy(update={"tool_calls": kept}))
                elif msg.content:
                    cleaned.append(msg.model_copy(update={"tool_calls": []}))
                # else: AIMessage with no content and no remaining tool_calls → drop
            else:
                cleaned.append(msg)

        logger.info(
            "SanitizeToolCalls: cleaned %d → %d messages",
            len(messages), len(cleaned),
        )
        return {
            "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *cleaned],
        }
