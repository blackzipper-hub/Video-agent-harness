"""Export storyboard batch inputs."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.video_state import CharacterProfile, StoryOutline, StoryboardScene, VideoAnalysisResult
from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json


def export_storyboard_batch_inputs(
    *,
    thread_id: str,
    run_id: str,
    batch_id: str,
    story_outline: StoryOutline,
    scenes: List[StoryboardScene],
    characters: List[CharacterProfile],
    analysis: Optional[VideoAnalysisResult] = None,
    user_input: str = "",
    prev_scenes: Optional[List[StoryboardScene]] = None,
    next_scenes: Optional[List[StoryboardScene]] = None,
    is_lip_sync_mv: bool = False,
    is_product_launch: bool = False,
    reference_image_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    def _s(sc: StoryboardScene) -> dict:
        return {
            "scene_number": sc.scene_number,
            "title": sc.title,
            "description": sc.description,
            "duration": sc.duration,
            "character_ids": list(sc.character_ids or []),
            "character_action": sc.character_action,
            "camera_angle": sc.camera_angle,
            "visual_style": getattr(sc, "visual_style", None) or "",
            "transition_style": getattr(sc, "transition_style", None) or "",
            "chapter_id": sc.chapter_id,
            "generation_mode": sc.generation_mode,
        }

    hidden_style = ""
    if analysis is not None and getattr(analysis, "hidden_style_description", None):
        hidden_style = (analysis.hidden_style_description or "").strip()

    payload = {
        "artifact": "storyboard_brief",
        "schema_version": 1,
        "batch_id": batch_id,
        "user_input": user_input or "",
        "is_lip_sync_mv": is_lip_sync_mv,
        "is_product_launch": is_product_launch,
        "style_guidance_for_visual": hidden_style or None,
        "story": {
            "title": story_outline.title,
            "theme": story_outline.theme,
            "description": story_outline.description,
            "style_guide": story_outline.style_guide,
            "key_message": story_outline.key_message,
            "total_duration": story_outline.total_duration,
        },
        "analysis": None
        if analysis is None
        else {
            "video_type": analysis.video_type,
            "main_character": analysis.main_character,
            "purpose": analysis.purpose,
            "key_elements": list(analysis.key_elements or []),
            "style_preferences": list(analysis.style_preferences or []),
            "target_audience": analysis.target_audience or "",
            "hidden_style_description": hidden_style or None,
            "style_guidance_for_visual": hidden_style or None,
        },
        "characters": [
            {
                "id": c.id,
                "name": c.name,
                "type": getattr(c.type, "value", c.type),
                "description": c.description or "",
                "appearance": c.appearance or "",
                "personality": c.personality or "",
                "role": c.role or "",
                "style": c.style or "",
                "body_type": c.body_type or "",
            }
            for c in characters
        ],
        "scenes": [_s(s) for s in scenes],
        "prev_scenes": [_s(s) for s in (prev_scenes or [])],
        "next_scenes": [_s(s) for s in (next_scenes or [])],
        "expected_shot_numbers": [s.scene_number for s in scenes],
        "reference_image_urls": list(reference_image_urls or []),
    }
    name = f"storyboard_brief_{batch_id}.json"
    write_input_json(thread_id, run_id, name, payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {
        "brief": f"{prefix}/inputs/{name}",
        "batch_id": batch_id,
        "artifact_name": f"storyboard_{batch_id}.json",
        "expected_shot_numbers": payload["expected_shot_numbers"],
        "image_urls": list(reference_image_urls or []),
    }
