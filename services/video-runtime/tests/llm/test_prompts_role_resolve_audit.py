"""全量回归：PROMPTS_CONFIG 的 role / model_fallback_chain 都能 resolve 并建成 resilience bundle。

避免再出现：
- prompt entry missing model_config.model
- model_fallback_chain[i] must include 'model'
"""
from __future__ import annotations

import pytest


def _iter_entries(prompts_config):
    for name, entry in prompts_config.items():
        if not isinstance(entry, dict):
            continue
        yield str(getattr(name, "value", name)), entry


@pytest.mark.parametrize("quality", ["best", "common", "economy"])
def test_all_va_prompts_resolve_and_build_bundle(monkeypatch, quality):
    monkeypatch.setenv("LLM_QUALITY", quality)
    from prompts.llm_model_profiles import has_model_or_role, resolve_model_config
    from prompts.prompt_config import PROMPTS_CONFIG
    from app.services.agent.utils.llm_resilience import (
        build_resilience_bundle_from_prompt_entry,
    )

    failures: list[str] = []
    checked = 0
    for name, entry in _iter_entries(PROMPTS_CONFIG):
        mc = entry.get("model_config")
        if not isinstance(mc, dict) or not has_model_or_role(mc):
            continue
        checked += 1
        try:
            resolved = resolve_model_config(mc)
            assert resolved.get("model"), f"{name}: empty model after resolve"
            for i, frag in enumerate((entry.get("resilience") or {}).get("model_fallback_chain") or []):
                if not isinstance(frag, dict):
                    raise AssertionError(f"{name}: fallback[{i}] not dict")
                if not has_model_or_role(frag):
                    raise AssertionError(f"{name}: fallback[{i}] missing model/role")
                assert resolve_model_config(frag).get("model"), (
                    f"{name}: fallback[{i}] empty model after resolve"
                )
            # schema=None → 纯 chat bundle；有 schema → 结构化。两者都要能 resolve 出 model。
            ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(entry)
            assert routes, f"{name}: empty routes"
            assert len(routes) == len(route_mcs)
            assert all(r[0] for r in routes), f"{name}: blank model id in routes={routes}"
            assert all(mc.get("model") for mc in route_mcs), f"{name}: blank route_mcs"
            _ = ctx
        except Exception as exc:  # noqa: BLE001 — 收集后一次报出
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    assert checked > 0
    assert not failures, (
        f"LLM_QUALITY={quality} failures ({len(failures)}/{checked}):\n"
        + "\n".join(failures)
    )


@pytest.mark.parametrize("quality", ["best", "common", "economy"])
def test_all_chat_prompts_resolve_and_build_bundle(monkeypatch, quality):
    monkeypatch.setenv("LLM_QUALITY", quality)
    from prompts.llm_model_profiles import has_model_or_role, resolve_model_config
    from app.chat.prompts.prompt_config import PROMPTS_CONFIG
    from app.chat.services.agent.utils.llm_resilience import (
        build_resilience_bundle_from_prompt_entry,
    )

    failures: list[str] = []
    checked = 0
    for name, entry in _iter_entries(PROMPTS_CONFIG):
        mc = entry.get("model_config")
        if not isinstance(mc, dict) or not has_model_or_role(mc):
            continue
        checked += 1
        try:
            resolved = resolve_model_config(mc)
            assert resolved.get("model"), f"{name}: empty model after resolve"
            ctx, routes = build_resilience_bundle_from_prompt_entry(entry)
            assert routes, f"{name}: empty routes"
            assert all(r[0] for r in routes), f"{name}: blank model id in routes={routes}"
            _ = ctx
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    assert checked > 0
    assert not failures, (
        f"LLM_QUALITY={quality} chat failures ({len(failures)}/{checked}):\n"
        + "\n".join(failures)
    )


def test_chat_parse_fallback_chain_accepts_role_dicts(monkeypatch):
    monkeypatch.setenv("LLM_QUALITY", "best")
    from prompts.llm_model_profiles import resolve_role_model
    from app.chat.services.agent.utils.llm_resilience import parse_model_fallback_chain

    primary = resolve_role_model("text")
    chain = parse_model_fallback_chain(
        primary,
        {
            "model_fallback_chain": [
                {"role": "text"},
                {"role": "tool"},
            ]
        },
    )
    assert chain[0] == primary
    assert resolve_role_model("tool") in chain


def test_analyze_video_resolves_role_before_gemini_guard(monkeypatch):
    monkeypatch.setenv("LLM_QUALITY", "best")
    from prompts.llm_model_profiles import resolve_model_config, resolve_role_model
    from prompts.prompt_config import PROMPTS_CONFIG, PromptName

    mc = resolve_model_config(
        PROMPTS_CONFIG[PromptName.VIDEO_AUDIO_TRANSCRIPTION].get("model_config", {}) or {}
    )
    assert "gemini" in mc["model"].lower()
    assert mc["model"] == resolve_role_model("multimodal")
