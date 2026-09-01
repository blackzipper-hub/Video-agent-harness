"""Routing coverage: DeepSeek / Deep Agent should prefer dest $mv for MV intents."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.capabilities.models import CapabilityRegistry
from app.chat.v2.deep_agent_runtime import DeepAgentRuntime
from app.chat.v2.harness import DynamicHarness
from app.chat.v2.skill_catalog import SkillCatalog
from app.chat.v2.workflows import (
    active_workflow,
    configure_workflows,
    is_workflow_skill,
    parse_explicit_skill_names,
)


def _catalog() -> SkillCatalog:
    root = Path(__file__).resolve().parents[1] / "skills"
    catalog = SkillCatalog([root / "system", root / "builtin", root / "external"])
    catalog.discover()
    configure_workflows(catalog, CapabilityRegistry())
    return catalog


def _score(desc: str, text: str) -> int:
    d = desc.lower()
    t = text.lower()
    keys = [
        "mv", "music video", "卡点", "歌曲", "beat", "唱这首歌",
        "短剧", "即梦", "seedance2",
    ]
    return sum(1 for k in keys if k in t and k in d)


def test_list_skills_exposes_mv_triggers():
    catalog = _catalog()
    view = {item["name"]: item for item in catalog.prompt_view()}
    assert "mv" in view
    desc = view["mv"]["description"]
    for token in ("MV", "卡点", "歌曲", "$mv"):
        assert token in desc


def test_explicit_dollar_mv_activation():
    _catalog()
    assert parse_explicit_skill_names("帮我做个 MV $mv") == ["mv"]
    merged = DynamicHarness._merge_activated_workflows(
        ["seedance2"], "改用 $mv 做卡点"
    )
    assert merged[0] == "mv"
    assert active_workflow(merged).skill_name == "mv"


def test_system_prompt_prefers_mv_for_music_video():
    runtime = DeepAgentRuntime.__new__(DeepAgentRuntime)
    runtime.settings = SimpleNamespace(DEEP_AGENT_V2_MAX_PARALLEL_GENERATION_TASKS=2)
    prompt = runtime._system_prompt()
    assert " mv" in f" {prompt}"
    assert "prefer mv" in prompt.lower()


def test_mv_intents_rank_mv_over_seedance2():
    catalog = _catalog()
    view = {item["name"]: item for item in catalog.prompt_view()}
    mv = view["mv"]["description"]
    sd2 = view.get("seedance2", {}).get("description", "")
    cases = [
        ("帮我做一个角色唱这首歌的MV", "mv"),
        ("用歌曲卡点做 music video", "mv"),
        ("beat sync 舞蹈视频", "mv"),
        ("$mv", "mv"),
    ]
    for text, expected in cases:
        explicit = parse_explicit_skill_names(text)
        if explicit:
            chosen = explicit[0]
        else:
            sm, s2 = _score(mv, text), _score(sd2, text) if sd2 else 0
            chosen = "mv" if sm >= s2 else "seedance2"
        assert chosen == expected, (text, chosen, expected)
    assert is_workflow_skill("mv")
