"""OpenAI clients that retry one failed call with a configured backup key."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr


logger = logging.getLogger(__name__)


async def _audit_async(**payload: Any) -> None:
    from app.chat.v2.token_usage import audit_llm_attempt
    await audit_llm_attempt(**payload)


def _audit_sync(**payload: Any) -> None:
    """Best-effort audit for synchronous calls; V2 normally uses async paths."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_audit_async(**payload))


def fallback_openai_api_key(explicit: str | None = None) -> str:
    return (explicit or os.getenv("OPENAI_API_KEY_FALLBACK", "")).strip()


class FailoverChatOpenAI(ChatOpenAI):
    """Use the primary key first and retry the same call once with a backup key.

    Streaming calls only fail over before their first meaningful output. OpenAI's
    Responses API can emit empty lifecycle chunks before reporting an account error;
    those chunks must not prevent the backup-key retry.
    """

    _fallback_model: ChatOpenAI | None = PrivateAttr(default=None)
    _rate_limit_max_retries: int = PrivateAttr(default=3)
    _rate_limit_max_delay_seconds: float = PrivateAttr(default=30.0)

    def __init__(
        self,
        *args: Any,
        fallback_api_key: str | None = None,
        rate_limit_max_retries: int | None = None,
        rate_limit_max_delay_seconds: float | None = None,
        **kwargs: Any,
    ) -> None:
        backup = fallback_openai_api_key(fallback_api_key)
        primary = str(kwargs.get("api_key") or os.getenv("OPENAI_API_KEY", "")).strip()
        super().__init__(*args, **kwargs)
        self._rate_limit_max_retries = max(
            0,
            rate_limit_max_retries
            if rate_limit_max_retries is not None
            else int(os.getenv("OPENAI_RATE_LIMIT_MAX_RETRIES", "3")),
        )
        self._rate_limit_max_delay_seconds = max(
            0.1,
            rate_limit_max_delay_seconds
            if rate_limit_max_delay_seconds is not None
            else float(os.getenv("OPENAI_RATE_LIMIT_MAX_DELAY_SECONDS", "30")),
        )
        if backup and backup != primary:
            fallback_kwargs = dict(kwargs)
            fallback_kwargs["api_key"] = backup
            self._fallback_model = ChatOpenAI(*args, **fallback_kwargs)

    @staticmethod
    def _can_retry(exc: BaseException) -> bool:
        return not isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit))

    def _log_failover(self, exc: BaseException) -> None:
        logger.warning(
            "OpenAI primary call failed; retrying once with backup key (%s)",
            type(exc).__name__,
        )

    @staticmethod
    def _mark_key_slot(result: Any, slot: str) -> Any:
        for generations in getattr(result, "generations", None) or []:
            for generation in generations or []:
                message = getattr(generation, "message", None)
                if message is not None:
                    metadata = dict(getattr(message, "response_metadata", None) or {})
                    metadata["cuti_key_slot"] = slot
                    message.response_metadata = metadata
        return result

    @staticmethod
    def _is_rate_limit_error(exc: BaseException) -> bool:
        message = str(exc).lower()
        return type(exc).__name__ == "RateLimitError" or "rate limit reached" in message

    def _rate_limit_delay(self, exc: BaseException, retry_number: int) -> float:
        message = str(exc).lower()
        match = re.search(r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|s)", message)
        if match:
            delay = float(match.group(1))
            if match.group(2) == "ms":
                delay /= 1000.0
            # A small margin prevents retrying on the same token-bucket boundary.
            delay += 0.1
        else:
            delay = float(2 ** (retry_number - 1))
        return min(max(delay, 0.1), self._rate_limit_max_delay_seconds)

    def _log_rate_limit_retry(self, exc: BaseException, retry_number: int, delay: float) -> None:
        logger.warning(
            "OpenAI backup key rate-limited; retrying in %.3fs (attempt %d/%d, %s)",
            delay,
            retry_number,
            self._rate_limit_max_retries,
            type(exc).__name__,
        )

    def _fallback_generate(self, *args: Any, **kwargs: Any):
        assert self._fallback_model is not None
        retries = 0
        while True:
            try:
                _audit_sync(key_slot="fallback", attempt=retries + 1, reason="failover", status="started")
                return self._mark_key_slot(
                    self._fallback_model._generate(*args, **kwargs), "fallback",
                )
            except BaseException as exc:
                if not self._is_rate_limit_error(exc) or retries >= self._rate_limit_max_retries:
                    raise
                retries += 1
                delay = self._rate_limit_delay(exc, retries)
                _audit_sync(key_slot="fallback", attempt=retries, reason="rate_limit", status="retry_scheduled", delay_seconds=delay, error_type=type(exc).__name__)
                self._log_rate_limit_retry(exc, retries, delay)
                time.sleep(delay)

    async def _fallback_agenerate(self, *args: Any, **kwargs: Any):
        assert self._fallback_model is not None
        retries = 0
        while True:
            try:
                await _audit_async(key_slot="fallback", attempt=retries + 1, reason="failover", status="started")
                return self._mark_key_slot(
                    await self._fallback_model._agenerate(*args, **kwargs), "fallback",
                )
            except BaseException as exc:
                if not self._is_rate_limit_error(exc) or retries >= self._rate_limit_max_retries:
                    raise
                retries += 1
                delay = self._rate_limit_delay(exc, retries)
                await _audit_async(key_slot="fallback", attempt=retries, reason="rate_limit", status="retry_scheduled", delay_seconds=delay, error_type=type(exc).__name__)
                self._log_rate_limit_retry(exc, retries, delay)
                await asyncio.sleep(delay)

    @staticmethod
    def _is_meaningful_stream_chunk(chunk: Any) -> bool:
        """Return whether yielding *chunk* commits visible output or a tool call."""
        message = getattr(chunk, "message", chunk)
        if getattr(message, "tool_calls", None) or getattr(message, "tool_call_chunks", None):
            return True
        additional = getattr(message, "additional_kwargs", None) or {}
        if additional.get("tool_calls") or additional.get("function_call"):
            return True

        content = getattr(message, "content", None)
        if isinstance(content, str):
            return bool(content)
        if isinstance(content, list):
            for block in content:
                if isinstance(block, str) and block:
                    return True
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type", ""))
                # Reasoning/lifecycle blocks are internal. Text and tool-related
                # blocks are externally observable and therefore cannot be replayed.
                if block_type in {"tool_call", "function_call", "output_text", "text"}:
                    if block_type in {"tool_call", "function_call"}:
                        return True
                    if block.get("text") or block.get("content"):
                        return True
        return False

    def _generate(self, *args: Any, **kwargs: Any):
        try:
            return super()._generate(*args, **kwargs)
        except BaseException as exc:
            if self._fallback_model is None or not self._can_retry(exc):
                raise
            _audit_sync(key_slot="primary", attempt=1, reason="primary_failure", status="failed", error_type=type(exc).__name__, error=str(exc)[:1000])
            self._log_failover(exc)
            return self._fallback_generate(*args, **kwargs)

    async def _agenerate(self, *args: Any, **kwargs: Any):
        try:
            return await super()._agenerate(*args, **kwargs)
        except BaseException as exc:
            if self._fallback_model is None or not self._can_retry(exc):
                raise
            await _audit_async(key_slot="primary", attempt=1, reason="primary_failure", status="failed", error_type=type(exc).__name__, error=str(exc)[:1000])
            self._log_failover(exc)
            return await self._fallback_agenerate(*args, **kwargs)

    def _fallback_stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        assert self._fallback_model is not None
        retries = 0
        while True:
            committed = False
            pending: list[Any] = []
            try:
                _audit_sync(key_slot="fallback", attempt=retries + 1, reason="failover_stream", status="started")
                for chunk in self._fallback_model._stream(*args, **kwargs):
                    if committed:
                        yield chunk
                    elif self._is_meaningful_stream_chunk(chunk):
                        committed = True
                        yield from pending
                        pending.clear()
                        yield chunk
                    else:
                        pending.append(chunk)
                yield from pending
                return
            except BaseException as exc:
                if (
                    committed
                    or not self._is_rate_limit_error(exc)
                    or retries >= self._rate_limit_max_retries
                ):
                    raise
                retries += 1
                delay = self._rate_limit_delay(exc, retries)
                _audit_sync(key_slot="fallback", attempt=retries, reason="rate_limit_stream", status="retry_scheduled", delay_seconds=delay, error_type=type(exc).__name__)
                self._log_rate_limit_retry(exc, retries, delay)
                time.sleep(delay)

    async def _fallback_astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        assert self._fallback_model is not None
        retries = 0
        while True:
            committed = False
            pending: list[Any] = []
            try:
                await _audit_async(key_slot="fallback", attempt=retries + 1, reason="failover_stream", status="started")
                async for chunk in self._fallback_model._astream(*args, **kwargs):
                    if committed:
                        yield chunk
                    elif self._is_meaningful_stream_chunk(chunk):
                        committed = True
                        for buffered in pending:
                            yield buffered
                        pending.clear()
                        yield chunk
                    else:
                        pending.append(chunk)
                for buffered in pending:
                    yield buffered
                return
            except BaseException as exc:
                if (
                    committed
                    or not self._is_rate_limit_error(exc)
                    or retries >= self._rate_limit_max_retries
                ):
                    raise
                retries += 1
                delay = self._rate_limit_delay(exc, retries)
                await _audit_async(key_slot="fallback", attempt=retries, reason="rate_limit_stream", status="retry_scheduled", delay_seconds=delay, error_type=type(exc).__name__)
                self._log_rate_limit_retry(exc, retries, delay)
                await asyncio.sleep(delay)

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        committed = False
        pending: list[Any] = []
        try:
            for chunk in super()._stream(*args, **kwargs):
                if committed:
                    yield chunk
                elif self._is_meaningful_stream_chunk(chunk):
                    committed = True
                    yield from pending
                    pending.clear()
                    yield chunk
                else:
                    pending.append(chunk)
            yield from pending
        except BaseException as exc:
            if committed or self._fallback_model is None or not self._can_retry(exc):
                raise
            _audit_sync(key_slot="primary", attempt=1, reason="primary_stream_failure", status="failed", error_type=type(exc).__name__, error=str(exc)[:1000])
            self._log_failover(exc)
            yield from self._fallback_stream(*args, **kwargs)

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        committed = False
        pending: list[Any] = []
        try:
            async for chunk in super()._astream(*args, **kwargs):
                if committed:
                    yield chunk
                elif self._is_meaningful_stream_chunk(chunk):
                    committed = True
                    for buffered in pending:
                        yield buffered
                    pending.clear()
                    yield chunk
                else:
                    pending.append(chunk)
            for buffered in pending:
                yield buffered
        except BaseException as exc:
            if committed or self._fallback_model is None or not self._can_retry(exc):
                raise
            await _audit_async(key_slot="primary", attempt=1, reason="primary_stream_failure", status="failed", error_type=type(exc).__name__, error=str(exc)[:1000])
            self._log_failover(exc)
            async for chunk in self._fallback_astream(*args, **kwargs):
                yield chunk
