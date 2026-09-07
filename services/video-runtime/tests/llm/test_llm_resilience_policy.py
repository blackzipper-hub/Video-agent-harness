"""
验证 llm_resilience：异常分桶、配置合并、与 PROMPTS_CONFIG 条目的衔接、执行器骨架。

对应: docs/llm-and-tool-resilience.md
运行:
  cd Cuti-VideoAgent && poetry run pytest tests/llm/test_llm_resilience_policy.py -v
"""
from __future__ import annotations

import pytest

from app.services.agent.utils.llm_resilience import (
    DEFAULT_RESILIENCE,
    ErrorBucket,
    ResilienceContext,
    build_linear_structured_routes,
    build_resilience_bundle_from_prompt_entry,
    build_route_model_configs_list,
    classify_llm_exception,
    effective_same_route_retries,
    execute_with_resilience,
    expand_structured_routes,
    merge_resilience_config,
    resolve_structured_output_strategy,
    should_retry_same_route,
)


def test_classify_structured_output_validation():
    from langchain.agents.structured_output import StructuredOutputValidationError
    from langchain_core.messages import AIMessage

    src = ValueError("json")
    exc = StructuredOutputValidationError("schema", src, AIMessage(content=""))
    assert classify_llm_exception(exc) == ErrorBucket.STRUCTURED_PARSE


def test_classify_timeout():
    assert classify_llm_exception(TimeoutError("x")) == ErrorBucket.TRANSIENT


def test_classify_bad_request():
    try:
        from openai import BadRequestError
    except ImportError:
        pytest.skip("openai not installed")
    import httpx

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(400, request=req, json={"error": {"message": "bad"}})
    exc = BadRequestError("bad", response=resp, body=None)
    assert classify_llm_exception(exc) == ErrorBucket.BAD_REQUEST


def test_merge_resilience_per_bucket_deep():
    base = {
        "same_route_extra_retries": 1,
        "per_bucket": {"bad_request": {"same_route_extra_retries": 0}},
    }
    ov = {"per_bucket": {"structured_parse": {"same_route_extra_retries": 2}}}
    m = merge_resilience_config(base, ov)
    assert m["same_route_extra_retries"] == 1
    assert m["per_bucket"]["bad_request"]["same_route_extra_retries"] == 0
    assert m["per_bucket"]["structured_parse"]["same_route_extra_retries"] == 2


def test_expand_structured_routes_default_order():
    r = expand_structured_routes(
        primary_model="gemini-2.5-flash",
        fallback_model="gpt-4.1-mini",
        strategy_order=("provider", "tool"),
    )
    assert r == [
        ("gemini-2.5-flash", "provider"),
        ("gemini-2.5-flash", "tool"),
        ("gpt-4.1-mini", "provider"),
    ]


def test_expand_structured_routes_no_fallback_model():
    r = expand_structured_routes(
        primary_model="gpt-4.1-mini",
        fallback_model=None,
        strategy_order=("provider", "tool"),
    )
    assert r == [("gpt-4.1-mini", "provider"), ("gpt-4.1-mini", "tool")]


def test_should_retry_same_route_respects_per_bucket():
    # per_bucket=None：沿用传入的 base extra_retries（此处为 1）
    assert should_retry_same_route(ErrorBucket.BAD_REQUEST, 1, 0, None) is True
    assert should_retry_same_route(ErrorBucket.BAD_REQUEST, 1, 1, None) is False
    pb = {"bad_request": {"same_route_extra_retries": 0}}
    assert should_retry_same_route(ErrorBucket.BAD_REQUEST, 1, 0, pb) is False


def test_build_route_model_configs_prepend_when_chain_first_differs_from_primary():
    primary_mc = {"model": "gpt-4.1-mini", "timeout": 180}
    merged = merge_resilience_config(
        DEFAULT_RESILIENCE,
        {"model_fallback_chain": [{"model": "gemini-2.5-flash", "max_output_tokens": 4096}]},
    )
    mcs = build_route_model_configs_list(primary_mc, merged)
    assert len(mcs) == 2
    assert mcs[0] == primary_mc
    assert mcs[1]["model"] == "gemini-2.5-flash"
    assert mcs[1]["max_output_tokens"] == 4096
    assert mcs[1]["timeout"] == 180


def test_build_route_model_configs_no_prepend_when_chain_first_matches_primary():
    primary_mc = {"model": "gpt-4.1-mini", "timeout": 180}
    merged = merge_resilience_config(
        DEFAULT_RESILIENCE,
        {
            "model_fallback_chain": [
                {"model": "gpt-4.1-mini"},
                {"model": "gemini-2.5-flash", "timeout": 240},
            ]
        },
    )
    mcs = build_route_model_configs_list(primary_mc, merged)
    assert len(mcs) == 2
    assert mcs[1]["model"] == "gemini-2.5-flash"


def test_build_route_model_configs_empty_chain_is_primary_only():
    assert build_route_model_configs_list({"model": "gpt-4.1-mini"}, {}) == [
        {"model": "gpt-4.1-mini"}
    ]


def test_build_linear_structured_routes_respects_max_fallback_steps():
    merged = merge_resilience_config(
        DEFAULT_RESILIENCE,
        {
            "max_fallback_steps": 1,
            "model_fallback_chain": [
                {"model": "gpt-4.1-mini"},
                {"model": "gemini-2.5-flash"},
                {"model": "gpt-5-nano"},
            ],
        },
    )
    primary_mc = {"model": "gpt-4.1-mini", "timeout": 180}
    r = build_linear_structured_routes(primary_mc=primary_mc, merged=merged)
    assert r == [
        ("gpt-4.1-mini", "provider"),
        ("gemini-2.5-flash", "provider"),
    ]


def test_resolve_structured_output_strategy_explicit_key():
    m = merge_resilience_config(DEFAULT_RESILIENCE, {"structured_output_strategy": "tool"})
    assert resolve_structured_output_strategy(m) == "tool"


def test_resolve_structured_output_strategy_order_first_only():
    m = merge_resilience_config(
        DEFAULT_RESILIENCE, {"structured_strategy_order": ("tool", "provider")}
    )
    assert resolve_structured_output_strategy(m) == "tool"


def test_resilience_context_narrowed():
    ctx = ResilienceContext(same_route_extra_retries=2, max_fallback_steps=1)
    n = ctx.narrowed(max_fallback_steps=0, same_route_extra_retries=0)
    assert n.max_fallback_steps == 0
    assert n.same_route_extra_retries == 0
    assert ctx.max_fallback_steps == 1


def test_effective_same_route_retries_per_bucket():
    merged = merge_resilience_config(
        DEFAULT_RESILIENCE,
        {"per_bucket": {ErrorBucket.BAD_REQUEST: {"same_route_extra_retries": 0}}},
    )
    assert effective_same_route_retries(merged, ErrorBucket.TRANSIENT) == 1
    assert effective_same_route_retries(merged, ErrorBucket.BAD_REQUEST) == 0


def test_effective_same_route_retries_bad_request_default_one():
    merged = merge_resilience_config(DEFAULT_RESILIENCE, {})
    assert effective_same_route_retries(merged, ErrorBucket.BAD_REQUEST) == 1


@pytest.mark.asyncio
async def test_execute_with_resilience_success_first_try():
    _routes = [("m", "provider")]
    _mcs = [{"model": "m", "timeout": 60}]

    async def invoke(route, mc):
        assert route == ("m", "provider")
        assert mc["model"] == "m"
        return "ok"

    ctx = ResilienceContext(same_route_extra_retries=1)
    out = await execute_with_resilience(
        invoke, routes=_routes, route_model_configs=_mcs, context=ctx
    )
    assert out == "ok"


@pytest.mark.asyncio
async def test_execute_with_resilience_same_route_retry_then_ok():
    n = {"calls": 0}
    _routes = [("m", "tool")]
    _mcs = [{"model": "m", "timeout": 60}]

    async def invoke(route, mc):
        n["calls"] += 1
        if n["calls"] == 1:
            raise TimeoutError("transient")
        assert route == ("m", "tool")
        return "ok"

    ctx = ResilienceContext(same_route_extra_retries=1)
    out = await execute_with_resilience(
        invoke, routes=_routes, route_model_configs=_mcs, context=ctx
    )
    assert out == "ok"
    assert n["calls"] == 2


@pytest.mark.asyncio
async def test_execute_with_resilience_advances_to_next_route():
    _routes = [("a", "provider"), ("a", "tool")]
    _mcs = [{"model": "a", "timeout": 60}, {"model": "a", "timeout": 60}]

    async def invoke(route, mc):
        if route == ("a", "provider"):
            raise TimeoutError("x")
        if route == ("a", "tool"):
            return "from_tool"
        raise AssertionError(route)

    ctx = ResilienceContext(same_route_extra_retries=0)
    out = await execute_with_resilience(
        invoke,
        routes=_routes,
        route_model_configs=_mcs,
        context=ctx,
    )
    assert out == "from_tool"


@pytest.mark.asyncio
async def test_execute_with_resilience_auth_raises_immediately():
    try:
        from openai import AuthenticationError
    except ImportError:
        pytest.skip("openai not installed")
    import httpx

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(401, request=req)
    exc = AuthenticationError("nope", response=resp, body=None)
    _routes = [("a", "provider"), ("b", "provider")]
    _mcs = [{"model": "a", "timeout": 60}, {"model": "b", "timeout": 60}]

    async def invoke(route, mc):
        raise exc

    ctx = ResilienceContext(same_route_extra_retries=2)
    with pytest.raises(AuthenticationError):
        await execute_with_resilience(
            invoke,
            routes=_routes,
            route_model_configs=_mcs,
            context=ctx,
        )


def test_build_bundle_resolves_role_only_model_config(monkeypatch):
    """PROMPTS_CONFIG 只写 role 时，bundle 必须解析出具体 model（否则线上缺 model 崩）。"""
    monkeypatch.setenv("LLM_QUALITY", "best")
    from prompts.llm_model_profiles import resolve_role_model
    from prompts.prompt_config import PROMPTS_CONFIG, PromptName

    entry = dict(PROMPTS_CONFIG[PromptName.VIDEO_AUDIO_TRANSCRIPTION])
    assert "model" not in (entry.get("model_config") or {})
    assert (entry.get("model_config") or {}).get("role") == "multimodal"
    _ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    expected = resolve_role_model("multimodal")
    assert routes[0][0] == expected
    assert route_mcs[0]["model"] == expected


def test_build_bundle_resolves_role_only_fallback_chain(monkeypatch):
    """model_fallback_chain 只写 role 时也必须解析。"""
    monkeypatch.setenv("LLM_QUALITY", "best")
    from prompts.llm_model_profiles import resolve_role_model

    entry = {
        "schema": None,
        "model_config": {"role": "multimodal", "timeout": 120},
        "resilience": {
            "model_fallback_chain": [
                {"role": "multimodal", "timeout": 120},
                {"role": "tool", "temperature": 0.5, "timeout": 180},
            ],
            "same_route_extra_retries": 2,
        },
    }
    chain = (entry.get("resilience") or {}).get("model_fallback_chain") or []
    assert chain and all("model" not in c for c in chain)
    _ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    assert routes
    assert all(r[0] for r in routes)
    assert route_mcs[0]["model"] == resolve_role_model("multimodal")
    assert any(mc["model"] == resolve_role_model("tool") for mc in route_mcs)


def test_build_route_model_configs_role_fragment_overrides_primary(monkeypatch):
    monkeypatch.setenv("LLM_QUALITY", "best")
    from prompts.llm_model_profiles import resolve_role_model

    primary_mc = {"role": "multimodal", "timeout": 120}
    merged = merge_resilience_config(
        DEFAULT_RESILIENCE,
        {
            "max_fallback_steps": 2,
            "model_fallback_chain": [
                {"role": "multimodal", "timeout": 120},
                {"role": "tool", "temperature": 0.5, "timeout": 180},
            ],
        },
    )
    mcs = build_route_model_configs_list(primary_mc, merged)
    assert mcs[0]["model"] == resolve_role_model("multimodal")
    assert mcs[1]["model"] == resolve_role_model("tool")
    assert mcs[1]["timeout"] == 180


def test_build_bundle_from_real_prompts_config_consistency_check():
    from prompts.llm_model_profiles import resolve_model_config
    from prompts.prompt_config import PROMPTS_CONFIG, PromptName

    entry = PROMPTS_CONFIG[PromptName.VIDEO_CONSISTENCY_CHECK]
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
    primary = resolve_model_config(entry["model_config"])["model"]
    assert primary in (r[0] for r in routes)
    assert all(s in ("provider", "tool", None) for _, s in routes)
    assert ctx.structured_strategy_order


def test_build_bundle_structured_single_route():
    from prompts.llm_model_profiles import resolve_model_config
    from prompts.prompt_config import PROMPTS_CONFIG, PromptName

    entry = dict(PROMPTS_CONFIG[PromptName.VIDEO_AUDIO_TRANSCRIPTION])
    ctx, routes, _mcs = build_resilience_bundle_from_prompt_entry(entry)
    assert routes
    assert routes[0][0] == resolve_model_config(entry["model_config"])["model"]
