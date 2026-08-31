"""Analysis artifact (disk) + agent draft.

- ``extra["research"]`` — gather only (visual/sound/motion landscape)
- ``extra["proposal"]`` — locks emotional_arc / must_keep_beats / quality_gates
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ResearchLandscape(BaseModel):
    saturated_angles: List[str] = Field(
        default_factory=list,
        description="Angles already overdone — avoid cloning these",
    )
    underserved_gaps: List[str] = Field(
        default_factory=list,
        description="Gaps / underserved angles this piece can own",
    )


class ResearchAngle(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(description="Short angle name")
    hook: str = Field(description="Emotional / opening hook for this direction")
    why_now: str = Field(default="", description="Why this angle is compelling now")
    grounded_in: List[str] = Field(
        default_factory=list,
        description="Which research findings support this angle",
    )


class ResearchSource(BaseModel):
    title: str = ""
    url: str = ""
    note: str = ""


class ResearchDataPoint(BaseModel):
    claim: str
    source_url: str = ""
    credibility: str = "secondary_source"


class ResearchVisualReference(BaseModel):
    description: str = Field(description="What the reference is")
    url: str = Field(default="", description="Source URL when available")
    what_works: str = Field(
        default="",
        description="What works visually/emotionally for our brief",
    )


class ResearchAudienceInsights(BaseModel):
    common_questions: List[str] = Field(default_factory=list)
    pain_points: List[str] = Field(default_factory=list)
    knowledge_level: str = ""


class AnalysisResearchBlock(BaseModel):
    """Gather-only research (gates live on proposal). Extra OM fields kept."""

    model_config = ConfigDict(extra="allow")

    topic: str = Field(description="Core topic researched")
    research_date: str = Field(description="ISO date YYYY-MM-DD when research was done")
    landscape: ResearchLandscape = Field(default_factory=ResearchLandscape)
    angles_discovered: List[ResearchAngle] = Field(
        default_factory=list,
        description="≥2 cinematic directions with hook + why_now (+ grounded_in)",
    )
    sources: List[ResearchSource] = Field(default_factory=list)
    visual_references: List[ResearchVisualReference] = Field(
        default_factory=list,
        description="Visual precedents (description / url / what_works)",
    )
    audience_insights: Optional[ResearchAudienceInsights] = Field(
        default=None,
        description="Optional audience notes",
    )
    data_points: List[ResearchDataPoint] = Field(
        default_factory=list,
        description="Optional factual anchors (not the cinematic focus)",
    )
    anti_patterns: List[str] = Field(
        default_factory=list,
        description="Looks/tropes to avoid",
    )


class ProposalConceptOption(BaseModel):
    """One cinematic concept direction (OM proposal concept_options row)."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="c1, c2, c3, …")
    title: str = Field(description="Specific compelling title — not generic")
    hook: str = Field(description="Opening line that creates curiosity / feeling")
    visual_approach: str = Field(
        description=(
            "Timed story spine for the whole piece: concrete beats from open→mid→end "
            "(who does what, comedy/payoff moments). Must be specific enough that Script "
            "can shoot without inventing a new plot."
        ),
    )
    key_points: List[str] = Field(
        default_factory=list,
        description="≥5 concrete story beats from THIS user brief to prove on screen",
    )
    emotional_arc: str = Field(default="", description="Arc for this concept")
    why_this_works: str = Field(default="", description="Rationale grounded in research")
    grounded_in: List[str] = Field(default_factory=list)


class ProposalSelectedConcept(BaseModel):
    model_config = ConfigDict(extra="allow")

    concept_id: str = Field(description="Selected concept id, e.g. c1")
    rationale: str = Field(default="", description="Why this concept was chosen")
    # Denormalized spine for script (copy of winning option)
    title: str = ""
    hook: str = ""
    visual_approach: str = ""
    key_points: List[str] = Field(default_factory=list)


class AnalysisProposalBlock(BaseModel):
    """Locks for outline/script. Extra OM proposal fields kept."""

    model_config = ConfigDict(extra="allow")

    emotional_arc: str = Field(
        description="Plain-language emotional arc for the piece",
    )
    selected_angle_name: str = Field(
        default="",
        description="Chosen research angle name (or short hybrid label)",
    )
    selected_hook: str = Field(
        default="",
        description="Locked opening hook sentence",
    )
    motion_required: bool = Field(
        default=True,
        description="Whether motion-led delivery is required",
    )
    delivery_shape: str = Field(
        default="",
        description="e.g. short_drama / teaser / social_cutdown",
    )
    must_keep_beats: List[str] = Field(
        default_factory=list,
        description="Non-deletable story beats from THIS user brief",
    )
    quality_gates: List[str] = Field(
        default_factory=list,
        description="Concrete gates for outline/script (timing, identity, etc.)",
    )
    concept_options: List[ProposalConceptOption] = Field(
        default_factory=list,
        description="≥3 genuinely different concepts (hook + visual_approach + key_points)",
    )
    selected_concept: Optional[ProposalSelectedConcept] = Field(
        default=None,
        description="Winning concept spine — Script must honor visual_approach + key_points",
    )


class AnalysisArtifact(BaseModel):
    schema_version: int = 1
    artifact: Literal["analysis"] = "analysis"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    video_type: str
    duration: float
    main_character: str
    purpose: str
    key_elements: List[str] = Field(default_factory=list)
    style_preferences: List[str] = Field(default_factory=list)
    target_audience: str = ""
    next_action: str = "outline"
    content_category: str = "Default"
    matched_style_category: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class AnalysisAgentDraft(BaseModel):
    """Args for write_analysis_artifact — field descriptions are exposed to the LLM."""

    video_type: str = Field(
        description="Primary format, e.g. short_drama / product_launch / educational / MV",
    )
    duration: float = Field(description="Target total duration in seconds")
    main_character: str = Field(description="Who/what the piece centers on")
    purpose: str = Field(description="One-sentence creative / business goal")
    key_elements: List[str] = Field(
        default_factory=list,
        description="Must-have story or product beats (3–8 short items) from THIS brief",
    )
    style_preferences: List[str] = Field(
        default_factory=list,
        description="Concrete visual/tone prefs (avoid empty 'cinematic')",
    )
    target_audience: str = Field(
        default="",
        description="Who watches this (age/platform/intent)",
    )
    next_action: str = Field(
        default="outline",
        description="Usually 'outline' — next pipeline stage",
    )
    content_category: str = Field(
        default="Default",
        description=(
            'One of "Default", "Lip-Sync MV", "Product Launch", "Short Drama". '
            'User asks for 短剧 / short drama / 穿越剧情 with character dialogue → "Short Drama".'
        ),
    )
    matched_style_category: Optional[str] = Field(
        default=None,
        description="Optional style-library match id/name if known",
    )
    research: Optional[AnalysisResearchBlock] = Field(
        default=None,
        description=(
            "Research gather (video-driven required) → extra.research. "
            "landscape, ≥2 angles_discovered, visual_references, sources."
        ),
    )
    proposal: Optional[AnalysisProposalBlock] = Field(
        default=None,
        description=(
            "Proposal lock (video-driven required) → extra.proposal. "
            "Require ≥3 concept_options + selected_concept spine "
            "(hook, visual_approach timed story, key_points≥5), plus "
            "emotional_arc / must_keep_beats / quality_gates."
        ),
    )
    extra: Dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque extras; research/proposal also mirrored under extra.*",
    )


def research_from_extra(extra: Optional[Dict[str, Any]]) -> Optional[AnalysisResearchBlock]:
    if not isinstance(extra, dict):
        return None
    raw = extra.get("research")
    if not isinstance(raw, dict):
        return None
    try:
        return AnalysisResearchBlock.model_validate(_coerce_visual_refs(raw))
    except Exception:
        return None


def proposal_from_extra(extra: Optional[Dict[str, Any]]) -> Optional[AnalysisProposalBlock]:
    if not isinstance(extra, dict):
        return None
    raw = extra.get("proposal")
    if not isinstance(raw, dict):
        return None
    try:
        return AnalysisProposalBlock.model_validate(raw)
    except Exception:
        return None


def must_keep_beats_from_extra(extra: Optional[Dict[str, Any]]) -> List[str]:
    prop = proposal_from_extra(extra)
    return list(prop.must_keep_beats) if prop and prop.must_keep_beats else []


def quality_gates_from_extra(extra: Optional[Dict[str, Any]]) -> List[str]:
    prop = proposal_from_extra(extra)
    return list(prop.quality_gates) if prop and prop.quality_gates else []


def _coerce_visual_refs(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize List[str] visual_references → objects."""
    out = dict(raw)
    refs = out.get("visual_references")
    if isinstance(refs, list) and refs and isinstance(refs[0], str):
        out["visual_references"] = [
            {"description": s, "url": "", "what_works": ""} for s in refs if isinstance(s, str)
        ]
    return out


def merge_research_into_extra(
    extra: Optional[Dict[str, Any]],
    research: Optional[AnalysisResearchBlock],
) -> Dict[str, Any]:
    out = dict(extra or {})
    if research is not None:
        out["research"] = research.model_dump(mode="json")
    return out


def merge_proposal_into_extra(
    extra: Optional[Dict[str, Any]],
    proposal: Optional[AnalysisProposalBlock],
) -> Dict[str, Any]:
    out = dict(extra or {})
    if proposal is not None:
        out["proposal"] = proposal.model_dump(mode="json")
    return out


def merge_research_and_proposal_into_extra(
    extra: Optional[Dict[str, Any]],
    research: Optional[AnalysisResearchBlock],
    proposal: Optional[AnalysisProposalBlock],
) -> Dict[str, Any]:
    out = merge_research_into_extra(extra, research)
    return merge_proposal_into_extra(out, proposal)
