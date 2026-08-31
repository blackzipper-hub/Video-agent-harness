"""Regression: gpt-5.6 Responses content blocks must become plain text for SSE/rail.

Bug symptom:
- UI shows "[object Object]" repeatedly
- Backend: TypeError: sequence item 0: expected str instance, dict found
  at _stream_and_emit_with_output_rail → "".join(full)
"""
from __future__ import annotations

import asyncio

import pytest

from app.chat.services.agent.agent_router_service import (
    AgentRouterService,
    _stream_chunk_to_text,
)


class TestStreamChunkToText:
    def test_plain_str(self):
        assert _stream_chunk_to_text("hello") == "hello"

    def test_none_and_empty(self):
        assert _stream_chunk_to_text(None) == ""
        assert _stream_chunk_to_text([]) == ""
        assert _stream_chunk_to_text({}) == ""

    def test_responses_reasoning_only_skipped(self):
        blocks = [
            {
                "id": "rs_1",
                "summary": [],
                "type": "reasoning",
                "content": [],
                "encrypted_content": "gAAAA...",
            }
        ]
        assert _stream_chunk_to_text(blocks) == ""

    def test_responses_mixed_reasoning_and_text(self):
        blocks = [
            {"id": "rs_1", "summary": [], "type": "reasoning", "content": []},
            {"type": "text", "text": "先调色。"},
            {"type": "text", "text": "再加音效。"},
        ]
        assert _stream_chunk_to_text(blocks) == "先调色。再加音效。"

    def test_nested_content_list(self):
        block = {"type": "text", "content": [{"type": "text", "text": "nested"}]}
        assert _stream_chunk_to_text(block) == "nested"

    def test_str_of_list_is_not_used(self):
        """MCP used to do str(list) → ugly repr; extractor must return readable text."""
        blocks = [{"type": "text", "text": "可读回复"}]
        out = _stream_chunk_to_text(blocks)
        assert out == "可读回复"
        assert "[{'" not in out
        assert "[object Object]" not in out


class TestStreamAndEmitRailWithResponsesBlocks:
    @pytest.mark.asyncio
    async def test_rail_join_survives_raw_dict_list_tokens(self):
        """模拟修复前 video_edit 直接 yield chunk.content（list[dict]）。"""

        async def raw_responses_stream():
            yield [
                {"type": "reasoning", "summary": []},
                {"type": "text", "text": "Hello"},
            ]
            yield {"type": "text", "text": " world"}
            yield [{"type": "text", "text": "!"}]

        svc = object.__new__(AgentRouterService)
        full, suggestions, blocked, reason = await AgentRouterService._stream_and_emit_with_output_rail(
            svc,
            raw_responses_stream(),
            target_event="chat_response",
            conversation_id=None,
            run_id=None,
            state=None,
            send_event_func=None,
        )
        assert full == "Hello world!"
        assert blocked is False
        assert isinstance(full, str)

    @pytest.mark.asyncio
    async def test_rail_emits_only_strings_to_frontend(self):
        emitted: list = []

        async def capture_send(**kwargs):
            emitted.append(kwargs.get("message"))

        async def raw_stream():
            yield [{"type": "text", "text": "AB"}]

        svc = object.__new__(AgentRouterService)
        await AgentRouterService._stream_and_emit_with_output_rail(
            svc,
            raw_stream(),
            target_event="chat_response",
            conversation_id=1,
            run_id="r1",
            state=None,
            send_event_func=capture_send,
        )
        assert emitted
        assert all(isinstance(m, str) for m in emitted)
        assert "".join(emitted) == "AB"
        assert not any(isinstance(m, dict) for m in emitted)


def test_edit_tokens_style_normalization_matches_chat_path():
    """_edit_tokens / chat _tokens 都应先 to_text 再 yield。"""
    chunk_content = [
        {"type": "reasoning", "summary": []},
        {"type": "text", "text": "ok"},
    ]
    # 修复前：yield chunk.content → rail 收到 list → join 崩 / UI [object Object]
    assert not isinstance(_stream_chunk_to_text(chunk_content), list)
    assert _stream_chunk_to_text(chunk_content) == "ok"
