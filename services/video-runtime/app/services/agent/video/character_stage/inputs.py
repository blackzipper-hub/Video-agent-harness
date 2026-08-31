"""Export outline/analysis priors for character stage."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.video_state import AudioTranscription, StoryOutline, VideoAnalysisResult
from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json


def export_character_inputs(
    *,
    thread_id: str,
    run_id: str,
    story_outline: StoryOutline,
    analysis: Optional[VideoAnalysisResult] = None,
    image_urls: Optional[List[str]] = None,
    user_input: str = "",
    audio_info: str = "",
    audio_transcription: Optional[AudioTranscription] = None,
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
                    "duration": ch.duration,
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
        "total_duration": story_outline.total_duration,
        "chapters": chapters,
    }
    write_input_json(thread_id, run_id, "outline_brief.json", outline_payload)

    prefix = virtual_run_prefix(thread_id, run_id)
    out: Dict[str, Any] = {
        "outline_brief": f"{prefix}/inputs/outline_brief.json",
        "reference_image_urls": list(image_urls or []),
        "user_input": user_input or "",
        "audio_info": audio_info or "",
    }

    # Unified character brief for skill (user + style + audio)
    hidden_style = ""
    if analysis is not None and getattr(analysis, "hidden_style_description", None):
        hidden_style = (analysis.hidden_style_description or "").strip()

    character_brief = {
        "artifact": "character_brief",
        "schema_version": 1,
        "user_input": user_input or "",
        "style_guidance_for_visual": hidden_style or None,
        "hidden_style_description": hidden_style or None,
        "audio_info": audio_info or "",
        "has_audio": audio_transcription is not None,
        "reference_image_urls": list(image_urls or []),
    }
    write_input_json(thread_id, run_id, "character_brief.json", character_brief)
    out["character_brief"] = f"{prefix}/inputs/character_brief.json"

    if analysis is not None:
        analysis_payload = {
            "artifact": "analysis_brief",
            "schema_version": 1,
            "video_type": analysis.video_type,
            "main_character": analysis.main_character,
            "purpose": analysis.purpose,
            "key_elements": list(analysis.key_elements or []),
            "style_preferences": list(analysis.style_preferences or []),
            "target_audience": analysis.target_audience or "",
            "duration": getattr(analysis, "duration", None),
            "hidden_style_description": hidden_style or None,
            "style_guidance_for_visual": hidden_style or None,
        }
        write_input_json(thread_id, run_id, "analysis_brief.json", analysis_payload)
        out["analysis_brief"] = f"{prefix}/inputs/analysis_brief.json"

    return out
