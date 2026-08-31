"""
``execute_with_resilience`` + 模拟异常：覆盖分桶、同路由重试、换路、401 直抛，无需真实 LLM。

运行:
  cd Cuti-VideoAgent && poetry run pytest tests/llm/test_llm_resilience_error_simulation.py -v -m unit
"""
from __future__ import annotations

import pytest
from langchain.agents.structured_output import StructuredOutputValidationError
from langchain_core.messages import AIMessage

from app.services.agent.utils.llm_resilience import (
    DEFAULT_RESILIENCE,
    ErrorBucket,
    ResilienceContext,
    execute_with_resilience,
    merge_resilience_config,
    resilience_context_from_merged,
)


def _bad_request_exc():
    try:
        from openai import BadRequestError
    except ImportError:
        pytest.skip("openai not installed")
    import httpx

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(400, request=req, json={"error": {"message": "bad"}})
    return BadRequestError("bad", response=resp, body=None)


def _rate_limit_exc():
    try:
        from openai import RateLimitError
    except ImportError:
        pytest.skip("openai not installed")
    import httpx

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(429, request=req, json={"error": {"message": "rl"}})
    return RateLimitError("rl", response=resp, body=None)


def _server_exc():
    try:
        from openai import InternalServerError
    except ImportError:
        pytest.skip("openai not installed")
    import httpx

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(500, request=req, json={"error": {"message": "srv"}})
    return InternalServerError("srv", response=resp, body=None)


def _auth_exc():
    try:
        from openai import AuthenticationError
    except ImportError:
        pytest.skip("openai not installed")
    import httpx

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(401, request=req)
    return AuthenticationError("auth", response=resp, body=None)


def _structured_validation_exc():
    return StructuredOutputValidationError("s", ValueError("x"), AIMessage(content=""))


def _mcs_for_routes(routes):
    return [{"model": r[0], "timeout": 180} for r in routes]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_empty_routes_runtime_error():
    ctx = ResilienceContext()

    async def invoke_fn(route, mc):
        raise AssertionError((route, mc))

    with pytest.raises(RuntimeError, match="empty routes"):
        await execute_with_resilience(
            invoke_fn, routes=[], route_model_configs=[], context=ctx
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_transient_one_retry_then_success():
    ctx = ResilienceContext(same_route_extra_retries=1)
    n = {"c": 0}
    _routes = [("a", None)]

    async def invoke_fn(route, mc):
        n["c"] += 1
        if n["c"] == 1:
            raise TimeoutError("t")
        assert route == ("a", None)
        return "ok"

    out = await execute_with_resilience(
        invoke_fn, routes=_routes, route_model_configs=_mcs_for_routes(_routes), context=ctx
    )
    assert out == "ok"
    assert n["c"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_bad_request_default_one_retry_then_success():
    merged = merge_resilience_config(DEFAULT_RESILIENCE, {})
    ctx = resilience_context_from_merged(merged)
    n = {"c": 0}
    _routes = [("m", "provider")]

    async def invoke_fn(route, mc):
        n["c"] += 1
        if n["c"] == 1:
            raise _bad_request_exc()
        assert route == ("m", "provider")
        return "ok"

    out = await execute_with_resilience(
        invoke_fn, routes=_routes, route_model_configs=_mcs_for_routes(_routes), context=ctx
    )
    assert out == "ok"
    assert n["c"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_bad_request_exhaust_one_retry_then_fallback_route():
    merged = merge_resilience_config(DEFAULT_RESILIENCE, {})
    ctx = resilience_context_from_merged(merged)
    seen: list[tuple[str, str | None]] = []
    n = {"c": 0}
    _routes = [("g", "provider"), ("gem", "provider")]

    async def invoke_fn(route, mc):
        n["c"] += 1
        seen.append(route)
        if route == ("g", "provider"):
            raise _bad_request_exc()
        if route == ("gem", "provider"):
            return "fallback"
        raise AssertionError(route)

    out = await execute_with_resilience(
        invoke_fn,
        routes=_routes,
        route_model_configs=_mcs_for_routes(_routes),
        context=ctx,
    )
    assert out == "fallback"
    assert n["c"] == 3
    assert seen == [("g", "provider"), ("g", "provider"), ("gem", "provider")]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_bad_request_no_same_route_retry_goes_to_next_route():
    ctx = ResilienceContext(
        same_route_extra_retries=1,
        per_bucket={ErrorBucket.BAD_REQUEST: {"same_route_extra_retries": 0}},
    )
    seen: list[tuple[str, str | None]] = []
    _routes = [("r1", "provider"), ("r2", "tool")]

    async def invoke_fn(route, mc):
        seen.append(route)
        if route == ("r1", "provider"):
            raise _bad_request_exc()
        if route == ("r2", "tool"):
            return "second"
        raise AssertionError(route)

    out = await execute_with_resilience(
        invoke_fn,
        routes=_routes,
        route_model_configs=_mcs_for_routes(_routes),
        context=ctx,
    )
    assert out == "second"
    assert seen == [("r1", "provider"), ("r2", "tool")]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_structured_parse_exhausts_retry_then_next_route():
    ctx = ResilienceContext(same_route_extra_retries=1)
    n = {"r1": 0}
    _routes = [("m", "provider"), ("m", "tool")]

    async def invoke_fn(route, mc):
        if route == ("m", "provider"):
            n["r1"] += 1
            raise _structured_validation_exc()
        if route == ("m", "tool"):
            return "tool_ok"
        raise AssertionError(route)

    out = await execute_with_resilience(
        invoke_fn,
        routes=_routes,
        route_model_configs=_mcs_for_routes(_routes),
        context=ctx,
    )
    assert out == "tool_ok"
    assert n["r1"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_rate_limit_retry_then_success():
    ctx = ResilienceContext(same_route_extra_retries=1)
    n = {"c": 0}
    _routes = [("m", None)]

    async def invoke_fn(route, mc):
        n["c"] += 1
        if n["c"] == 1:
            raise _rate_limit_exc()
        return "rl_ok"

    out = await execute_with_resilience(
        invoke_fn,
        routes=_routes,
        route_model_configs=_mcs_for_routes(_routes),
        context=ctx,
    )
    assert out == "rl_ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_server_error_retry_then_success():
    ctx = ResilienceContext(same_route_extra_retries=1)
    n = {"c": 0}
    _routes = [("m", None)]

    async def invoke_fn(route, mc):
        n["c"] += 1
        if n["c"] == 1:
            raise _server_exc()
        return "srv_ok"

    out = await execute_with_resilience(
        invoke_fn,
        routes=_routes,
        route_model_configs=_mcs_for_routes(_routes),
        context=ctx,
    )
    assert out == "srv_ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_unknown_bucket_uses_base_extra_retry():
    ctx = ResilienceContext(same_route_extra_retries=1)
    n = {"c": 0}
    _routes = [("m", None)]

    async def invoke_fn(route, mc):
        n["c"] += 1
        if n["c"] == 1:
            raise ValueError("unknown-ish")
        return "unk_ok"

    out = await execute_with_resilience(
        invoke_fn,
        routes=_routes,
        route_model_configs=_mcs_for_routes(_routes),
        context=ctx,
    )
    assert out == "unk_ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_auth_raises_no_further_route():
    try:
        from openai import AuthenticationError
    except ImportError:
        pytest.skip("openai not installed")

    ctx = ResilienceContext(same_route_extra_retries=2)
    exc = _auth_exc()
    _routes = [("a", None), ("b", None)]

    async def invoke_fn(route, mc):
        raise exc

    with pytest.raises(AuthenticationError):
        await execute_with_resilience(
            invoke_fn,
            routes=_routes,
            route_model_configs=_mcs_for_routes(_routes),
            context=ctx,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_all_routes_fail_raises_last_timeout():
    ctx = ResilienceContext(same_route_extra_retries=0)
    _routes = [("a", None), ("b", None)]

    async def invoke_fn(route, mc):
        raise TimeoutError("always")

    with pytest.raises(TimeoutError, match="always"):
        await execute_with_resilience(
            invoke_fn,
            routes=_routes,
            route_model_configs=_mcs_for_routes(_routes),
            context=ctx,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_zero_extra_retries_single_attempt_per_route():
    ctx = ResilienceContext(same_route_extra_retries=0)
    seen: list[str] = []
    _routes = [("x", None), ("y", None)]

    async def invoke_fn(route, mc):
        seen.append(route[0])
        raise TimeoutError("x")

    with pytest.raises(TimeoutError):
        await execute_with_resilience(
            invoke_fn,
            routes=_routes,
            route_model_configs=_mcs_for_routes(_routes),
            context=ctx,
        )
    assert seen == ["x", "y"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_narrowed_context_zero_retries():
    base = ResilienceContext(same_route_extra_retries=2)
    ctx = base.narrowed(same_route_extra_retries=0)
    n = {"c": 0}
    _routes = [("m", None)]

    async def invoke_fn(route, mc):
        n["c"] += 1
        raise TimeoutError("t")

    with pytest.raises(TimeoutError):
        await execute_with_resilience(
            invoke_fn,
            routes=_routes,
            route_model_configs=_mcs_for_routes(_routes),
            context=ctx,
        )
    assert n["c"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sim_dual_model_three_routes_exhaust_primary_then_fallback_ok():
    """与 ``model_fallback_chain`` 线性路由一致：主模型两档 strategy 均失败后第三档（备用模型）成功。"""
    ctx = ResilienceContext(same_route_extra_retries=1)
    routes = [("gpt-4.1-mini", "provider"), ("gemini-2.5-flash", "provider")]
    n = {"c": 0}

    async def invoke_fn(route, mc):
        n["c"] += 1
        if route[0] == "gpt-4.1-mini":
            raise TimeoutError("skip_primary")
        assert route == ("gemini-2.5-flash", "provider")
        return "fallback_ok"

    out = await execute_with_resilience(
        invoke_fn, routes=routes, route_model_configs=_mcs_for_routes(routes), context=ctx
    )
    assert out == "fallback_ok"
    assert n["c"] == 3
