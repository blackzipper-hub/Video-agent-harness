"""
Empty-response resilience: ErrorBucket, retries, fallback routes, and the
`resilience_empty_reason` schema hook.

Run:
  pytest tests/llm/test_llm_resilience_empty_check.py -v
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from app.services.agent.utils.llm_resilience import (
    DEFAULT_RESILIENCE,
    EmptyStructuredResponseError,
    ErrorBucket,
    ResilienceContext,
    StructuredResilienceKind,
    _extract_semantic_result,
    _wrap_invoke_with_result_check,
    classify_llm_exception,
    execute_with_resilience,
)


# ==================== 1) 分桶 / 默认配置 ====================


def test_classify_empty_structured_response_error_to_empty_bucket():
    exc = EmptyStructuredResponseError("scenes is empty", raw={"structured_response": None})
    assert classify_llm_exception(exc) == ErrorBucket.EMPTY_RESPONSE


def test_empty_response_error_carries_raw_for_diagnostics():
    raw = {"structured_response": None, "messages": []}
    exc = EmptyStructuredResponseError("structured_response is None", raw=raw)
    assert exc.raw is raw
    assert str(exc) == "structured_response is None"


def test_default_resilience_has_empty_response_per_bucket_entry():
    pb = DEFAULT_RESILIENCE["per_bucket"]
    assert "empty_response" in pb
    assert pb["empty_response"]["same_route_extra_retries"] == 1


# ==================== 2) _extract_semantic_result ====================


class _FakeParsed:
    """轻量 pydantic-like 占位（不引入 BaseModel，避免不必要依赖）。"""

    def __init__(self, value: Any = "sentinel"):
        self.value = value


def test_extract_semantic_create_agent_dict():
    parsed = _FakeParsed()
    out = {"structured_response": parsed, "messages": []}
    sem = _extract_semantic_result(out, kind=StructuredResilienceKind.CREATE_AGENT, include_raw=True)
    assert sem is parsed


def test_extract_semantic_create_agent_missing_key_returns_none():
    out = {"messages": []}
    sem = _extract_semantic_result(out, kind=StructuredResilienceKind.CREATE_AGENT, include_raw=True)
    assert sem is None


def test_extract_semantic_create_react_agent_dict():
    parsed = _FakeParsed()
    out = {"structured_response": parsed}
    sem = _extract_semantic_result(out, kind=StructuredResilienceKind.CREATE_REACT_AGENT, include_raw=False)
    assert sem is parsed


def test_extract_semantic_structured_chat_messages_include_raw_true():
    parsed = _FakeParsed()
    out = {"parsed": parsed, "raw": "AIMessage"}
    sem = _extract_semantic_result(
        out, kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES, include_raw=True
    )
    assert sem is parsed


def test_extract_semantic_structured_chat_messages_include_raw_false():
    parsed = _FakeParsed()
    sem = _extract_semantic_result(
        parsed,
        kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
        include_raw=False,
    )
    assert sem is parsed


def test_extract_semantic_structured_chat_messages_include_raw_true_parsed_none():
    out = {"parsed": None, "raw": "AIMessage"}
    sem = _extract_semantic_result(
        out, kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES, include_raw=True
    )
    assert sem is None


# ==================== 3) _wrap_invoke_with_result_check ====================


@pytest.mark.asyncio
async def test_wrap_builtin_none_triggers_when_structured_response_is_none():
    async def orig(route, mc):
        return {"structured_response": None, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )
    with pytest.raises(EmptyStructuredResponseError, match="structured_response is None"):
        await wrapped(("m", "provider"), {"model": "m"})


@pytest.mark.asyncio
async def test_wrap_builtin_none_disabled_passes_none_through():
    async def orig(route, mc):
        return {"structured_response": None, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=False,
        log_context=None,
    )
    out = await wrapped(("m", "provider"), {"model": "m"})
    assert out["structured_response"] is None


class _SemWithEmptyHook:
    """模拟带 resilience_empty_reason 的 pydantic 实例。"""

    def __init__(self, reason: Optional[str]):
        self._reason = reason

    def resilience_empty_reason(self) -> Optional[str]:
        return self._reason


@pytest.mark.asyncio
async def test_wrap_schema_hook_triggers_when_reason_returned():
    sem = _SemWithEmptyHook("scenes is empty")
    async def orig(route, mc):
        return {"structured_response": sem, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )
    with pytest.raises(EmptyStructuredResponseError, match="scenes is empty"):
        await wrapped(("m", "provider"), {"model": "m"})


@pytest.mark.asyncio
async def test_wrap_schema_hook_returns_none_means_ok():
    sem = _SemWithEmptyHook(None)
    async def orig(route, mc):
        return {"structured_response": sem, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )
    out = await wrapped(("m", "provider"), {"model": "m"})
    assert out["structured_response"] is sem


@pytest.mark.asyncio
async def test_wrap_schema_hook_disabled_does_not_call_hook():
    sem = _SemWithEmptyHook("would trigger")
    async def orig(route, mc):
        return {"structured_response": sem, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=False,
        log_context=None,
    )
    out = await wrapped(("m", "provider"), {"model": "m"})
    assert out["structured_response"] is sem


@pytest.mark.asyncio
async def test_wrap_schema_hook_exception_is_swallowed():
    class _SemBadHook:
        def resilience_empty_reason(self) -> Optional[str]:
            raise RuntimeError("hook bug")

    async def orig(route, mc):
        return {"structured_response": _SemBadHook(), "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )
    out = await wrapped(("m", "provider"), {"model": "m"})
    assert "structured_response" in out


@pytest.mark.asyncio
async def test_wrap_schema_without_hook_attribute_is_fine():
    sem = _FakeParsed()
    async def orig(route, mc):
        return {"structured_response": sem, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )
    out = await wrapped(("m", "provider"), {"model": "m"})
    assert out["structured_response"] is sem


@pytest.mark.asyncio
async def test_wrap_result_validator_triggers():
    sem = _FakeParsed("present")
    async def orig(route, mc):
        return {"structured_response": sem, "messages": []}

    def validator(s: Any) -> Optional[str]:
        return "custom reason" if s.value == "present" else None

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=validator,
        builtin_empty_check=True,
        log_context=None,
    )
    with pytest.raises(EmptyStructuredResponseError, match="custom reason"):
        await wrapped(("m", "provider"), {"model": "m"})


@pytest.mark.asyncio
async def test_wrap_result_validator_runs_after_schema_hook():
    """schema 钩子先抛 → 不应再走 result_validator（避免覆盖更具体的 reason）。"""
    sem = _SemWithEmptyHook("schema-empty")
    validator_called = {"n": 0}

    def validator(s: Any) -> Optional[str]:
        validator_called["n"] += 1
        return "validator-empty"

    async def orig(route, mc):
        return {"structured_response": sem, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=validator,
        builtin_empty_check=True,
        log_context=None,
    )
    with pytest.raises(EmptyStructuredResponseError, match="schema-empty"):
        await wrapped(("m", "provider"), {"model": "m"})
    assert validator_called["n"] == 0


@pytest.mark.asyncio
async def test_wrap_result_validator_exception_is_swallowed():
    sem = _FakeParsed()

    def validator(s: Any) -> Optional[str]:
        raise RuntimeError("validator bug")

    async def orig(route, mc):
        return {"structured_response": sem, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=validator,
        builtin_empty_check=True,
        log_context=None,
    )
    out = await wrapped(("m", "provider"), {"model": "m"})
    assert out["structured_response"] is sem


@pytest.mark.asyncio
async def test_wrap_structured_chat_messages_include_raw_true():
    sem = _SemWithEmptyHook("scenes is empty")
    async def orig(route, mc):
        return {"parsed": sem, "raw": "AIMessage"}

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )
    with pytest.raises(EmptyStructuredResponseError, match="scenes is empty"):
        await wrapped(("m", "provider"), {"model": "m"})


@pytest.mark.asyncio
async def test_wrap_structured_chat_messages_include_raw_false():
    sem = _SemWithEmptyHook("matches is empty")
    async def orig(route, mc):
        return sem  # 直接返回 pydantic 实例

    wrapped = _wrap_invoke_with_result_check(
        orig,
        kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
        include_raw=False,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )
    with pytest.raises(EmptyStructuredResponseError, match="matches is empty"):
        await wrapped(("m", "provider"), {"model": "m"})


# ==================== 5) execute_with_resilience 端到端 ====================


def _mcs_for_routes(routes):
    return [{"model": r[0], "timeout": 180} for r in routes]


@pytest.mark.asyncio
async def test_empty_response_default_one_retry_then_success():
    """默认 per_bucket.empty_response = 1 次同路由重试：首次空 → 重试成功。"""
    ctx = ResilienceContext(
        same_route_extra_retries=DEFAULT_RESILIENCE["same_route_extra_retries"],
        per_bucket=DEFAULT_RESILIENCE["per_bucket"],
    )
    sem_empty = _SemWithEmptyHook("scenes is empty")
    sem_good = _SemWithEmptyHook(None)
    n = {"c": 0}
    _routes = [("gemini", "provider")]

    async def orig_invoke(route, mc):
        n["c"] += 1
        if n["c"] == 1:
            return {"structured_response": sem_empty, "messages": []}
        return {"structured_response": sem_good, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig_invoke,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )

    out = await execute_with_resilience(
        wrapped,
        routes=_routes,
        route_model_configs=_mcs_for_routes(_routes),
        context=ctx,
    )
    assert out["structured_response"] is sem_good
    assert n["c"] == 2


@pytest.mark.asyncio
async def test_empty_response_exhaust_retry_then_fallback_route_succeeds():
    """同路由重试耗尽 → 切换到 fallback model 一次成功。"""
    ctx = ResilienceContext(
        same_route_extra_retries=DEFAULT_RESILIENCE["same_route_extra_retries"],
        per_bucket=DEFAULT_RESILIENCE["per_bucket"],
    )
    sem_empty = _SemWithEmptyHook("scenes is empty")
    sem_good = _SemWithEmptyHook(None)
    seen: list[tuple[str, str | None]] = []
    _routes = [("gemini", "provider"), ("gpt", "provider")]

    async def orig_invoke(route, mc):
        seen.append(route)
        if route == ("gemini", "provider"):
            return {"structured_response": sem_empty, "messages": []}
        return {"structured_response": sem_good, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig_invoke,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )
    out = await execute_with_resilience(
        wrapped,
        routes=_routes,
        route_model_configs=_mcs_for_routes(_routes),
        context=ctx,
    )
    assert out["structured_response"] is sem_good
    # gemini × 2 (init + 1 retry) + gpt × 1
    assert seen == [("gemini", "provider"), ("gemini", "provider"), ("gpt", "provider")]


@pytest.mark.asyncio
async def test_empty_response_all_routes_exhausted_raises():
    """所有路由全部返回空 → 最终抛 EmptyStructuredResponseError。"""
    ctx = ResilienceContext(
        same_route_extra_retries=1,
        per_bucket=DEFAULT_RESILIENCE["per_bucket"],
    )
    sem_empty = _SemWithEmptyHook("scenes is empty")
    _routes = [("a", "provider"), ("b", "provider")]

    async def orig_invoke(route, mc):
        return {"structured_response": sem_empty, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig_invoke,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )
    with pytest.raises(EmptyStructuredResponseError):
        await execute_with_resilience(
            wrapped,
            routes=_routes,
            route_model_configs=_mcs_for_routes(_routes),
            context=ctx,
        )


@pytest.mark.asyncio
async def test_empty_response_per_bucket_override_zero_retry():
    """per_bucket.empty_response.same_route_extra_retries=0：empty 直接进下一条路由。"""
    ctx = ResilienceContext(
        same_route_extra_retries=2,
        per_bucket={ErrorBucket.EMPTY_RESPONSE: {"same_route_extra_retries": 0}},
    )
    sem_empty = _SemWithEmptyHook("scenes is empty")
    sem_good = _SemWithEmptyHook(None)
    seen: list[tuple[str, str | None]] = []
    _routes = [("a", "provider"), ("b", "provider")]

    async def orig_invoke(route, mc):
        seen.append(route)
        if route == ("a", "provider"):
            return {"structured_response": sem_empty, "messages": []}
        return {"structured_response": sem_good, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig_invoke,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )
    out = await execute_with_resilience(
        wrapped,
        routes=_routes,
        route_model_configs=_mcs_for_routes(_routes),
        context=ctx,
    )
    assert out["structured_response"] is sem_good
    # a × 1（不重试）+ b × 1
    assert seen == [("a", "provider"), ("b", "provider")]


@pytest.mark.asyncio
async def test_empty_response_per_bucket_override_two_retries():
    ctx = ResilienceContext(
        same_route_extra_retries=1,
        per_bucket={ErrorBucket.EMPTY_RESPONSE: {"same_route_extra_retries": 2}},
    )
    sem_empty = _SemWithEmptyHook("scenes is empty")
    sem_good = _SemWithEmptyHook(None)
    n = {"c": 0}
    _routes = [("a", "provider")]

    async def orig_invoke(route, mc):
        n["c"] += 1
        if n["c"] <= 2:
            return {"structured_response": sem_empty, "messages": []}
        return {"structured_response": sem_good, "messages": []}

    wrapped = _wrap_invoke_with_result_check(
        orig_invoke,
        kind=StructuredResilienceKind.CREATE_AGENT,
        include_raw=True,
        result_validator=None,
        builtin_empty_check=True,
        log_context=None,
    )
    out = await execute_with_resilience(
        wrapped,
        routes=_routes,
        route_model_configs=_mcs_for_routes(_routes),
        context=ctx,
    )
    assert out["structured_response"] is sem_good
    assert n["c"] == 3  # init + 2 retries
