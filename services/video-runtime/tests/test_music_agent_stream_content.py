"""Music agent stream must normalize list-shaped chunk.content before join."""
from __future__ import annotations

from app.services.agent.utils.prompt_utils import llm_chunk_content_to_text


def test_music_stream_join_survives_responses_blocks():
    chunks = [
        [{"type": "reasoning", "summary": []}, {"type": "text", "text": "Generating"}],
        {"type": "text", "text": " cue"},
        [{"type": "text", "text": " done"}],
        "!",
    ]
    parts = [llm_chunk_content_to_text(chunk) for chunk in chunks]
    assert "".join(parts) == "Generating cue done!"


def test_nested_text_list_does_not_raise():
    chunk = [{"type": "text", "text": ["a", "b"]}]
    assert llm_chunk_content_to_text(chunk) == "ab"
    assert "".join([llm_chunk_content_to_text(chunk)]) == "ab"
