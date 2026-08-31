"""Regression: completion-message stream must normalize Responses content blocks.

Bug symptom (post model upgrade):
- UI briefly shows "[object Object]" while streaming
- Backend: TypeError: sequence item 0: expected str instance, list found
- Then i18n fallback like "故事梗概生成完成" replaces the rich completion bubble
"""
from __future__ import annotations

from app.services.agent.utils.prompt_utils import llm_chunk_content_to_text


class TestLlmChunkContentToText:
    def test_plain_str(self):
        assert llm_chunk_content_to_text("hello") == "hello"

    def test_none_and_empty(self):
        assert llm_chunk_content_to_text(None) == ""
        assert llm_chunk_content_to_text([]) == ""
        assert llm_chunk_content_to_text({}) == ""

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
        assert llm_chunk_content_to_text(blocks) == ""

    def test_responses_mixed_reasoning_and_text(self):
        blocks = [
            {"id": "rs_1", "summary": [], "type": "reasoning", "content": []},
            {"type": "text", "text": "女主用职场话术拆解死亡倒计时。"},
            {"type": "text", "text": "结尾事业线开启。"},
        ]
        assert (
            llm_chunk_content_to_text(blocks)
            == "女主用职场话术拆解死亡倒计时。结尾事业线开启。"
        )

    def test_join_survives_list_chunks(self):
        parts = [
            llm_chunk_content_to_text(
                [{"type": "reasoning", "summary": []}, {"type": "text", "text": "A"}]
            ),
            llm_chunk_content_to_text({"type": "text", "text": "B"}),
            llm_chunk_content_to_text([{"type": "text", "text": "C"}]),
        ]
        assert "".join(parts) == "ABC"
