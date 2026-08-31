"""Script stage — thin runtime; craft rules in script-director skill."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.contracts.artifacts.script import ScriptAgentDraft, ScriptArtifact, ScriptVoicePerformance
from app.services.agent.stage_runtime.generic_stage import (
    load_artifact,
    make_write_json_tool,
    run_stage_deep_agent,
)
from prompts.prompt_config import PromptName

logger = logging.getLogger(__name__)


def _validate(draft: ScriptAgentDraft, cast_names: Optional[List[str]] = None) -> Optional[str]:
    """Structural/tool gate only — synopsis/contact/UI craft rules live in SKILL.md."""
    if not draft.sections:
        return "sections empty"
    total = float(draft.total_duration_seconds or 0)
    if total <= 0:
        return "total_duration_seconds must be > 0"
    roles = {s.beat_role for s in draft.sections}
    if "hook" not in roles and not any("hook" in (s.label or "").lower() for s in draft.sections):
        return "need a HOOK section"
    if "landing" not in roles and not any(
        "landing" in (s.label or "").lower() for s in draft.sections
    ):
        return "need a LANDING section"
    ordered = sorted(draft.sections, key=lambda s: float(s.start_seconds))
    if float(ordered[0].start_seconds) > 0.6:
        return "first section should start near 0s"
    if abs(float(ordered[-1].end_seconds) - total) > 1.0:
        return f"last end_seconds={ordered[-1].end_seconds} should ≈ total={total}"
    if not draft.beat_map:
        draft.beat_map = ["hook", "escalation", "reveal", "landing"]
    if (draft.voice_performance_intent or "").strip() and draft.voice_performance is None:
        draft.voice_performance = ScriptVoicePerformance(
            performance_intent=draft.voice_performance_intent.strip(),
            pacing_profile="cinematic",
        )
    elif draft.voice_performance and not (draft.voice_performance_intent or "").strip():
        draft.voice_performance_intent = draft.voice_performance.performance_intent or ""
    if draft.voice_performance is None:
        return "voice_performance object required"

    for s in ordered:
        if float(s.end_seconds) <= float(s.start_seconds):
            return f"section {s.id} end<=start"
        if not (s.text or "").strip():
            return f"section {s.id} empty text"
        if s.delivery_cues is None or not (
            (s.delivery_cues.delivery_note or "").strip()
            or (s.delivery_cues.energy or "").strip()
            or s.delivery_cues.pace
        ):
            return f"section {s.id} needs delivery_cues"
    return None


async def generate_script_via_deep_agent(
    *,
    thread_id: str,
    run_id: str,
    input_paths: Dict[str, Any],
    detected_language: Optional[str] = None,
) -> Tuple[ScriptArtifact, List[Any]]:
    artifact_name = input_paths.get("artifact_name") or "script.json"
    target = input_paths.get("target_duration_seconds")
    cast_names = list(input_paths.get("cast_names") or [])

    tool = make_write_json_tool(
        thread_id=thread_id,
        run_id=run_id,
        artifact_name=artifact_name,
        draft_model=ScriptAgentDraft,
        tool_name="write_script_artifact",
        extra_validate=lambda d: _validate(d, cast_names=cast_names),
        stamp={
            "artifact": "script",
            "schema_version": 1,
            "thread_id": thread_id,
            "run_id": run_id,
        },
    )
    outline_brief = input_paths.get("outline_brief")
    analysis_brief = input_paths.get("analysis_brief")
    read_bits = []
    if outline_brief:
        read_bits.append(str(outline_brief))
    if analysis_brief:
        read_bits.append(str(analysis_brief))
    read_hint = (
        " and ".join(read_bits)
        if read_bits
        else "inputs/outline_brief.json (+ analysis_brief if present)"
    )
    from app.services.agent.stage_runtime.paths import is_story_narrative_category

    content_category = str(input_paths.get("content_category") or "")
    human = (
        f"Run script stage. Read {read_hint} "
        f"(read_file limit=2000). Follow script-director. "
        f"write_script_artifact total_duration_seconds≈{target}."
    )
    if cast_names:
        human += f" Cast names for dialogue speakers: {cast_names}."
    system_extra = ""
    if is_story_narrative_category(content_category):
        system_extra = (
            "Story narrative mode (Default / Short Drama): write dense shootable "
            "dialogue covering every selected_concept key_point / visual_approach beat. "
            "Do NOT use trailer sparse/title-led defaults or Explainer storytelling templates. "
            "No per-line汉字 hard cap. Persist in this turn — no human approval wait."
        )
    msgs = await run_stage_deep_agent(
        stage="script",
        agent_name="script_stage",
        prompt_name=PromptName.VIDEO_OUTLINE_GENERATION_VIDEO_DRIVEN,  # model_config only
        tools=[tool],
        human_text=human,
        detected_language=detected_language,
        draft_model=ScriptAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
        system_extra=system_extra,
        content_category=content_category,
    )
    return load_artifact(thread_id, run_id, artifact_name, ScriptArtifact), msgs
