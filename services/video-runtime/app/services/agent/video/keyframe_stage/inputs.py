from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json


def export_keyframe_prompt_inputs(
    *,
    thread_id: str,
    run_id: str,
    batch_id: str,
    shots: List[Dict[str, Any]],
    style_guide: str = "",
    user_input: str = "",
    prev_shot: Optional[Dict[str, Any]] = None,
    next_shot: Optional[Dict[str, Any]] = None,
    is_lip_sync_mv: bool = False,
    generate_first_frame: bool = True,
    generate_last_frame: bool = False,
    first_frame_prompts_context: str = "",
    max_reference_images: Optional[int] = None,
) -> Dict[str, Any]:
    """Export facts-only keyframe brief (rules in keyframe-director SKILL.md)."""
    image_urls: List[str] = []
    seen = set()
    for shot in shots:
        for url in shot.get("ref_image_urls") or []:
            if url and url not in seen:
                seen.add(url)
                image_urls.append(url)
        for elem in shot.get("elements_with_images") or []:
            url = elem.get("url")
            if url and url not in seen:
                seen.add(url)
                image_urls.append(url)

    payload = {
        "artifact": "keyframe_brief",
        "schema_version": 1,
        "batch_id": batch_id,
        "style_guide": style_guide,
        "user_input": user_input,
        "is_lip_sync_mv": is_lip_sync_mv,
        "generate_first_frame": generate_first_frame,
        "generate_last_frame": generate_last_frame,
        "first_frame_prompts_context": first_frame_prompts_context or "",
        "max_reference_images": max_reference_images,
        "prev_shot": prev_shot,
        "next_shot": next_shot,
        "shots": shots,
        "expected_shot_numbers": [s.get("shot_number") for s in shots],
        "reference_image_urls": image_urls,
    }
    name = f"keyframe_brief_{batch_id}.json"
    write_input_json(thread_id, run_id, name, payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {
        "brief": f"{prefix}/inputs/{name}",
        "artifact_name": f"keyframes_{batch_id}.json",
        "expected_shot_numbers": payload["expected_shot_numbers"],
        "image_urls": image_urls,
    }
