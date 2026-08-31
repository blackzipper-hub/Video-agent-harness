"""Chat 流式输出与推荐动作格式化 tail 的解析工具（无 LangGraph 依赖）。"""
from __future__ import annotations

import json
from typing import List, Optional, Tuple

from pydantic import BaseModel

# 旧 JSON 分隔符（仅用于历史消息 / 测试兼容）
ACTION_SUGGESTIONS_JSON_MARKER = "<<<ACTION_SUGGESTIONS_JSON>>>"

ACTION_SUGGESTIONS_START = "<<<ACTION_SUGGESTIONS_START>>>"
ACTION_SUGGESTIONS_END = "<<<ACTION_SUGGESTIONS_END>>>"


def _marker_holdback_suffix_len(text: str, marker: str) -> int:
    max_hold = 0
    for i in range(1, len(marker)):
        if text.endswith(marker[:i]):
            max_hold = i
    return max_hold


class ActionSuggestionsStreamSplitter:
    """将 LLM 流式输出拆为可见聊天区与 START/END 之间的格式化推荐动作区。"""

    def __init__(
        self,
        start_marker: str = ACTION_SUGGESTIONS_START,
        end_marker: str = ACTION_SUGGESTIONS_END,
    ) -> None:
        self._start = start_marker
        self._end = end_marker
        self._phase = "visible"  # visible | suggestions | done
        self._visible_parts: List[str] = []
        self._suggestions_parts: List[str] = []
        self._pending = ""
        self._end_reached = False

    @property
    def end_reached(self) -> bool:
        return self._end_reached

    @property
    def phase(self) -> str:
        return self._phase

    def feed(self, piece: str) -> Tuple[str, str]:
        """返回 (visible_emit, suggestions_emit)，均为本次 feed 应推 SSE 的增量。"""
        if not piece or self._phase == "done":
            return "", ""

        visible_emit: List[str] = []
        suggestions_emit: List[str] = []

        if self._phase == "visible":
            self._pending += piece
            idx = self._pending.find(self._start)
            if idx >= 0:
                before = self._pending[:idx]
                after = self._pending[idx + len(self._start) :]
                if before:
                    self._visible_parts.append(before)
                    visible_emit.append(before)
                self._phase = "suggestions"
                self._pending = after
                if after:
                    hold = _marker_holdback_suffix_len(self._pending, self._end)
                    emit_part = self._pending[:-hold] if hold else self._pending
                    self._pending = self._pending[-hold:] if hold else ""
                    if emit_part:
                        self._suggestions_parts.append(emit_part)
                        suggestions_emit.append(emit_part)
            else:
                hold = _marker_holdback_suffix_len(self._pending, self._start)
                emit_part = self._pending[:-hold] if hold else self._pending
                self._pending = self._pending[-hold:] if hold else ""
                if emit_part:
                    self._visible_parts.append(emit_part)
                    visible_emit.append(emit_part)
            return "".join(visible_emit), "".join(suggestions_emit)

        # suggestions phase
        self._pending += piece
        idx = self._pending.find(self._end)
        if idx >= 0:
            before = self._pending[:idx]
            self._pending = ""
            self._phase = "done"
            self._end_reached = True
            if before:
                self._suggestions_parts.append(before)
                suggestions_emit.append(before)
        else:
            hold = _marker_holdback_suffix_len(self._pending, self._end)
            emit_part = self._pending[:-hold] if hold else self._pending
            self._pending = self._pending[-hold:] if hold else ""
            if emit_part:
                self._suggestions_parts.append(emit_part)
                suggestions_emit.append(emit_part)
        return "", "".join(suggestions_emit)

    def finish(self) -> Tuple[str, str]:
        if self._phase == "suggestions" and self._pending:
            self._suggestions_parts.append(self._pending)
            self._pending = ""
        elif self._phase == "visible" and self._pending:
            self._visible_parts.append(self._pending)
            self._pending = ""
        return "".join(self._visible_parts), "".join(self._suggestions_parts)


# 兼容旧测试名
ActionSuggestionsMarkerSplitter = ActionSuggestionsStreamSplitter


def split_chat_reply_and_suggestions_tail(
    full_text: str,
    start_marker: str = ACTION_SUGGESTIONS_START,
    end_marker: str = ACTION_SUGGESTIONS_END,
    *,
    legacy_json_marker: str = ACTION_SUGGESTIONS_JSON_MARKER,
) -> Tuple[str, str]:
    text = full_text or ""
    if start_marker in text:
        before_start, _, after_start = text.partition(start_marker)
        body, _, _after_end = after_start.partition(end_marker)
        return before_start.strip(), body.strip()
    if legacy_json_marker in text:
        before, _, after = text.partition(legacy_json_marker)
        return before.strip(), after.strip()
    return text.strip(), ""


def parse_formatted_action_suggestions_body(
    raw_body: str,
    *,
    result_model: type[BaseModel],
) -> Optional[BaseModel]:
    """解析 START/END 之间的格式化推荐动作（非 JSON）。"""
    body = (raw_body or "").strip()
    if not body:
        return None

    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return None

    reply_type = lines[0].lower()
    item_lines: List[str]
    if reply_type in ("choice", "open"):
        item_lines = lines[1:]
    else:
        reply_type = "choice"
        item_lines = lines

    suggestions: List[dict] = []
    for line in item_lines:
        if line.startswith("#"):
            continue
        if "|" in line:
            label, _, message = line.partition("|")
        else:
            label, message = line, line
        label = label.strip()
        message = message.strip()
        if not label and not message:
            continue
        if not label:
            label = message[:15]
        if not message:
            message = label
        suggestions.append({"label": label, "message": message})

    if len(suggestions) < 2:
        return None

    try:
        return result_model.model_validate(
            {"reply_type": reply_type, "suggestions": suggestions}
        )
    except Exception:
        return None


def parse_merged_action_suggestions(
    raw_tail: str,
    *,
    result_model: type[BaseModel],
) -> Optional[BaseModel]:
    """优先解析格式化 body，失败再尝试历史 JSON tail。"""
    parsed = parse_formatted_action_suggestions_body(raw_tail, result_model=result_model)
    if parsed is not None:
        return parsed
    return parse_merged_action_suggestions_json(raw_tail, result_model=result_model)


def parse_merged_action_suggestions_json(
    raw_tail: str,
    *,
    result_model: type[BaseModel],
) -> Optional[BaseModel]:
    tail = (raw_tail or "").strip()
    if not tail:
        return None
    start = tail.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(tail[start:])
        return result_model.model_validate(data)
    except Exception:
        return None
