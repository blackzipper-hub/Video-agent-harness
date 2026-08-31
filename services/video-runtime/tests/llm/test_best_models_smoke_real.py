"""
Smoke-test: 用现有 create_llm / structured_output 路径验证最好档模型能否跑通。

运行:
  conda activate cuti-video-local
  cd cuti-video-agent/services/agent
  python -m pytest tests/llm/test_best_models_smoke_real.py -v -s -m integration

注意: app.config.load_dotenv() 可能从上层 .env 注入 host.docker.internal:7890；
宿主机跑测时每次 create_llm 后需清掉，否则易 Connection/ServerDisconnected。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _clear_docker_host_proxy() -> None:
    for k in list(os.environ):
        if "proxy" not in k.lower():
            continue
        val = os.environ.get(k) or ""
        if "host.docker.internal" in val or ":7890" in val:
            os.environ.pop(k, None)


class _SmokeEcho(BaseModel):
    ok: bool = Field(description="是否理解指令")
    answer: str = Field(description="一句话回答")


_OPENAI_SMOKE = [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.4-mini",
    "gpt-5-mini",
]
_GEMINI_SMOKE = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
]


@pytest.fixture(scope="module")
def _env(dev_env_loaded):
    _clear_docker_host_proxy()
    return {
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "google": bool(os.getenv("GOOGLE_API_KEY")),
    }


def _cfg(model: str, **extra):
    base = {"model": model, "timeout": 120, "max_tokens": 512, "max_output_tokens": 512}
    base.update(extra)
    return base


def _make_llm(model_config: dict):
    # 先触发 app.config.load_dotenv（可能注入 docker 代理），再清掉，再建 client
    import app.models  # noqa: F401
    from prompts.prompt_config import create_llm

    _clear_docker_host_proxy()
    return create_llm(model_config)


def _content_text(out) -> str:
    text = getattr(out, "content", "") or ""
    if isinstance(text, list):
        text = "".join(
            (p.get("text") if isinstance(p, dict) else str(p)) for p in text
        )
    return text if isinstance(text, str) else str(text)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", _OPENAI_SMOKE)
async def test_openai_plain_chat(dev_env_loaded, _env, model_id):
    if not _env["openai"]:
        pytest.skip("OPENAI_API_KEY")
    from langchain_core.messages import HumanMessage

    llm = _make_llm(_cfg(model_id))
    out = await llm.ainvoke([HumanMessage(content="Reply with exactly: PONG")])
    text = _content_text(out)
    print(f"\n[plain] {model_id} → {text!r}")
    assert len(text) > 0


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", _OPENAI_SMOKE)
async def test_openai_structured(dev_env_loaded, _env, model_id):
    if not _env["openai"]:
        pytest.skip("OPENAI_API_KEY")
    from langchain_core.messages import HumanMessage

    llm = _make_llm(_cfg(model_id))
    structured = llm.with_structured_output(_SmokeEcho)
    result = await structured.ainvoke(
        [HumanMessage(content="Confirm you received this. Set ok=true, answer=ready.")]
    )
    print(f"\n[structured] {model_id} → {result}")
    assert isinstance(result, _SmokeEcho)
    assert result.ok is True
    assert len(result.answer) > 0


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", _GEMINI_SMOKE)
async def test_gemini_plain_chat(dev_env_loaded, _env, model_id):
    if not _env["google"]:
        pytest.skip("GOOGLE_API_KEY")
    from langchain_core.messages import HumanMessage

    llm = _make_llm(_cfg(model_id, temperature=0.2))
    out = await llm.ainvoke([HumanMessage(content="Reply with exactly: PONG")])
    text = _content_text(out)
    print(f"\n[plain] {model_id} → {text!r}")
    assert len(text) > 0


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", _GEMINI_SMOKE)
async def test_gemini_structured(dev_env_loaded, _env, model_id):
    if not _env["google"]:
        pytest.skip("GOOGLE_API_KEY")
    from langchain_core.messages import HumanMessage

    llm = _make_llm(_cfg(model_id, temperature=0.2))
    method = "json_schema" if "gemini-3" in model_id else None
    kwargs = {"method": method} if method else {}
    structured = llm.with_structured_output(_SmokeEcho, **kwargs)
    result = await structured.ainvoke(
        [HumanMessage(content="Confirm you received this. Set ok=true, answer=ready.")]
    )
    print(f"\n[structured] {model_id} → {result}")
    assert isinstance(result, _SmokeEcho)
    assert result.ok is True


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"])
async def test_openai_bind_tools(dev_env_loaded, _env, model_id):
    """GPT-5.6 默认走 Responses，function tools + 默认 reasoning 应可用。"""
    if not _env["openai"]:
        pytest.skip("OPENAI_API_KEY")
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool

    @tool
    def ping(x: str) -> str:
        """Echo a short string."""
        return f"pong:{x}"

    llm = _make_llm(_cfg(model_id))
    assert llm.use_responses_api is True
    bound = llm.bind_tools([ping])
    out = await bound.ainvoke(
        [HumanMessage(content="Call the ping tool with x=hi. Prefer a tool call over plain text.")]
    )
    tcs = getattr(out, "tool_calls", None) or []
    print(f"\n[bind_tools] {model_id} → tool_calls={tcs!r}")
    assert tcs, f"{model_id} returned no tool_calls (got content={getattr(out, 'content', None)!r})"
    assert tcs[0].get("name") == "ping"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", ["gpt-5.6-terra", "gpt-5.6-sol"])
async def test_openai_astream_responses_chunks_to_text(dev_env_loaded, _env, model_id):
    """回归 [object Object]：Responses astream 的 content 可能是 list[dict]，必须能抽成 str 并 join。"""
    if not _env["openai"]:
        pytest.skip("OPENAI_API_KEY")
    from langchain_core.messages import HumanMessage

    from app.chat.services.agent.agent_router_service import (
        AgentRouterService,
        _stream_chunk_to_text,
    )

    llm = _make_llm(_cfg(model_id))
    assert llm.use_responses_api is True

    raw_types: list[str] = []
    text_parts: list[str] = []

    async def _tokens():
        async for chunk in llm.astream(
            [HumanMessage(content="Reply with exactly three words: hello from terra")]
        ):
            raw = chunk.content if hasattr(chunk, "content") else chunk
            raw_types.append(type(raw).__name__)
            text = _stream_chunk_to_text(raw)
            if text:
                text_parts.append(text)
                yield text

    svc = object.__new__(AgentRouterService)
    full, _, blocked, _ = await AgentRouterService._stream_and_emit_with_output_rail(
        svc,
        _tokens(),
        target_event="chat_response",
        conversation_id=None,
        run_id=None,
        state=None,
        send_event_func=None,
    )
    print(f"\n[astream] {model_id} raw_types={raw_types} full={full!r}")
    assert isinstance(full, str)
    assert len(full) > 0
    assert blocked is False
    assert "[object Object]" not in full
    # 允许出现 list（Responses blocks）；关键是 to_text + rail 后仍是可读 str
    assert all(isinstance(p, str) for p in text_parts)


@pytest.mark.integration
def test_create_llm_gpt56_defaults_responses_api(dev_env_loaded):
    llm = _make_llm(_cfg("gpt-5.6-terra"))
    assert llm.use_responses_api is True
    # 官方路径保留默认 reasoning，不强制 none
    assert llm.reasoning_effort is None

    llm_chat = _make_llm(_cfg("gpt-5.6-terra", use_responses_api=False))
    assert llm_chat.use_responses_api is False
    assert llm_chat.reasoning_effort == "none"

    llm_override = _make_llm(_cfg("gpt-5.6-terra", reasoning_effort="low"))
    assert llm_override.use_responses_api is True
    assert llm_override.reasoning_effort == "low"

    # 其他档位模型不受 GPT-5.6 默认影响
    mini = _make_llm(_cfg("gpt-5-mini"))
    assert mini.use_responses_api is not True
    assert mini.reasoning_effort is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_role_profile_best_resolves(dev_env_loaded, monkeypatch):
    monkeypatch.setenv("LLM_QUALITY", "best")
    from prompts.llm_model_profiles import resolve_model_config

    tool_mc = resolve_model_config({"role": "tool", "timeout": 60})
    text_mc = resolve_model_config({"role": "text", "timeout": 60})
    mm_mc = resolve_model_config({"role": "multimodal", "timeout": 60})
    assert tool_mc["model"] == "gpt-5.6-terra"
    assert text_mc["model"] == "gpt-5.6-sol"
    assert mm_mc["model"] == "gemini-3.1-pro-preview"
    tool_llm = _make_llm(tool_mc)
    text_llm = _make_llm(text_mc)
    assert tool_llm.use_responses_api is True
    assert text_llm.use_responses_api is True
    _make_llm(mm_mc)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.requires_google
async def test_gemini_31_pro_image_data_url(dev_env_loaded, _env):
    if not _env["google"]:
        pytest.skip("GOOGLE_API_KEY")
    import base64

    from langchain_core.messages import HumanMessage

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    data_url = "data:image/png;base64," + base64.b64encode(png).decode()
    llm = _make_llm(_cfg("gemini-3.1-pro-preview", temperature=0.2, max_output_tokens=256))
    msg = HumanMessage(
        content=[
            {"type": "text", "text": "What color is this pixel roughly? One word."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
    )
    out = await llm.ainvoke([msg])
    text = _content_text(out)
    print(f"\n[vision-image] gemini-3.1-pro-preview → {text!r}")
    assert len(text) > 0
