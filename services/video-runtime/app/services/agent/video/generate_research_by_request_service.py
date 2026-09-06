"""Reference research stage: web search → grounded creative directions.

Backs the ``research.generate`` capability. Any workflow can schedule it before
its prompt-writing stages.

The split: the `video-research` Skill owns every judgement call — search batches,
per-type emphasis, and the quality bar for each field. This module owns only what
a Skill cannot enforce: model selection, the output shape, and whether a cited URL
was ever actually returned by search.

That line is deliberate. An earlier version rejected briefs on character counts
("findings under 80 chars is too thin"), which is a taste judgement wearing a
number: `slow push in on the subject` clears 40 characters and says nothing. Taste
belongs in Markdown where it can be argued with. Code checks only what is
mechanically true — the field is present, the type is in the documented set, the
citation is one search really handed back.

Provenance is the one check that has to be code, and it has to run before the
model writes rather than after. Checking a link's pulse afterwards can only catch
a fabrication once it exists; measured over one A/B, 12% of cited URLs were dead
with search on and 28% with it off, all invented in the same confident shape.
Comparing against the provider's own citation record instead makes citing an
unvisited page impossible rather than merely detectable.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse
from typing import Any, Dict, List, Literal, Optional, get_args

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

# Research needs a provider whose search reports the real destination of every
# result. OpenAI does, as web_search_call.action.sources (plus optional
# url_citation annotations on prose). Gemini reports only an expiring
# vertexaisearch redirect, so a brief written on Gemini cannot be audited and
# rots within the hour — usable, but the fallback rather than the default.
_CITABLE_MODEL = "gpt-5.6-terra"
_SEARCH_FALLBACK_MODEL = "gemini-3.1-pro-preview"
DirectionType = Literal[
    "mood_piece", "tension_arc", "reveal", "intimate", "epic", "raw"
]
SourceReliability = Literal["primary", "secondary", "anecdotal"]
# Derived from the Literal so the Skill-table test and the field stay one set.
DIRECTION_TYPES = frozenset(get_args(DirectionType))


def _required_text(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("required")
    return text


def _as_uri(value: str, *, required: bool) -> str:
    text = (value or "").strip()
    if not text:
        if required:
            raise ValueError("required")
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be a URI")
    return text


class VisualReference(BaseModel):
    """One found precedent, kept as a record rather than a bare URL.

    Field names follow OpenMontage's ``research_brief`` schema, which the
    analysis stage already mirrors in ``contracts/artifacts/analysis.py``. A
    list of naked links is unusable an hour later: nobody remembers which of
    the five was the grade reference and which was the FPV one.
    """

    description: str = ""
    url: str = Field(default="", json_schema_extra={"format": "uri"})
    what_works: str = ""

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _as_uri(value, required=False)


class ExistingWork(BaseModel):
    """What already exists for this subject — OpenMontage's landscape entry.

    ``what_it_misses`` is the load-bearing half. Knowing five neon-rain montages
    exist only helps if you also know none of them stayed on one face.
    """

    title: str = Field(min_length=1)
    url: str = Field(default="", json_schema_extra={"format": "uri"})
    source: str = Field(min_length=1)
    angle: str = Field(min_length=1)
    what_it_covers: str = Field(min_length=1)
    what_it_misses: str = ""
    engagement_signal: str = ""

    @field_validator("title", "source", "angle", "what_it_covers")
    @classmethod
    def _required(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _as_uri(value, required=False)


class Landscape(BaseModel):
    """OpenMontage ``research_brief.landscape``: 3 works, 1 gap, all three keys."""

    existing_content: List[ExistingWork] = Field(min_length=3)
    saturated_angles: List[str]
    underserved_gaps: List[str] = Field(min_length=1)


class ResearchDirection(BaseModel):
    """Mirrors the Step 7 table of the video-research Skill, field for field.

    Any name here that the Skill does not ask for is a field the model will never
    fill; any Skill field missing here is silently dropped on the way to the
    artifact. Both have happened, hence the test that diffs the two.
    """

    name: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    type: DirectionType
    visual_references: List[VisualReference] = Field(default_factory=list)
    # OM keeps this as ``music_source.mood_direction`` on the proposal packet,
    # distilled from research. We have no proposal stage, so it rides the
    # direction. Music only — sound design and dialogue are the edit's business.
    mood_direction: str = ""
    motion_commitment: str = Field(min_length=1)
    grounded_in: List[str] = Field(default_factory=list)

    @field_validator("name", "hook", "motion_commitment")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _required_text(value)


class ResearchSource(BaseModel):
    url: str = Field(min_length=1, json_schema_extra={"format": "uri"})
    title: str = Field(min_length=1)
    used_for: str = Field(min_length=1)
    reliability: Optional[SourceReliability] = None

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _as_uri(value, required=True)

    @field_validator("title", "used_for")
    @classmethod
    def _required(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("reliability", mode="before")
    @classmethod
    def _blank_reliability(cls, value: Any) -> Any:
        return None if value in (None, "") else value


class ResearchDraft(BaseModel):
    topic: str = Field(min_length=1)
    research_summary: str = ""
    landscape: Landscape
    reference_context: str = ""
    directions: List[ResearchDirection] = Field(min_length=3)
    sources: List[ResearchSource] = Field(min_length=5)

    @field_validator("topic")
    @classmethod
    def _topic_required(cls, value: str) -> str:
        return _required_text(value)


def cited_urls(draft: ResearchDraft) -> List[str]:
    """Every http(s) URL the brief points at, in one list."""
    urls = [source.url for source in draft.sources]
    urls += [
        reference.url
        for direction in draft.directions
        for reference in direction.visual_references
    ]
    urls += [work.url for work in draft.landscape.existing_content]
    return [
        url
        for url in dict.fromkeys(str(u or "").strip() for u in urls)
        if url.startswith(("http://", "https://"))
    ]


def unvouched(urls: List[str], registry: Any) -> List[str]:
    """URLs the provider never reported returning.

    Recalled from training rather than retrieved: the shape is always plausible
    (``vimeo.com/blog/post/lighting-for-cyberpunk/``) and the page has never
    existed. There is no reason to keep one, because a real search result was
    available for the asking.
    """
    return [url for url in urls if not registry.allows(url)]


def _make_write_tool(holder: Dict[str, Any], registry: Any):
    from app.services.agent.stage_runtime.provider_web_search import canonical_url

    # Parameters are spelled out rather than collected with **kwargs. A **kwargs
    # tool advertises one string parameter literally named "kwargs", so the model
    # JSON-encodes the whole brief into it and every call fails to bind — the
    # agent retried six times, gave up, and answered in prose.
    #
    # Async only so it can be awaited alongside the rest of the agent loop; the
    # provenance check is a set lookup against what search already returned.
    async def write_research(
        topic: str = "",
        research_summary: str = "",
        landscape: Optional[Any] = None,
        reference_context: str = "",
        directions: Optional[List[Any]] = None,
        sources: Optional[List[Any]] = None,
    ) -> str:
        """Persist the research brief. Call once the directions are grounded."""
        try:
            draft = ResearchDraft.model_validate(
                {
                    "topic": topic,
                    "research_summary": research_summary,
                    "landscape": landscape or {},
                    "reference_context": reference_context,
                    "directions": directions or [],
                    "sources": sources or [],
                }
            )
        except ValidationError as exc:
            return f"VALIDATION_ERROR: {exc}"

        # Rejected before anything is stored, and named individually, because
        # the agent still has a turn left: it can look the reference back up in
        # its own results and cite the URL search actually gave it.
        invented = unvouched(cited_urls(draft), registry)
        if invented:
            return (
                "VALIDATION_ERROR: these URLs were never returned by your "
                f"searches, so they cannot be cited: {invented}. Replace each "
                "one with a URL that appeared verbatim in a search result, or "
                "drop the citation and keep the finding."
            )

        # Store the canonical form. Providers append their own tracking params
        # (`?utm_source=openai`), which would otherwise make the same page two
        # different strings in one brief.
        for source in draft.sources:
            source.url = canonical_url(source.url)
        for direction in draft.directions:
            for reference in direction.visual_references:
                reference.url = canonical_url(reference.url)
        for work in draft.landscape.existing_content:
            work.url = canonical_url(work.url)

        holder["payload"] = {
            "topic": draft.topic.strip(),
            "research_summary": draft.research_summary.strip(),
            "landscape": draft.landscape.model_dump(),
            "reference_context": draft.reference_context.strip(),
            "directions": [d.model_dump() for d in draft.directions],
            "sources": [s.model_dump() for s in draft.sources],
            "searched_urls": sorted(registry.urls),
            "search_queries": list(getattr(registry, "queries", None) or []),
        }
        return (
            f"OK: research stored with {len(draft.directions)} directions and "
            f"{len(draft.sources)} sources, all traced to search results"
        )

    return write_research


def _write_research_tool(holder: Dict[str, Any], registry: Any) -> Any:
    """Bind the write tool to ResearchDraft so the model sees the real shape.

    Without an args_schema, `directions` reaches the model as "array of anything"
    and it has to guess the per-direction field names — the one thing the Skill's
    Step 7 table already settled. Passing the model means the provider sees
    `visual_references`, `mood_direction`, `motion_commitment` and the rest by
    name.
    """
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(
        coroutine=_make_write_tool(holder, registry),
        name="write_research",
        description=(
            "Persist the finished research brief. Call once after searching, "
            "with the topic, research_summary, landscape "
            "(≥3 existing_content, ≥1 underserved_gap), at least 3 "
            "directions, and at least 5 sources."
        ),
        args_schema=ResearchDraft,
    )


def _search_capable_llm() -> Any:
    """Build the research model, preferring one whose citations can be audited.

    The ranking is about the citation record, not the vendor. OpenAI reports
    every source on ``web_search_call.action.sources``, which is
    what the provenance gate compares against. Gemini reports an expiring
    ``vertexaisearch`` redirect instead, so on that provider the gate has
    nothing to check and the stored links rot within the hour.

    So: keep an OpenAI deployment as configured, move a Gemini one over when an
    OpenAI key exists, and otherwise stay on Gemini 3 — degraded but working,
    rather than dead for want of a credential the deployment never had.
    """
    import os

    from app.services.agent.stage_runtime.provider_web_search import (
        is_gemini3_model,
        is_openai_model,
        model_identifier,
    )
    from prompts.llm_model_profiles import resolve_model_config
    from prompts.prompt_loader import create_llm_from_model_config

    model_config = resolve_model_config({"role": "multimodal"})
    llm = create_llm_from_model_config(model_config)
    if is_openai_model(llm):
        return llm

    if (os.getenv("OPENAI_API_KEY") or "").strip():
        logger.info(
            "research stage: %s cites redirects; switching to %s for auditable "
            "citations",
            model_identifier(llm),
            _CITABLE_MODEL,
        )
        model_config["model"] = _CITABLE_MODEL
        return create_llm_from_model_config(model_config)

    if is_gemini3_model(llm):
        return llm

    logger.info(
        "research stage: model %s has no native web search; falling back to %s",
        model_identifier(llm),
        _SEARCH_FALLBACK_MODEL,
    )
    model_config["model"] = _SEARCH_FALLBACK_MODEL
    return create_llm_from_model_config(model_config)


def _system_prompt(content_category: Optional[str] = None) -> str:
    """Harness glue only.

    Every instruction about *how* to research — search batches, search budget,
    citation rules — lives in the video-research Skill. Restating any of it here
    would fork the methodology into a copy that silently drifts, so this prompt
    says who the agent is, which Skill owns the method, and how to hand the
    result back.

    ``content_category`` is accepted for callers that still pass it (short
    drama / product-ad workflows). It is not a delivery shape — that is
    inferred from the brief, same as OpenMontage.
    """
    del content_category
    return (
        "You are a video production stage agent for reference research. "
        "Load the video-research Skill and follow it exactly — its process, "
        "its search batches, and its execution "
        "constraints. Infer the delivery shape from the brief. "
        "Cite only URLs that your searches returned. Then call write_research. "
        "If it returns VALIDATION_ERROR, fix what it names and call it again "
        "in the same turn."
    )


async def generate_research_by_request(
    *,
    thread_id: str,
    run_id: str,
    user_input: str,
    content_category: Optional[str] = None,
    detected_language: Optional[str] = None,
) -> Dict[str, Any]:
    """Research references for a brief and return a structured research payload."""
    brief = (user_input or "").strip()
    if not brief:
        raise ValueError("research.generate requires a brief (parameters.user_input)")

    from langchain_core.messages import HumanMessage

    from app.services.agent.stage_runtime.deep_agent_factory import (
        create_stage_deep_agent,
    )
    from app.services.agent.stage_runtime.paths import virtual_skills_builtin
    from app.services.agent.stage_runtime.provider_web_search import CitationRegistry

    holder: Dict[str, Any] = {}
    registry = CitationRegistry()
    from app.chat.v2.language import output_language_instruction

    language_note = (
        f"\n\n{output_language_instruction(detected_language)}"
        if detected_language
        else ""
    )
    agent = create_stage_deep_agent(
        model=_search_capable_llm(),
        tools=[_write_research_tool(holder, registry)],
        skills_virtual_paths=[virtual_skills_builtin("research")],
        system_prompt=_system_prompt(content_category) + language_note,
        name="research_stage",
        enable_web_search=True,
    )

    task = (
        f"Research references for this brief.\n\nBrief: {brief}\n\n"
        "Follow the video-research Skill's search batches, then call write_research."
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        config={"callbacks": [registry]},
    )

    payload = holder.get("payload")
    if not payload:
        raise RuntimeError(
            "research stage produced no verified research brief — the agent never "
            "completed write_research"
        )
    logger.info(
        "research stage OK thread=%s run=%s directions=%d sources=%d searched=%d",
        thread_id,
        run_id,
        len(payload["directions"]),
        len(payload["sources"]),
        len(payload.get("search_queries") or []),
    )
    return {
        **payload,
        "title": f"Reference research: {payload['topic']}"[:120],
        "summary": payload["research_summary"].strip() or (
            f"{len(payload['directions'])} directions grounded in "
            f"{len(payload['sources'])} sources from search"
        ),
        "messages": len(list(result.get("messages") or [])),
    }
