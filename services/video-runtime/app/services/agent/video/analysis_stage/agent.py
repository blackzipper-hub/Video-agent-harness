"""Analysis stage deep agent (folded cinematic research + thin proposal)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from app.contracts.artifacts.analysis import (
    AnalysisAgentDraft,
    AnalysisArtifact,
    AnalysisProposalBlock,
    AnalysisResearchBlock,
    merge_research_and_proposal_into_extra,
    must_keep_beats_from_extra,
    proposal_from_extra,
    research_from_extra,
)
from app.models.tool_enums import ContentCategory
from app.models.video_state import VideoAnalysisResult
from app.services.agent.stage_runtime.deep_agent_factory import create_stage_deep_agent
from app.services.agent.stage_runtime.generic_stage import (
    ensure_artifact_from_result,
    _log_write_tool_retries,
)
from app.services.agent.stage_runtime.paths import (
    is_story_narrative_category,
    narrative_skills_paths,
    virtual_run_prefix,
)
from app.services.agent.stage_runtime.workspace import (
    artifact_relpath,
    read_artifact_json,
    write_artifact_json,
)
from prompts.llm_model_profiles import resolve_model_config

logger = logging.getLogger(__name__)

_ALLOWED_CC = {c.value for c in ContentCategory}
# Official google_search + FC requires Gemini 3 + server-side flag (provider_web_search).
_ANALYSIS_GEMINI3 = "gemini-3.1-pro-preview"


def _validate_research(research: Optional[AnalysisResearchBlock], *, require: bool) -> Optional[str]:
    """Cinematic research gather checks (gates live on proposal)."""
    if not require:
        return None
    if research is None:
        return "research required for video-driven analysis (web search + extra.research)"
    if not (research.topic or "").strip():
        return "research.topic required"
    if not (research.research_date or "").strip():
        return "research.research_date required (YYYY-MM-DD)"
    if len(research.landscape.saturated_angles or []) < 1:
        return "research.landscape.saturated_angles needs ≥1"
    if len(research.landscape.underserved_gaps or []) < 1:
        return "research.landscape.underserved_gaps needs ≥1"
    if len(research.angles_discovered or []) < 3:
        return "research.angles_discovered needs ≥3 (cinematic direction diversity)"
    for i, angle in enumerate(research.angles_discovered or []):
        name = getattr(angle, "name", None) if not isinstance(angle, dict) else angle.get("name")
        hook = getattr(angle, "hook", None) if not isinstance(angle, dict) else angle.get("hook")
        if not (name or "").strip() or not (hook or "").strip():
            return f"research.angles_discovered[{i}] needs name + hook"
    if len(research.visual_references or []) < 1:
        return "research.visual_references needs ≥1 (description[/url/what_works])"
    if len(research.sources or []) < 3:
        return "research.sources needs ≥3 URLs after web search (prefer ≥5)"
    return None


def _validate_proposal(proposal: Optional[AnalysisProposalBlock], *, require: bool) -> Optional[str]:
    if not require:
        return None
    if proposal is None:
        return "proposal required for video-driven analysis (→ extra.proposal)"
    if not (proposal.emotional_arc or "").strip():
        return "proposal.emotional_arc required"
    if len(proposal.must_keep_beats or []) < 1:
        return "proposal.must_keep_beats needs ≥1 (signature beats from THIS brief)"
    if len(proposal.quality_gates or []) < 1:
        return "proposal.quality_gates needs ≥1"
    # E1: thick concept spine (OM proposal density)
    opts = list(proposal.concept_options or [])
    if len(opts) < 3:
        return "proposal.concept_options needs ≥3 genuinely different concepts"
    for i, c in enumerate(opts):
        cid = getattr(c, "id", None) if not isinstance(c, dict) else c.get("id")
        hook = getattr(c, "hook", None) if not isinstance(c, dict) else c.get("hook")
        va = getattr(c, "visual_approach", None) if not isinstance(c, dict) else c.get("visual_approach")
        kps = getattr(c, "key_points", None) if not isinstance(c, dict) else c.get("key_points")
        if not (cid or "").strip() or not (hook or "").strip():
            return f"proposal.concept_options[{i}] needs id + hook"
        if not (va or "").strip() or len(str(va)) < 40:
            return f"proposal.concept_options[{i}].visual_approach too thin (need timed story spine)"
        if len(kps or []) < 5:
            return f"proposal.concept_options[{i}].key_points needs ≥5 from THIS brief"
    sel = proposal.selected_concept
    if sel is None:
        return "proposal.selected_concept required (winning concept spine for Script)"
    sel_id = getattr(sel, "concept_id", None) or ""
    sel_va = getattr(sel, "visual_approach", None) or ""
    sel_kps = getattr(sel, "key_points", None) or []
    if not str(sel_id).strip():
        return "proposal.selected_concept.concept_id required"
    if len(str(sel_va).strip()) < 40:
        return "proposal.selected_concept.visual_approach required (copy winning spine)"
    if len(sel_kps) < 5:
        return "proposal.selected_concept.key_points needs ≥5"
    return None


def _make_write_tool(
    *,
    thread_id: str,
    run_id: str,
    prefer_duration: Optional[float],
    require_research: bool,
):
    # Video-driven: research + proposal; audio-driven: both optional.
    require_proposal = require_research

    def write_analysis_artifact(**kwargs: Any) -> str:
        """Validate analysis draft and write artifacts/analysis.json."""
        try:
            draft = AnalysisAgentDraft.model_validate(kwargs)
        except ValidationError as e:
            return f"VALIDATION_ERROR: {e}"

        cc = (draft.content_category or "Default").strip()
        if cc not in _ALLOWED_CC:
            return (
                f"VALIDATION_ERROR: content_category={cc!r} "
                f"must be one of {sorted(_ALLOWED_CC)}"
            )
        if draft.duration <= 0:
            return "VALIDATION_ERROR: duration must be > 0"
        if prefer_duration is not None and abs(float(draft.duration) - float(prefer_duration)) > 1.5:
            return (
                f"VALIDATION_ERROR: duration={draft.duration} should match "
                f"audio/target ≈ {prefer_duration}"
            )
        if not (draft.main_character and draft.purpose and draft.video_type):
            return "VALIDATION_ERROR: video_type, main_character, purpose required"

        research_err = _validate_research(draft.research, require=require_research)
        if research_err:
            return f"VALIDATION_ERROR: {research_err}"
        proposal_err = _validate_proposal(draft.proposal, require=require_proposal)
        if proposal_err:
            return f"VALIDATION_ERROR: {proposal_err}"

        extra = merge_research_and_proposal_into_extra(
            draft.extra, draft.research, draft.proposal
        )
        art = AnalysisArtifact(
            thread_id=thread_id,
            run_id=run_id,
            video_type=draft.video_type,
            duration=float(draft.duration),
            main_character=draft.main_character,
            purpose=draft.purpose,
            key_elements=list(draft.key_elements or []),
            style_preferences=list(draft.style_preferences or []),
            target_audience=draft.target_audience or "",
            next_action=draft.next_action or "outline",
            content_category=cc,
            matched_style_category=draft.matched_style_category,
            extra=extra,
        )
        write_artifact_json(thread_id, run_id, "analysis.json", art.model_dump(mode="json"))
        return f"OK wrote {artifact_relpath(thread_id, run_id, 'analysis.json')}"

    return StructuredTool.from_function(
        func=write_analysis_artifact,
        name="write_analysis_artifact",
        description=(
            "Validate and persist analysis.json. Args match AnalysisAgentDraft "
            "(video-driven: include research + proposal). "
            "On VALIDATION_ERROR, fix and call again in the same turn."
        ),
        args_schema=AnalysisAgentDraft,
    )


def artifact_to_video_analysis(art: AnalysisArtifact) -> VideoAnalysisResult:
    return VideoAnalysisResult(
        video_type=art.video_type,
        duration=float(art.duration),
        main_character=art.main_character,
        purpose=art.purpose,
        key_elements=list(art.key_elements or []),
        style_preferences=list(art.style_preferences or []),
        target_audience=art.target_audience or "",
        next_action=art.next_action or "outline",
        content_category=ContentCategory.from_value(art.content_category),
        matched_style_category=art.matched_style_category,
        extra=dict(art.extra or {}),
    )


async def generate_analysis_via_deep_agent(
    *,
    thread_id: str,
    run_id: str,
    input_paths: Dict[str, Any],
    detected_language: Optional[str] = None,
) -> Tuple[AnalysisArtifact, VideoAnalysisResult, List[Any]]:
    from prompts.prompt_config import PROMPTS_CONFIG, PromptName
    from prompts.prompt_loader import create_llm_from_model_config

    prefer = input_paths.get("target_duration_seconds")
    audio_path = input_paths.get("audio_context")
    # Video-driven: research + proposal folded into analysis. Audio-driven: optional.
    require_research = not bool(audio_path)
    write_tool = _make_write_tool(
        thread_id=thread_id,
        run_id=run_id,
        prefer_duration=float(prefer) if prefer is not None else None,
        require_research=require_research,
    )
    entry = PROMPTS_CONFIG[PromptName.VIDEO_REQUIREMENTS_ANALYSIS]
    mc = resolve_model_config(dict(entry.get("model_config") or {"role": "multimodal"}))
    # Gemini 3 required for official google_search + deepagents FC combo
    if "gemini-3" not in str(mc.get("model") or "").lower():
        logger.info(
            "analysis stage: forcing model %s for web_search (was %s)",
            _ANALYSIS_GEMINI3,
            mc.get("model"),
        )
        mc["model"] = _ANALYSIS_GEMINI3
    llm = create_llm_from_model_config(mc)

    prefix = virtual_run_prefix(thread_id, run_id)
    brief_path = input_paths["user_brief"]

    content_category = str(input_paths.get("content_category") or "")
    story_narrative = is_story_narrative_category(content_category)
    craft_bit = (
        "For story narrative (Default / Short Drama) stay on research + proposal "
        "directors; do NOT activate Explainer storytelling templates. "
        if story_narrative
        else "Use creative skills as needed. "
    )
    system_prompt = (
        "You are a video production stage agent for analysis. "
        "Activate analysis-director, then research-director and proposal-director "
        f"(full cinematic process). {craft_bit}"
        "Use provider web search, then read briefs with read_file limit=2000. "
        "Persist via write_analysis_artifact (args match AnalysisAgentDraft). "
        "If write returns VALIDATION_ERROR, fix and call again in the same turn. "
        "No human approval wait — auto-select a thick brief-faithful selected_concept "
        "(timed visual_approach + key_points covering every concrete user-brief event) "
        "and write in this turn. Delivery is Seedance/I2V — no Remotion/HyperFrames choice. "
    )

    agent = create_stage_deep_agent(
        model=llm,
        tools=[write_tool],
        skills_virtual_paths=narrative_skills_paths(
            "analysis",
            content_category=content_category,
        ),
        system_prompt=system_prompt,
        name="analysis_stage",
        enable_web_search=True,
    )

    lang_note = ""
    if detected_language:
        lang_note = (
            f" Write ALL user-facing creative fields in language: {detected_language} "
            f"(style_preferences tags, main_character, purpose, video_type, "
            f"target_audience, key_elements). Do not use English style tags when "
            f"language is {detected_language}, even if the brief mentions 海外 or "
            f"contains an English title."
        )
    research_note = (
        " Video-driven: include research and proposal per analysis-director "
        "(≥3 concept_options + selected_concept spine)."
        if require_research
        else " Audio-driven: research/proposal optional; prefer audio duration."
    )
    text = (
        f"Run the analysis stage.{lang_note}{research_note} "
        f"Read {brief_path}"
        + (f" and {audio_path}" if audio_path else "")
        + " with read_file limit=2000. Follow analysis-director. "
        "Call write_analysis_artifact."
    )
    content: Any = text
    image_urls = input_paths.get("reference_image_urls") or []
    if image_urls:
        parts: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        for url in image_urls[:8]:
            parts.append({"type": "image_url", "image_url": {"url": url}})
        content = parts

    result = await agent.ainvoke({"messages": [HumanMessage(content=content)]})
    messages = list(result.get("messages") or [])
    _log_write_tool_retries("analysis", messages)
    ensure_artifact_from_result(
        result=result if isinstance(result, dict) else dict(result),
        write_tool=write_tool,
        thread_id=thread_id,
        run_id=run_id,
        artifact_name="analysis.json",
        stage="analysis",
    )

    raw = read_artifact_json(thread_id, run_id, "analysis.json")
    artifact = AnalysisArtifact.model_validate(raw)
    analysis = artifact_to_video_analysis(artifact)
    research = research_from_extra(artifact.extra)
    proposal = proposal_from_extra(artifact.extra)
    mk = must_keep_beats_from_extra(artifact.extra)
    logger.info(
        "analysis deep agent OK thread=%s run=%s path=%s/artifacts/analysis.json "
        "research=%s proposal=%s must_keep=%d",
        thread_id,
        run_id,
        prefix,
        bool(research),
        bool(proposal),
        len(mk),
    )
    return artifact, analysis, messages
