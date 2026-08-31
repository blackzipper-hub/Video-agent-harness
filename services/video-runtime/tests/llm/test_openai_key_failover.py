from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

from app.llm.openai_failover import FailoverChatOpenAI


@pytest.mark.asyncio
async def test_primary_failure_retries_once_with_backup_key(monkeypatch):
    expected = object()
    generate = AsyncMock(side_effect=[RuntimeError("primary failed"), expected])
    monkeypatch.setattr(ChatOpenAI, "_agenerate", generate)

    model = FailoverChatOpenAI(
        model="gpt-4o-mini",
        api_key="sk-primary-test-value",
        fallback_api_key="sk-backup-test-value",
    )
    result = await model._agenerate([])

    assert result is expected
    assert generate.await_count == 2


@pytest.mark.asyncio
async def test_successful_primary_call_never_uses_backup_key(monkeypatch):
    expected = object()
    generate = AsyncMock(return_value=expected)
    monkeypatch.setattr(ChatOpenAI, "_agenerate", generate)

    model = FailoverChatOpenAI(
        model="gpt-4o-mini",
        api_key="sk-primary-test-value",
        fallback_api_key="sk-backup-test-value",
    )
    result = await model._agenerate([])

    assert result is expected
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_backup_failure_is_not_retried_again(monkeypatch):
    generate = AsyncMock(side_effect=RuntimeError("failed"))
    monkeypatch.setattr(ChatOpenAI, "_agenerate", generate)

    model = FailoverChatOpenAI(
        model="gpt-4o-mini",
        api_key="sk-primary-test-value",
        fallback_api_key="sk-backup-test-value",
    )
    with pytest.raises(RuntimeError, match="failed"):
        await model._agenerate([])

    assert generate.await_count == 2


@pytest.mark.asyncio
async def test_stream_empty_chunk_before_failure_uses_backup(monkeypatch):
    calls = 0

    async def stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield ChatGenerationChunk(message=AIMessageChunk(content=""))
            raise RuntimeError("primary failed after lifecycle chunk")
        yield ChatGenerationChunk(message=AIMessageChunk(content="backup output"))

    monkeypatch.setattr(ChatOpenAI, "_astream", stream)
    model = FailoverChatOpenAI(
        model="gpt-4o-mini",
        api_key="sk-primary-test-value",
        fallback_api_key="sk-backup-test-value",
    )

    chunks = [chunk async for chunk in model._astream([])]

    assert calls == 2
    assert [chunk.message.content for chunk in chunks] == ["backup output"]


@pytest.mark.asyncio
async def test_stream_failure_after_visible_output_is_not_replayed(monkeypatch):
    calls = 0

    async def stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        yield ChatGenerationChunk(message=AIMessageChunk(content="primary output"))
        raise RuntimeError("primary failed after output")

    monkeypatch.setattr(ChatOpenAI, "_astream", stream)
    model = FailoverChatOpenAI(
        model="gpt-4o-mini",
        api_key="sk-primary-test-value",
        fallback_api_key="sk-backup-test-value",
    )

    chunks = []
    with pytest.raises(RuntimeError, match="after output"):
        async for chunk in model._astream([]):
            chunks.append(chunk)

    assert calls == 1
    assert [chunk.message.content for chunk in chunks] == ["primary output"]


@pytest.mark.asyncio
async def test_backup_stream_rate_limit_waits_and_retries(monkeypatch):
    calls = 0
    sleeps: list[float] = []

    async def stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("primary failed")
            yield  # pragma: no cover
        if calls == 2:
            yield ChatGenerationChunk(message=AIMessageChunk(content=""))
            raise RuntimeError("Rate limit reached. Please try again in 785ms.")
        yield ChatGenerationChunk(message=AIMessageChunk(content="backup output"))

    async def sleep(delay: float):
        sleeps.append(delay)

    monkeypatch.setattr(ChatOpenAI, "_astream", stream)
    monkeypatch.setattr("app.llm.openai_failover.asyncio.sleep", sleep)
    model = FailoverChatOpenAI(
        model="gpt-4o-mini",
        api_key="sk-primary-test-value",
        fallback_api_key="sk-backup-test-value",
    )

    chunks = [chunk async for chunk in model._astream([])]

    assert calls == 3
    assert sleeps == [pytest.approx(0.885)]
    assert [chunk.message.content for chunk in chunks] == ["backup output"]


@pytest.mark.asyncio
async def test_backup_without_credits_aborts_without_retry(monkeypatch):
    calls = 0

    async def stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("primary failed")
            yield  # pragma: no cover
        raise RuntimeError("You have no credits remaining. Add credits to continue.")
        yield  # pragma: no cover

    monkeypatch.setattr(ChatOpenAI, "_astream", stream)
    model = FailoverChatOpenAI(
        model="gpt-4o-mini",
        api_key="sk-primary-test-value",
        fallback_api_key="sk-backup-test-value",
    )

    with pytest.raises(RuntimeError, match="no credits remaining"):
        async for _ in model._astream([]):
            pass

    # Exactly one primary call and one backup call: credit exhaustion is a
    # terminal billing error, not a transient rate limit.
    assert calls == 2
