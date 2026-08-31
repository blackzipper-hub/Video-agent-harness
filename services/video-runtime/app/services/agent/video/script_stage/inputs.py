"""Export outline/analysis priors for script stage."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.models.video_state import CharacterProfile, StoryOutline, VideoAnalysisResult
from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json


def export_script_inputs(
    *,
    thread_id: str,
    run_id: str,
    story_outline: StoryOutline,
    analysis: Optional[VideoAnalysisResult] = None,
    user_input: str = "",
    characters: Optional[Sequence[CharacterProfile]] = None,
) -> Dict[str, Any]:
    chapters = []
    if story_outline.structure and story_outline.structure.chapters:
        for ch in story_outline.structure.chapters:
            chapters.append(
                {
                    "id": ch.id,
                    "order": ch.order,
                    "title": ch.title,
                    "description": ch.description,
                    "duration": float(ch.duration or 0),
                }
            )
    cast: List[Dict[str, Any]] = []
    for c in characters or []:
        cast.append(
            {
                "id": c.id,
                "name": c.name,
                "type": getattr(getattr(c, "type", None), "value", None) or str(getattr(c, "type", "") or ""),
                "appearance": (c.appearance or "")[:200],
                "role": (c.role or "")[:120],
            }
        )
    outline_payload = {
        "artifact": "outline_brief",
        "schema_version": 1,
        "title": story_outline.title,
        "theme": story_outline.theme,
        "description": story_outline.description,
        "key_message": story_outline.key_message,
        "style_guide": story_outline.style_guide,
        "total_duration": int(story_outline.total_duration or 0),
        "chapters": chapters,
        "user_input": user_input or "",
        # Identity lock facts — names/ids for speakers (rules live in script-director SKILL)
        "cast_lock": cast,
    }
    write_input_json(thread_id, run_id, "outline_brief.json", outline_payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    out: Dict[str, Any] = {
        "outline_brief": f"{prefix}/inputs/outline_brief.json",
        "target_duration_seconds": int(story_outline.total_duration or 0),
        "artifact_name": "script.json",
        "cast_names": [c["name"] for c in cast if c.get("name")],
    }
    if analysis is not None:
        extra = dict(getattr(analysis, "extra", None) or {})
        # E2: surface selected concept spine at top-level for Script (also still in extra.proposal)
        proposal = extra.get("proposal") if isinstance(extra.get("proposal"), dict) else {}
        selected = proposal.get("selected_concept") if isinstance(proposal, dict) else None
        cc = getattr(analysis, "content_category", None)
        content_category = getattr(cc, "value", None) or (str(cc) if cc else "") or ""
        analysis_payload = {
            "artifact": "analysis_brief",
            "schema_version": 1,
            "content_category": content_category,
            "video_type": analysis.video_type,
            "main_character": analysis.main_character,
            "purpose": analysis.purpose,
            "key_elements": list(analysis.key_elements or []),
            "style_preferences": list(analysis.style_preferences or []),
            "hidden_style_description": getattr(analysis, "hidden_style_description", None),
            "extra": extra,
            "selected_concept": selected,
            "proposal_must_keep_beats": list(proposal.get("must_keep_beats") or [])
            if isinstance(proposal, dict)
            else [],
            "proposal_quality_gates": list(proposal.get("quality_gates") or [])
            if isinstance(proposal, dict)
            else [],
            "proposal_emotional_arc": (proposal.get("emotional_arc") or "")
            if isinstance(proposal, dict)
            else "",
        }
        write_input_json(thread_id, run_id, "analysis_brief.json", analysis_payload)
        out["analysis_brief"] = f"{prefix}/inputs/analysis_brief.json"
        out["content_category"] = content_category
    return out
