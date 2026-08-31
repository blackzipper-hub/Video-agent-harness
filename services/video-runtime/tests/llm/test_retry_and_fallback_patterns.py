"""
验证 LangChain Runnable 的 with_retry / with_fallbacks 组合行为（cuti-video-local）。

不修改业务代码；用于方案设计前确认语义与顺序。
运行:
  cd Cuti-VideoAgent && conda run -n cuti-video-local python -m pytest tests/llm/test_retry_and_fallback_patterns.py -v
"""
import pytest


def test_with_retry_eventually_succeeds():
    """同一 runnable 在异常时按次数重试，成功后返回。"""
    from tenacity import RetryError

    calls = {"n": 0}

    def flaky(x: str) -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return x + "-ok"

    from langchain_core.runnables import RunnableLambda

    r = RunnableLambda(flaky).with_retry(
        stop_after_attempt=5,
        retry_if_exception_type=(ValueError,),
        wait_exponential_jitter=False,
    )
    assert r.invoke("a") == "a-ok"
    assert calls["n"] == 3


def test_with_retry_exhausted_raises():
    """重试用尽后抛出最后一次异常。"""
    from langchain_core.runnables import RunnableLambda

    def always_fail(x: str):
        raise RuntimeError("always")

    r = RunnableLambda(always_fail).with_retry(
        stop_after_attempt=2,
        retry_if_exception_type=(RuntimeError,),
        wait_exponential_jitter=False,
    )
    with pytest.raises(RuntimeError, match="always"):
        r.invoke("x")


def test_with_fallbacks_primary_fails_secondary_ok():
    """主 runnable 抛错时落到 fallback 列表中的下一个。"""
    from langchain_core.runnables import RunnableLambda

    def primary(x: str):
        raise ConnectionError("primary down")

    def backup(x: str):
        return f"backup:{x}"

    chain = RunnableLambda(primary).with_fallbacks([RunnableLambda(backup)])
    assert chain.invoke("hi") == "backup:hi"


def test_fallbacks_order_first_success_wins():
    """with_fallbacks 按顺序尝试：第一个成功则不再试后面。"""
    from langchain_core.runnables import RunnableLambda

    log = []

    def ok1(x):
        log.append("1")
        return "one"

    def ok2(x):
        log.append("2")
        return "two"

    chain = RunnableLambda(ok1).with_fallbacks([RunnableLambda(ok2)])
    assert chain.invoke("x") == "one"
    assert log == ["1"]


def test_retry_then_fallback_composition():
    """先对主模型包 retry，再对整体包 fallbacks：主 retry 耗尽后才试备用模型。"""
    from langchain_core.runnables import RunnableLambda

    calls_primary = {"n": 0}

    def primary(x: str):
        calls_primary["n"] += 1
        raise ValueError("bad")

    def secondary(x: str):
        return "secondary"

    primary_retried = RunnableLambda(primary).with_retry(
        stop_after_attempt=2,
        retry_if_exception_type=(ValueError,),
        wait_exponential_jitter=False,
    )
    chain = primary_retried.with_fallbacks([RunnableLambda(secondary)])
    assert chain.invoke("q") == "secondary"
    assert calls_primary["n"] == 2  # 2 次尝试后放弃主链路


@pytest.mark.asyncio
async def test_ainvoke_retry_async():
    from langchain_core.runnables import RunnableLambda

    n = {"c": 0}

    async def af(x: str):
        n["c"] += 1
        if n["c"] < 2:
            raise TimeoutError("t")
        return x

    r = RunnableLambda(af).with_retry(
        stop_after_attempt=4,
        retry_if_exception_type=(TimeoutError,),
        wait_exponential_jitter=False,
    )
    out = await r.ainvoke("z")
    assert out == "z"
    assert n["c"] == 2


def _bad_request_error():
    """构造可实例化的 openai.BadRequestError（需合法 httpx.Response）。"""
    import httpx
    from openai import BadRequestError

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(400, request=req, json={"error": {"message": "bad"}})
    return BadRequestError("bad", response=resp, body=None)


def test_openai_bad_request_not_retried_by_default_without_matching_type():
    """若 retry_if_exception_type 不含 BadRequestError，则不会重试（需显式加入类型）。"""
    try:
        from openai import BadRequestError
    except ImportError:
        pytest.skip("openai not installed")

    from langchain_core.runnables import RunnableLambda

    def boom(x):
        raise _bad_request_error()

    r = RunnableLambda(boom).with_retry(
        stop_after_attempt=3,
        retry_if_exception_type=(TimeoutError,),  # 不匹配
        wait_exponential_jitter=False,
    )
    with pytest.raises(BadRequestError):
        r.invoke("x")


def test_openai_bad_request_retried_when_type_included():
    """把 BadRequestError 加入 retry 类型后，同一模型会重试（是否业务上要这么做另议）。"""
    try:
        from openai import BadRequestError
    except ImportError:
        pytest.skip("openai not installed")

    from langchain_core.runnables import RunnableLambda

    n = {"c": 0}

    def maybe_400(x):
        n["c"] += 1
        if n["c"] < 2:
            raise _bad_request_error()
        return "fixed"

    r = RunnableLambda(maybe_400).with_retry(
        stop_after_attempt=3,
        retry_if_exception_type=(BadRequestError,),
        wait_exponential_jitter=False,
    )
    assert r.invoke("x") == "fixed"
    assert n["c"] == 2
