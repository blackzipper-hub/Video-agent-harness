"""research.generate — the platform capability behind reference research."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.capabilities.models import CapabilityRegistry
from app.services.agent.video import generate_research_by_request_service as research


def test_research_capability_is_registered_as_a_local_service():
    capability = CapabilityRegistry().get("research.generate")

    assert capability.executor == "local.service"
    assert capability.service_target == "generate_research_by_request"
    assert capability.output_type == "research"
    assert capability.terminal_events == ["research_generated"]


def test_generate_research_alias_resolves():
    assert CapabilityRegistry().canonical_id("generate-research") == "research.generate"


def test_research_does_not_require_a_locked_workflow():
    """Research is most useful while the user is still deciding what to make."""
    from app.chat.v2.workflows import capability_requires_workflow

    assert capability_requires_workflow("research.generate") is False


def test_mv_workflow_leads_with_research():
    from app.chat.v2.skill_catalog import SkillCatalog
    from app.chat.v2.workflows import configure_workflows, WORKFLOWS

    roots = Path(__file__).resolve().parents[1] / "skills"
    catalog = SkillCatalog([roots / "system", roots / "builtin", roots / "external"])
    catalog.discover()
    configure_workflows(catalog, CapabilityRegistry())

    spec = next(s for s in WORKFLOWS.values() if s.skill_name == "mv")
    assert spec.pipeline[0] == "research.generate"
    assert "research.generate" in (spec.allowed_capabilities or ())


SKILL = (
    Path(__file__).resolve().parents[1]
    / "skills/builtin/research/video-research/SKILL.md"
)


def _skill_table_fields(heading: str) -> list[str]:
    """Backticked field names from the first `| Field |` table under a heading."""
    text = SKILL.read_text(encoding="utf-8")
    section = text.split(heading, 1)[1]
    fields: list[str] = []
    for line in section.splitlines():
        if not line.startswith("|"):
            if fields:
                break
            continue
        match = re.match(r"\|\s*`([a-z_]+)`\s*\|", line)
        if match:
            fields.append(match.group(1))
        elif fields:
            break
    return fields


def test_direction_fields_match_the_skill_table():
    """The Skill's Step 7 table is the contract; the model must mirror it exactly.

    Drift here is silent in both directions: a code-only field is one the model is
    never told to write, and a Skill-only field is dropped on the way to the
    artifact. Both shipped once before this test existed.
    """
    assert _skill_table_fields("### Step 7: Angle synthesis") == list(
        research.ResearchDirection.model_fields
    )


def test_source_fields_match_the_skill_table():
    assert _skill_table_fields("### Step 8: Bibliography and handoff") == list(
        research.ResearchSource.model_fields
    )


def test_direction_type_enum_matches_the_skill_table():
    """The enum is duplicated for runtime validation; keep the copies identical."""
    text = SKILL.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| `type` |"))
    documented = set(re.findall(r"`([a-z_]+)`", row)) - {"type"}

    assert documented == set(research.DIRECTION_TYPES)


def test_video_research_stays_on_openmontage_placeholders():
    """Search queries use [subject]/[mood hint]/[delivery shape], not canned scenes."""
    text = SKILL.read_text(encoding="utf-8")
    assert "## Per-type tuning" not in text
    assert "content_category" not in text
    for phrase in (
        "天台唱歌",
        "闺蜜背叛",
        "当众打脸",
        "证据上桌",
        "近看拉开",
        "霓虹雨夜",
        "杯身水珠",
    ):
        assert phrase not in text


def test_the_service_prompt_delegates_the_method_to_the_skill():
    """How to search lives in the Skill. The service prompt must not inline it."""
    prompt = research._system_prompt("music_video")

    assert "video-research" in prompt
    assert "`music_video`" not in prompt
    assert "content_category" not in prompt
    assert "Infer the delivery shape from the brief." in prompt
    assert "beat-hit" not in prompt.lower()
    assert "Per-type" not in prompt


def test_the_prompt_infers_delivery_shape_from_the_brief():
    prompt = research._system_prompt(None)

    assert "Infer the delivery shape from the brief." in prompt
    assert "content_category" not in prompt
    assert "Per-type" not in prompt


def test_mv_workflow_does_not_stamp_content_category():
    from app.chat.v2.skill_catalog import SkillCatalog
    from app.chat.v2.workflows import configure_workflows, WORKFLOWS

    roots = Path(__file__).resolve().parents[1] / "skills"
    catalog = SkillCatalog([roots / "system", roots / "builtin", roots / "external"])
    catalog.discover()
    configure_workflows(catalog, CapabilityRegistry())

    spec = next(s for s in WORKFLOWS.values() if s.skill_name == "mv")
    assert "content_category" not in spec.parameters


def _registry(*urls: str):
    """A citation registry as it looks after a search run returned `urls`."""
    from app.services.agent.stage_runtime.provider_web_search import CitationRegistry

    registry = CitationRegistry()
    registry.urls = {url: "" for url in urls}
    return registry


@pytest.fixture
def write_tool():
    """Write tool whose search is recorded as having returned the ok/* pages."""
    holder: dict = {}
    registry = _registry(
        "https://ok/ref", *(f"https://ok/{index}" for index in range(5))
    )
    return research._make_write_tool(holder, registry), holder


def _directions(count: int = 3) -> list[dict]:
    return [
        {
            "name": f"direction {index}",
            "hook": "a feeling, stated once",
            "type": "mood_piece",
            "visual_references": [
                {
                    "description": "grade breakdown",
                    "url": "https://ok/ref",
                    "what_works": "teal-crushed blacks under a single tungsten key",
                }
            ],
            "motion_commitment": "slow dolly push-in on a long lens, tripod-stable",
        }
        for index in range(count)
    ]


def _summary() -> str:
    return (
        "Cuts land on the downbeat around 1.2s; low-key tungsten key with shallow "
        "depth of field; grade crushes the blacks toward teal."
    )


def _sources(count: int = 5) -> list[dict]:
    return [
        {
            "url": f"https://ok/{index}",
            "title": f"source {index}",
            "used_for": "landscape",
        }
        for index in range(count)
    ]


def _landscape(*, extra: list[dict] | None = None) -> dict:
    works = [
        {
            "title": f"existing work {index}",
            "url": "https://ok/ref",
            "source": "youtube",
            "angle": "mood piece",
            "what_it_covers": "grade and framing",
            "what_it_misses": "never stays on one face",
        }
        for index in range(3)
    ]
    if extra:
        works.extend(extra)
    return {
        "existing_content": works,
        "saturated_angles": ["neon rain empty streets"],
        "underserved_gaps": ["a face held through the drop"],
    }


@pytest.mark.asyncio
async def test_a_reference_search_never_returned_is_rejected(write_tool):
    """The plausible-but-invented URL is the failure this gate exists for.

    Measured over one A/B, 12% of cited URLs were dead with search on and 28%
    with it off, all of them recalled rather than retrieved and all wearing the
    same believable shape.
    """
    write, holder = write_tool
    directions = _directions()
    directions[0]["visual_references"] = [
        {
            "description": "a page that does not exist",
            "url": "https://vimeo.com/blog/post/lighting-for-cyberpunk/",
            "what_works": "invented",
        }
    ]

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape=_landscape(),
        directions=directions,
        sources=_sources(),
    )

    assert "never returned by your searches" in result
    assert "vimeo.com/blog/post/lighting-for-cyberpunk" in result
    assert not holder


@pytest.mark.asyncio
async def test_landscape_citations_are_checked_like_every_other_url(write_tool):
    """existing_content cites search results too, so it gets the same gate."""
    write, holder = write_tool

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape=_landscape(
            extra=[
                {
                    "title": "every neon rain montage",
                    "url": "https://nofilmschool.com/cyberpunk-aesthetic",
                    "source": "blog",
                    "angle": "city wide shots on the drop",
                    "what_it_covers": "scale",
                    "what_it_misses": "never stays on one face",
                }
            ]
        ),
        directions=_directions(),
        sources=_sources(),
    )

    assert "never returned by your searches" in result
    assert not holder


@pytest.mark.asyncio
async def test_tracking_params_do_not_make_a_citation_unrecognisable(write_tool):
    """OpenAI appends `?utm_source=openai` to every URL it cites.

    Compared raw, the same page is two different strings depending on who found
    it, and every real citation would be rejected as invented.
    """
    write, holder = write_tool
    directions = _directions()
    directions[0]["visual_references"][0]["url"] = "https://ok/ref?utm_source=openai"

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape=_landscape(),
        directions=directions,
        sources=[
            {
                "url": f"https://ok/{i}?utm_source=openai",
                "title": f"source {i}",
                "used_for": "landscape",
            }
            for i in range(5)
        ],
    )

    assert result.startswith("OK:"), result
    assert holder["payload"]["sources"][0]["url"] == "https://ok/0"
    assert (
        holder["payload"]["directions"][0]["visual_references"][0]["url"]
        == "https://ok/ref"
    )


@pytest.mark.asyncio
async def test_a_finding_without_a_link_is_kept_not_dropped(write_tool):
    """A finding with no citation is still a finding.

    Search snippets routinely describe a work well enough to record what it
    misses without yielding a linkable page. Requiring a URL to keep the entry
    would throw away the finding to punish the missing link.
    """
    write, holder = write_tool

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape=_landscape(
            extra=[
                {
                    "title": "a work with no link",
                    "source": "blog",
                    "angle": "texture study",
                    "what_it_covers": "material close-ups",
                    "what_it_misses": "close-ups",
                }
            ]
        ),
        directions=_directions(),
        sources=_sources(),
    )

    assert result.startswith("OK:"), result
    kept = holder["payload"]["landscape"]["existing_content"]
    assert "a work with no link" in [work["title"] for work in kept]


@pytest.mark.asyncio
async def test_an_unaudited_provider_leaves_the_gate_open(write_tool):
    """Gemini reports redirects, never a citation record.

    With an empty registry the gate cannot tell a real URL from an invented one,
    so it must not reject everything — that would make the fallback provider
    unusable rather than merely degraded.
    """
    holder: dict = {}
    write = research._make_write_tool(holder, _registry())

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape=_landscape(),
        directions=_directions(),
        sources=[
            {
                "url": f"https://anywhere/{i}",
                "title": f"source {i}",
                "used_for": "landscape",
            }
            for i in range(5)
        ],
    )

    assert result.startswith("OK:"), result
    assert len(holder["payload"]["sources"]) == 5


@pytest.mark.asyncio
async def test_write_rejects_a_thin_landscape(write_tool):
    """OpenMontage's research_brief schema: ≥3 existing works, ≥1 gap."""
    write, holder = write_tool

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape={
            "existing_content": [
                {"title": "only one", "source": "youtube", "angle": "a", "what_it_covers": "b"}
            ],
            "underserved_gaps": ["a face on the drop"],
        },
        directions=_directions(),
        sources=_sources(),
    )

    assert "existing_content" in result
    assert "at least 3" in result
    assert not holder

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape={
            "existing_content": [
                {
                    "title": f"work {index}",
                    "source": "youtube",
                    "angle": "a",
                    "what_it_covers": "b",
                }
                for index in range(3)
            ],
            "underserved_gaps": [],
        },
        directions=_directions(),
        sources=_sources(),
    )

    assert "underserved_gaps" in result
    assert "at least 1" in result
    assert not holder


@pytest.mark.asyncio
async def test_write_rejects_om_required_fields_left_blank(write_tool):
    """Shared with research_brief.schema.json: those keys must be filled, not just present."""
    write, holder = write_tool
    landscape = _landscape()
    landscape["existing_content"][0]["what_it_covers"] = ""

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape=landscape,
        directions=_directions(),
        sources=_sources(),
    )

    assert "what_it_covers" in result
    assert not holder

    sources = _sources()
    sources[0]["title"] = ""
    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape=_landscape(),
        directions=_directions(),
        sources=sources,
    )

    assert "title" in result
    assert not holder


@pytest.mark.asyncio
async def test_the_tool_is_invocable_the_way_an_agent_invokes_it(monkeypatch):
    """Invoke through the LangChain tool, not the bare function.

    The unit tests all called the function directly and passed, while the real
    agent could not bind a single argument: a `**kwargs` signature advertises one
    string parameter named "kwargs", so the model's JSON went in as text and every
    call failed. Only an .ainvoke through the tool wrapper exercises that binding.
    """

    holder: dict = {}
    tool = research._write_research_tool(
        holder, _registry("https://ok/ref", *(f"https://ok/{i}" for i in range(5)))
    )

    result = await tool.ainvoke(
        {
            "topic": "cyberpunk MV",
            "research_summary": _summary(),
            "landscape": _landscape(),
            "directions": _directions(),
            "sources": [
                {
                    "url": f"https://ok/{i}",
                    "title": f"source {i}",
                    "used_for": "landscape",
                }
                for i in range(5)
            ],
        }
    )

    assert result.startswith("OK:"), result
    assert holder["payload"]["directions"][0]["visual_references"]


def test_the_tool_advertises_the_skills_field_names():
    """The provider must see per-direction field names, not `array of anything`."""
    tool = research._write_research_tool({}, _registry())
    schema = tool.args_schema.model_json_schema()
    direction = schema["$defs"]["ResearchDirection"]["properties"]

    assert set(direction) == set(research.ResearchDirection.model_fields)


def test_the_schema_carries_openmontage_mins():
    """Same minItems OM puts on research_brief — not a second set of constants."""
    schema = research.ResearchDraft.model_json_schema()
    landscape = schema["$defs"]["Landscape"]["properties"]

    assert schema["properties"]["directions"]["minItems"] == 3
    assert schema["properties"]["sources"]["minItems"] == 5
    assert landscape["existing_content"]["minItems"] == 3
    assert landscape["underserved_gaps"]["minItems"] == 1
    assert set(schema["$defs"]["ResearchDirection"]["properties"]["type"]["enum"]) == set(
        research.DIRECTION_TYPES
    )
    assert set(schema["$defs"]["ExistingWork"]["required"]) == {
        "title",
        "source",
        "angle",
        "what_it_covers",
    }
    assert set(schema["$defs"]["ResearchSource"]["required"]) == {
        "url",
        "title",
        "used_for",
    }
    assert set(schema["$defs"]["Landscape"]["required"]) == {
        "existing_content",
        "saturated_angles",
        "underserved_gaps",
    }
    assert schema["$defs"]["ResearchSource"]["properties"]["url"]["format"] == "uri"


@pytest.mark.asyncio
async def test_write_rejects_fewer_than_three_directions(write_tool):
    write, _ = write_tool

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape=_landscape(),
        directions=_directions(2),
    )

    assert "directions" in result
    assert "at least 3" in result


@pytest.mark.asyncio
async def test_write_rejects_a_direction_missing_a_required_field(write_tool):
    write, _ = write_tool
    directions = _directions()
    directions[1]["motion_commitment"] = ""

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape=_landscape(),
        directions=directions,
        sources=_sources(),
    )

    assert "motion_commitment" in result
    assert "required" in result or "at least 1" in result


@pytest.mark.asyncio
async def test_write_does_not_judge_taste(write_tool):
    """Vague prose passes the gate on purpose — the Skill owns the quality bar.

    A character-count threshold reads like rigour and isn't: it passes
    `slow push in on the subject` and fails a tight one-liner. Enforcing it in
    code also forks the standard away from the Skill's Quality bar columns, which
    is how the gate started rejecting a field the Skill never asked for.
    """
    write, holder = write_tool
    directions = _directions()
    directions[0]["motion_commitment"] = "cinematic"

    result = await write(
        topic="a",
        research_summary="dark and moody",
        landscape=_landscape(),
        directions=directions,
        sources=_sources(),
    )

    assert result.startswith("OK:")
    assert holder


@pytest.mark.asyncio
async def test_write_rejects_a_type_outside_the_skills_enum(write_tool):
    write, _ = write_tool
    directions = _directions()
    directions[0]["type"] = "vibey"

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape=_landscape(),
        directions=directions,
        sources=_sources(),
    )

    assert "type" in result
    assert "vibey" in result


@pytest.mark.asyncio
async def test_write_rejects_a_thin_bibliography(write_tool):
    """One source is a hunch, not research."""
    write, holder = write_tool

    result = await write(
        topic="a",
        research_summary=_summary(),
        landscape=_landscape(),
        directions=_directions(),
        sources=[{"url": "https://ok/1", "title": "one", "used_for": "landscape"}],
    )

    assert "sources" in result
    assert "at least 5" in result
    assert not holder


@pytest.mark.asyncio
async def test_write_stores_the_brief_with_what_search_returned(write_tool):
    write, holder = write_tool
    sources = _sources()

    result = await write(
        topic="Cyberpunk MV",
        research_summary=_summary(),
        landscape=_landscape(),
        directions=_directions(),
        sources=sources,
    )

    assert result.startswith("OK:")
    payload = holder["payload"]
    assert payload["topic"] == "Cyberpunk MV"
    assert len(payload["sources"]) == 5
    # Kept so a saved brief records what was available, not just what was cited.
    assert "https://ok/ref" in payload["searched_urls"]


class _Annotated:
    """Message shaped like an OpenAI reply carrying url_citation annotations."""

    def __init__(self, *citations: tuple[str, str]):
        self.content = [
            {"type": "reasoning", "summary": []},
            {
                "type": "text",
                "text": "...",
                "annotations": [
                    {"type": "url_citation", "url": url, "title": title}
                    for url, title in citations
                ],
            },
        ]


def test_search_queries_are_read_off_the_provider_record():
    from app.services.agent.stage_runtime.provider_web_search import (
        CitationRegistry,
        search_queries,
    )

    class OpenAISearch:
        content = [
            {
                "type": "web_search_call",
                "action": {"query": "cyberpunk MV site:youtube.com"},
            }
        ]
        response_metadata = {}

    class GeminiSearch:
        content = "grounded"
        response_metadata = {
            "grounding_metadata": {
                "web_search_queries": ["cyberpunk site:reddit.com"]
            }
        }

    assert search_queries(OpenAISearch()) == ["cyberpunk MV site:youtube.com"]
    assert search_queries(GeminiSearch()) == ["cyberpunk site:reddit.com"]

    class Generation:
        def __init__(self, message):
            self.message = message

    class Result:
        def __init__(self, message):
            self.generations = [[Generation(message)]]

    registry = CitationRegistry()
    registry.on_llm_end(Result(OpenAISearch()))
    registry.on_llm_end(Result(GeminiSearch()))
    assert registry.queries == [
        "cyberpunk MV site:youtube.com",
        "cyberpunk site:reddit.com",
    ]


def test_citations_are_read_off_the_provider_record():
    from app.services.agent.stage_runtime.provider_web_search import url_citations

    found = url_citations(
        _Annotated(("https://www.bilibili.com/video/BV1?utm_source=openai", "调色"))
    )

    assert found == {"https://www.bilibili.com/video/BV1": "调色"}


def test_a_message_with_no_annotations_contributes_nothing():
    from app.services.agent.stage_runtime.provider_web_search import url_citations

    class Plain:
        content = "just text"

    assert url_citations(Plain()) == {}


class _SearchCall:
    """OpenAI turn that searched and then called a tool — no prose annotations."""

    def __init__(self, query: str, *urls: str):
        self.content = [
            {
                "type": "web_search_call",
                "action": {
                    "type": "search",
                    "query": query,
                    "sources": [{"type": "url", "url": url} for url in urls],
                },
            },
            {"type": "function_call", "name": "write_research"},
        ]
        self.response_metadata = {}


def test_search_sources_are_read_off_the_provider_record():
    from app.services.agent.stage_runtime.provider_web_search import (
        CitationRegistry,
        search_sources,
    )

    message = _SearchCall(
        "Hamil Bros cyberpunk",
        "https://hamilbrosstudios.com/project/labor-xii-cyberpunk-music-video/?utm_source=openai",
        "https://hamilbrosstudios.com/",
    )
    found = search_sources(message)

    assert found == {
        "https://hamilbrosstudios.com/project/labor-xii-cyberpunk-music-video/": "",
        "https://hamilbrosstudios.com/": "",
    }

    class Generation:
        def __init__(self, message):
            self.message = message

    class Result:
        def __init__(self, message):
            self.generations = [[Generation(message)]]

    registry = CitationRegistry()
    registry.on_llm_end(Result(message))
    assert registry.allows(
        "https://hamilbrosstudios.com/project/labor-xii-cyberpunk-music-video/"
    )
    assert not registry.allows("https://vimeo.com/blog/post/lighting-for-cyberpunk/")


def test_openai_search_requests_action_sources():
    from app.services.agent.stage_runtime.provider_web_search import (
        resolve_provider_web_search,
    )

    class _OpenAI:
        model = "gpt-5.6-terra"
        include = None

        def model_copy(self, *, update=None):
            other = _OpenAI()
            other.include = (update or {}).get("include")
            return other

    adapted, tools, strategy = resolve_provider_web_search(_OpenAI())
    assert strategy == "openai_web_search"
    assert adapted.include == ["web_search_call.action.sources"]
    assert tools == [{"type": "web_search"}]


def test_the_registry_collects_across_every_model_call():
    """Search happens over several turns; one turn's citations are not the set."""
    from app.services.agent.stage_runtime.provider_web_search import CitationRegistry

    class Generation:
        def __init__(self, message):
            self.message = message

    class Result:
        def __init__(self, message):
            self.generations = [[Generation(message)]]

    registry = CitationRegistry()
    registry.on_llm_end(Result(_Annotated(("https://a/one", "one"))))
    registry.on_llm_end(Result(_Annotated(("https://b/two", "two"))))

    assert registry.allows("https://a/one")
    assert registry.allows("https://b/two")
    assert not registry.allows("https://c/invented")


class _Model:
    def __init__(self, name: str):
        self.model = name


@pytest.mark.parametrize(
    ("configured", "openai_key", "expected"),
    [
        # An OpenAI deployment stays put; its citations are already auditable.
        ("gpt-5.6-terra", "sk-x", "gpt-5.6-terra"),
        ("o3-pro", "sk-x", "o3-pro"),
        # Gemini only ever cites an expiring redirect, so move to the provider
        # whose citation record the provenance gate can actually check.
        ("gemini-3.1-pro-preview", "sk-x", "gpt-5.6-terra"),
        ("gemini-2.5-flash", "sk-x", "gpt-5.6-terra"),
        ("some-local-llama", "sk-x", "gpt-5.6-terra"),
        # No OpenAI credential: degraded beats dead.
        ("gemini-3.1-pro-preview", "", "gemini-3.1-pro-preview"),
        ("gemini-2.5-flash", "", "gemini-3.1-pro-preview"),
        ("some-local-llama", "", "gemini-3.1-pro-preview"),
    ],
)
def test_research_prefers_the_provider_whose_citations_can_be_audited(
    monkeypatch, configured, openai_key, expected
):
    """Stubs the prompt loader: this asserts the branch choice, not model construction."""
    import sys
    import types
    from unittest.mock import MagicMock

    # The provider predicates live in a package whose __init__ re-exports the
    # deepagents factory, so importing the leaf drags the agent runtime in.
    for name in ("deepagents", "deepagents.backends", "deepagents.backends.filesystem"):
        monkeypatch.setitem(sys.modules, name, MagicMock())

    built: list[str] = []

    profiles = types.ModuleType("prompts.llm_model_profiles")
    profiles.resolve_model_config = lambda config: {
        "role": "multimodal", "model": configured,
    }
    loader = types.ModuleType("prompts.prompt_loader")

    def fake_create(config):
        built.append(config["model"])
        return _Model(config["model"])

    loader.create_llm_from_model_config = fake_create
    monkeypatch.setitem(sys.modules, "prompts", types.ModuleType("prompts"))
    monkeypatch.setitem(sys.modules, "prompts.llm_model_profiles", profiles)
    monkeypatch.setitem(sys.modules, "prompts.prompt_loader", loader)
    monkeypatch.setenv("OPENAI_API_KEY", openai_key)

    assert research._search_capable_llm().model == expected
    assert built[-1] == expected


@pytest.mark.asyncio
async def test_research_requires_a_brief():
    with pytest.raises(ValueError, match="requires a brief"):
        await research.generate_research_by_request(
            thread_id="t", run_id="r", user_input="   "
        )


def test_research_generate_is_declared_on_the_atomic_plugin():
    from pathlib import Path

    import yaml

    from app.video_runtime.builtin_plugins.cuti_provider import CutiAtomicProviderPlugin

    manifest = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1]
            / "plugins/cuti-atomic-providers/video-plugin.yaml"
        ).read_text(encoding="utf-8")
    )
    assert "research.generate" in manifest["contributions"]["capabilities"]
    assert "research.generate" in CutiAtomicProviderPlugin().capability_handlers()
