"""Regression tests for Responses API chunks in story generation."""
from __future__ import annotations

from app.services.agent.story.story_generation_service import _stream_chunk_to_text


def test_story_stream_skips_reasoning_and_keeps_visible_text():
    blocks = [
        {
            "id": "rs_internal",
            "type": "reasoning",
            "summary": [],
            "content": [],
            "encrypted_content": "gAAAA-secret",
        },
        {"type": "text", "text": "# 角色与场景连续性锚点"},
    ]

    output = _stream_chunk_to_text(blocks)

    assert output == "# 角色与场景连续性锚点"
    assert "reasoning" not in output
    assert "encrypted_content" not in output
    assert "gAAAA" not in output


def test_story_stream_does_not_stringify_unknown_objects():
    blocks = [
        {"type": "reasoning", "encrypted_content": "opaque"},
        {"type": "text", "content": [{"type": "text", "text": "visible"}]},
    ]

    assert _stream_chunk_to_text(blocks) == "visible"
