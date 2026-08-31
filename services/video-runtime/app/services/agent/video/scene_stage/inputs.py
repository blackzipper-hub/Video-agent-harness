"""Export chapter + characters JSON for scene stage."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from app.models.tool_enums import ContentCategory
from app.models.video_state import (
    AudioTranscription,
    CharacterProfile,
    StoryChapter,
    StoryOutline,
)
from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json


def _chapter_audio_segments(
    chapter: StoryChapter,
    audio_transcription: AudioTranscription,
) -> List[Dict[str, Any]]:
    uuid_to_index = {seg.uuid: i for i, seg in enumerate(audio_transcription.segments)}
    out: List[Dict[str, Any]] = []
    for seg_uuid in chapter.audio_segment_ids or []:
        if seg_uuid not in uuid_to_index:
            continue
        idx = uuid_to_index[seg_uuid]
        seg = audio_transcription.segments[idx]
        row: Dict[str, Any] = {
            "index": idx,
            "duration_seconds": round(float(seg.duration), 3),
            "text": seg.text,
        }
        if getattr(seg, "emotion", None) and seg.emotion:
            row["emotion"] = seg.emotion
        if getattr(seg, "tempo", None) and seg.tempo:
            row["tempo"] = seg.tempo
        if getattr(seg, "vocal_gender", None) in ("f", "m"):
            row["vocal_gender"] = "女" if seg.vocal_gender == "f" else "男"
        out.append(row)
    return out


def _scene_structure_for_brief(scene_structure: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for slot in scene_structure:
        row: Dict[str, Any] = {
            "scene_number": int(slot["scene_number"]),
            "audio_segment_index": int(slot["audio_segment_index"]),
            "duration_seconds": round(float(slot["duration"]), 3),
        }
        cue = slot.get("enhancement_cue")
        if isinstance(cue, dict) and (cue.get("description") or "").strip():
            row["enhancement_cue"] = {
                "type": cue.get("type") or "broll",
                "description": cue.get("description"),
                "timestamp_hint": cue.get("timestamp_hint") or "",
            }
        rows.append(row)
    return rows


def _chapter_summary(ch: StoryChapter) -> Dict[str, Any]:
    return {
        "id": ch.id,
        "order": ch.order,
        "title": ch.title,
        "description": ch.description,
        "duration": float(ch.duration or 0),
        "enhancement_cues": [
            c.model_dump() if hasattr(c, "model_dump") else c
            for c in (ch.enhancement_cues or [])
        ],
    }


def export_chapter_scene_inputs(
    *,
    thread_id: str,
    run_id: str,
    story_outline: StoryOutline,
    chapter: StoryChapter,
    characters: List[CharacterProfile],
    user_input: str = "",
    content_category: Optional[str] = None,
    allowed_durations: Optional[List[float]] = None,
    min_video_duration: Optional[float] = None,
    preferred_scene_duration: Optional[float] = None,
    api_min_duration: Optional[float] = None,
    chapter_index: int = 0,
    total_chapters: int = 1,
    reference_image_urls: Optional[List[str]] = None,
    script_sections: Optional[List[Dict[str, Any]]] = None,
    mode: Literal["video_driven", "audio_driven"] = "video_driven",
    audio_transcription: Optional[AudioTranscription] = None,
    scene_structure: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    chars_payload = {
        "artifact": "characters",
        "schema_version": 1,
        "characters": [
            {
                "id": c.id,
                "type": getattr(c.type, "value", c.type) if c.type is not None else "character",
                "name": c.name,
                "description": c.description,
                "appearance": c.appearance or "",
                "personality": c.personality or "",
                "role": c.role or "",
                "style": c.style or "",
                "body_type": getattr(c, "body_type", None) or "",
            }
            for c in characters
        ],
    }
    write_input_json(thread_id, run_id, "characters.json", chars_payload)

    all_chapters = (
        list(story_outline.structure.chapters)
        if story_outline.structure and story_outline.structure.chapters
        else []
    )
    # Resolve index from outline order if possible (more reliable than caller hint)
    resolved_index = chapter_index
    for i, ch in enumerate(all_chapters):
        if ch.id == chapter.id:
            resolved_index = i
            break
    n_total = len(all_chapters) if all_chapters else total_chapters
    prior = [_chapter_summary(ch) for ch in all_chapters[:resolved_index]]
    following = [_chapter_summary(ch) for ch in all_chapters[resolved_index + 1 :]]

    cc = content_category or ContentCategory.DEFAULT.value
    is_audio = mode == "audio_driven" and audio_transcription is not None

    brief: Dict[str, Any] = {
        "artifact": "chapter_brief",
        "schema_version": 1,
        "mode": mode,
        "user_input": user_input or "",
        "content_category": cc,
        "is_lip_sync_mv": cc == ContentCategory.LIP_SYNC_MV.value,
        "story": {
            "title": story_outline.title,
            "description": story_outline.description,
            "key_message": story_outline.key_message,
            "style_guide": story_outline.style_guide,
            "theme": story_outline.theme,
        },
        "chapter": {
            "id": chapter.id,
            "order": chapter.order,
            "index": resolved_index,
            "total_chapters": n_total,
            "title": chapter.title,
            "description": chapter.description,
            "duration": float(chapter.duration),
            "enhancement_cues": [
                c.model_dump() if hasattr(c, "model_dump") else c
                for c in (chapter.enhancement_cues or [])
            ],
        },
        # Sibling outline chapters (text only — not yet-generated scenes).
        # Parallel scene agents cannot see each other's scene outputs; use these
        # so later chapters continue from prior chapter *results*, not cold opens.
        "prior_chapters": prior,
        "following_chapters": following,
        # Duration facts (OM: LLM designs wall-clock; program only clamps):
        # - allowed_durations: API-capable seconds (clamp set)
        # - preferred_scene_duration / min_video_duration: planning prefer (~8), NOT api min
        # - api_min_duration: hard floor for a single generate call (often 3/4)
        "allowed_durations": list(allowed_durations or []),
        "min_video_duration": min_video_duration,
        "preferred_scene_duration": preferred_scene_duration
        if preferred_scene_duration is not None
        else min_video_duration,
        "api_min_duration": api_min_duration,
        "reference_image_urls": list(reference_image_urls or []),
        # OM: scene plan binds to script sections (not invent plot)
        "script_sections_for_chapter": list(script_sections or []),
    }
    if is_audio:
        brief["audio_segments"] = _chapter_audio_segments(chapter, audio_transcription)
        brief["scene_structure"] = _scene_structure_for_brief(scene_structure or [])
    # Per-chapter brief name so parallel chapters don't clobber
    brief_name = f"chapter_brief_{chapter.id}.json"
    write_input_json(thread_id, run_id, brief_name, brief)

    prefix = virtual_run_prefix(thread_id, run_id)
    return {
        "chapter_brief": f"{prefix}/inputs/{brief_name}",
        "characters": f"{prefix}/inputs/characters.json",
        "chapter_id": chapter.id,
        "chapter_duration": float(chapter.duration),
        "artifact_name": f"scenes_{chapter.id}.json",
        "character_ids": [c.id for c in characters],
        "image_urls": list(reference_image_urls or []),
        # Video-driven always binds to script.sections[].id (contract check, not craft).
        "require_script_bind": (not is_audio),
        "allowed_script_section_ids": [
            str(s.get("id") or "").strip()
            for s in (script_sections or [])
            if str(s.get("id") or "").strip()
        ],
        "mode": mode,
        "scene_structure": list(scene_structure or []) if is_audio else None,
    }
