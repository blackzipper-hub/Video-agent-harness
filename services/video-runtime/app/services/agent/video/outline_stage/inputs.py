"""Export analysis / audio priors into run workspace inputs/."""
from __future__ import annotations

from typing import Any, List, Optional

from app.agent_config.duration import get_video_driven_duration_values, preferred_planning_unit
from app.models.user_options import UserOption
from app.models.video_state import (
    AudioTranscription,
    ContentCategory,
    UserInput,
    VideoAnalysisResult,
)
from app.contracts.artifacts.analysis_brief import AnalysisBriefArtifact, AudioContextArtifact
from app.services.agent.utils.database_utils import build_audio_context_for_prompts

from ...stage_runtime.paths import virtual_run_prefix
from ...stage_runtime.workspace import write_input_json

# Keep in sync with outline_generation_service.MAX_CHAPTER_DURATION_SECONDS
MAX_CHAPTER_DURATION_SECONDS = 50


def build_analysis_brief(
    *,
    user_input_data: Optional[UserInput],
    analysis_data: VideoAnalysisResult,
    audio_transcription: Optional[AudioTranscription],
    user_option: Optional[UserOption],
) -> AnalysisBriefArtifact:
    cc = (
        user_input_data.user_option.content_category
        if (user_input_data and user_input_data.user_option)
        else None
    )
    content_category = (cc.value if cc else "") or ContentCategory.DEFAULT.value
    is_audio = audio_transcription is not None
    mode = "audio_driven" if is_audio else "video_driven"
    target = (
        float(audio_transcription.duration)
        if is_audio
        else float(analysis_data.duration or 0)
    )
    image_urls: List[str] = []
    if user_input_data and user_input_data.images:
        image_urls = [img.url for img in user_input_data.images if getattr(img, "url", None)]

    # research + proposal folded into analysis.extra → brief for outline/script
    analysis_extra = dict(getattr(analysis_data, "extra", None) or {})
    brief = AnalysisBriefArtifact(
        mode=mode,  # type: ignore[arg-type]
        content_category=content_category,
        user_input=(user_input_data.user_input if user_input_data else "") or "",
        target_duration_seconds=target,
        main_character=analysis_data.main_character or "",
        purpose=analysis_data.purpose or "",
        key_elements=list(analysis_data.key_elements or []),
        video_type=getattr(analysis_data, "video_type", "") or "",
        target_audience=getattr(analysis_data, "target_audience", "") or "",
        style_preferences=list(analysis_data.style_preferences or []),
        style_guidance_for_visual=(analysis_data.hidden_style_description or "").strip() or None,
        reference_image_urls=image_urls,
        extra=analysis_extra,
    )

    if not is_audio:
        from app.services.agent.stage_runtime.paths import is_story_narrative_category

        # Story narrative (Default / Short Drama): no recommended_chapters pressure.
        # Product Launch / Lip-Sync MV still get duration-bucket hints.
        if not is_story_narrative_category(content_category):
            video_durations = get_video_driven_duration_values(user_option)
            planning_unit = preferred_planning_unit(video_durations)
            effective_min = int(planning_unit)
            max_chapters = max(1, int(target // effective_min)) if target > 0 else 1
            recommended = max(
                1,
                min(max_chapters, max(1, int(round(target / max(effective_min * 2, 1))))),
            )
            brief.max_chapters = max_chapters
            brief.recommended_chapters = recommended
            brief.min_chapter_duration = effective_min
            brief.max_chapter_duration = min(
                MAX_CHAPTER_DURATION_SECONDS,
                int(target) if target > 0 else MAX_CHAPTER_DURATION_SECONDS,
            )
            brief.min_video_duration = int(planning_unit)

    return brief


def export_outline_inputs(
    *,
    thread_id: str,
    run_id: str,
    user_input_data: Optional[UserInput],
    analysis_data: VideoAnalysisResult,
    audio_transcription: Optional[AudioTranscription],
    sections: Optional[List[Any]],
    user_option: Optional[UserOption],
) -> dict:
    """Write inputs/*.json; return virtual paths for the agent."""
    brief = build_analysis_brief(
        user_input_data=user_input_data,
        analysis_data=analysis_data,
        audio_transcription=audio_transcription,
        user_option=user_option,
    )
    write_input_json(thread_id, run_id, "analysis_brief.json", brief.model_dump(mode="json"))

    prefix = virtual_run_prefix(thread_id, run_id)
    out = {
        "analysis_brief": f"{prefix}/inputs/analysis_brief.json",
        "audio_context": None,
        "reference_image_urls": list(brief.reference_image_urls),
        "mode": brief.mode,
        "target_duration_seconds": brief.target_duration_seconds,
        "content_category": brief.content_category,
    }

    if audio_transcription is not None:
        ctx = build_audio_context_for_prompts(audio_transcription, sections)
        audio_art = AudioContextArtifact(
            audio_duration=float(audio_transcription.duration or 0),
            total_segments=len(audio_transcription.segments or []),
            music_line_global=ctx.get("global_block") or "",
            sections_info=(ctx.get("sections_with_segments_block") or ctx.get("sections_block") or "").strip(),
            segments_info=ctx.get("segments_block") or "",
            section_count=len(sections) if sections else None,
        )
        write_input_json(thread_id, run_id, "audio_context.json", audio_art.model_dump(mode="json"))
        out["audio_context"] = f"{prefix}/inputs/audio_context.json"

    return out
