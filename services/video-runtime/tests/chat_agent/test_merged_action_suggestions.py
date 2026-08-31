"""Chat + action suggestions merged mode unit tests (no LLM)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.chat.services.agent.action_suggestions_merge import (
    ACTION_SUGGESTIONS_END,
    ACTION_SUGGESTIONS_START,
    ActionSuggestionsStreamSplitter,
    parse_formatted_action_suggestions_body,
    parse_merged_action_suggestions,
    split_chat_reply_and_suggestions_tail,
)


class _SuggestionItem(BaseModel):
    label: str
    message: str


class _SuggestionsResult(BaseModel):
    reply_type: str
    suggestions: list[_SuggestionItem] = Field(min_length=2)


class TestActionSuggestionsStreamSplitter:
    def test_splits_start_end_across_chunks(self):
        reply = "你好，选一个风格吧。"
        body = (
            "choice\n"
            "活泼|我想要活泼一点\n"
            "沉稳|我想要沉稳一点"
        )
        full = reply + ACTION_SUGGESTIONS_START + body + ACTION_SUGGESTIONS_END
        splitter = ActionSuggestionsStreamSplitter()
        visible_emitted = []
        suggestions_emitted = []
        chunk_size = 7
        for i in range(0, len(full), chunk_size):
            v, s = splitter.feed(full[i : i + chunk_size])
            if v:
                visible_emitted.append(v)
            if s:
                suggestions_emitted.append(s)
        visible, tail = splitter.finish()
        assert visible == reply
        assert tail == body
        assert ACTION_SUGGESTIONS_START not in "".join(visible_emitted)
        assert ACTION_SUGGESTIONS_END not in "".join(suggestions_emitted)
        assert splitter.end_reached

    def test_no_marker_returns_all_visible(self):
        text = "只有聊天，没有分隔符。"
        splitter = ActionSuggestionsStreamSplitter()
        v, s = splitter.feed(text)
        assert v == text
        assert s == ""
        visible, tail = splitter.finish()
        assert visible == text
        assert tail == ""


class TestSplitAndParseMergedSuggestions:
    def test_split_chat_reply_and_suggestions_tail(self):
        body = "choice\n活泼|我想要活泼一点\n沉稳|我想要沉稳一点"
        reply, tail = split_chat_reply_and_suggestions_tail(
            f"回复正文\n{ACTION_SUGGESTIONS_START}\n{body}\n{ACTION_SUGGESTIONS_END}"
        )
        assert reply == "回复正文"
        assert "活泼" in tail

    def test_parse_formatted_body(self):
        raw = (
            "choice\n"
            "活泼|我想要活泼一点\n"
            "沉稳|我想要沉稳一点"
        )
        parsed = parse_formatted_action_suggestions_body(raw, result_model=_SuggestionsResult)
        assert parsed is not None
        assert parsed.reply_type == "choice"
        assert len(parsed.suggestions) == 2

    def test_parse_merged_prefers_formatted(self):
        raw = "open\nA|我选A\nB|我选B\nC|我选C"
        parsed = parse_merged_action_suggestions(raw, result_model=_SuggestionsResult)
        assert parsed is not None
        assert parsed.reply_type == "open"

    def test_parse_invalid_returns_none(self):
        assert parse_merged_action_suggestions("not valid", result_model=_SuggestionsResult) is None
