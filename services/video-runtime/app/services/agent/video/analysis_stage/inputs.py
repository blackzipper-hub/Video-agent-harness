"""Export user/audio priors for analysis stage."""
from __future__ import annotations

from typing import Any, List, Optional

from app.models.video_state import AudioTranscription
from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json
from app.services.agent.utils.database_utils import build_audio_context_for_prompts


def export_analysis_inputs(
    *,
    thread_id: str,
    run_id: str,
    user_input: str,
    images: Optional[List[Any]] = None,
    audio_transcription: Optional[AudioTranscription] = None,
    sections: Optional[List[Any]] = None,
    target_duration: Optional[float] = None,
    user_content_category: Optional[str] = None,
    available_style_categories: Optional[List[str]] = None,
    history_summary: str = "",
) -> dict:
    image_urls = []
    if images:
        image_urls = [img.url for img in images if getattr(img, "url", None)]

    brief = {
        "artifact": "user_brief",
        "schema_version": 1,
        "user_input": user_input or "",
        "user_content_category": user_content_category or "",
        "target_duration_seconds": float(target_duration) if target_duration else None,
        "has_audio": audio_transcription is not None,
        "available_style_categories": list(available_style_categories or []),
        "reference_image_urls": image_urls,
        "content_category_allowed": [
            "Default",
            "Lip-Sync MV",
            "Product Launch",
            "Short Drama",
        ],
        "history_summary": history_summary or "",
    }
    write_input_json(thread_id, run_id, "user_brief.json", brief)
    prefix = virtual_run_prefix(thread_id, run_id)
    out: dict = {
        "user_brief": f"{prefix}/inputs/user_brief.json",
        "reference_image_urls": image_urls,
        "target_duration_seconds": brief["target_duration_seconds"],
        "content_category": user_content_category or "",
    }

    if audio_transcription is not None:
        ctx = build_audio_context_for_prompts(audio_transcription, sections)
        audio_payload = {
            "artifact": "audio_context",
            "schema_version": 1,
            "duration": float(audio_transcription.duration or 0),
            "language": getattr(audio_transcription, "language", "") or "",
            "is_instrumental": bool(getattr(audio_transcription, "is_instrumental", False)),
            "text": getattr(audio_transcription, "text", "") or "",
            "global_block": ctx.get("global_block") or "",
            "sections_with_segments_block": ctx.get("sections_with_segments_block") or "",
            "segments_block": ctx.get("segments_block") or "",
        }
        write_input_json(thread_id, run_id, "audio_context.json", audio_payload)
        out["audio_context"] = f"{prefix}/inputs/audio_context.json"
        out["target_duration_seconds"] = float(audio_transcription.duration or 0)

    return out
