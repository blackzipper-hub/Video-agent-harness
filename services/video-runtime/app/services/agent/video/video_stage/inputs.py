from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json

_PLACE_PREFS = ("学院大厅", "古堡大厅", "宴会厅", "宫廷大厅", "地牢", "教室", "走廊", "大厅")
_PLACE_RE = re.compile(
    r"(大厅|学院|地牢|深渊|教室|走廊|宴会|街|店|宫廷|神殿|礼堂|广场|天台)"
)


def _continuity_place(shots: List[Dict[str, Any]]) -> str:
    """Story-level place noun for shots whose scene_description omits location."""
    for s in shots:
        blob = str(s.get("scene_description") or "")
        for pref in _PLACE_PREFS:
            if pref in blob:
                return pref
        m = _PLACE_RE.search(blob)
        if m:
            return m.group(1)
    return ""


def export_video_prompt_inputs(
    *,
    thread_id: str,
    run_id: str,
    batch_id: str,
    shots: List[Dict[str, Any]],
    style_guide: str = "",
    user_input: str = "",
    keyframe_image_urls: Optional[List[str]] = None,
    prev_shot: Optional[Dict[str, Any]] = None,
    next_shot: Optional[Dict[str, Any]] = None,
    is_lip_sync_mv: bool = False,
    has_lipsync_shots: bool = False,
) -> Dict[str, Any]:
    """Export facts-only video brief (rules in video-director SKILL.md)."""
    image_urls = [u for u in (keyframe_image_urls or []) if u]
    expected = [s.get("shot_number") for s in shots]
    dialogue_by_shot = {
        int(s["shot_number"]): str(s.get("dialogue") or "")
        for s in shots
        if s.get("shot_number") is not None
    }
    cont = _continuity_place(shots)
    # Prefer prev_shot place when batch is mid-sequence and batch shots omit place.
    if not cont and prev_shot:
        cont = _continuity_place([prev_shot])
    payload = {
        "artifact": "video_brief",
        "schema_version": 1,
        "batch_id": batch_id,
        "style_guide": style_guide,
        "user_input": user_input,
        "is_lip_sync_mv": is_lip_sync_mv,
        "has_lipsync_shots": has_lipsync_shots,
        "prev_shot": prev_shot,
        "next_shot": next_shot,
        "shots": shots,
        "keyframe_image_urls": image_urls,
        "expected_shot_numbers": expected,
        "dialogue_by_shot": dialogue_by_shot,
        "continuity_place": cont,
    }
    name = f"video_brief_{batch_id}.json"
    write_input_json(thread_id, run_id, name, payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {
        "brief": f"{prefix}/inputs/{name}",
        "artifact_name": f"video_prompts_{batch_id}.json",
        "expected_shot_numbers": expected,
        "dialogue_by_shot": dialogue_by_shot,
        "image_urls": image_urls,
    }
