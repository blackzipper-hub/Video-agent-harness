from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json


def export_keyframe_eval_inputs(
    *,
    thread_id: str,
    run_id: str,
    batch_id: str,
    prompts: List[Dict[str, Any]],
    keyframe_prompt_length_range: str = "",
    max_reference_images: int = 8,
    detected_language: Optional[str] = None,
    user_input: str = "",
    image_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    urls = [u for u in (image_urls or []) if u]
    expected = [(p.get("shot_number"), p.get("frame_index", 0)) for p in prompts]
    payload = {
        "artifact": "keyframe_eval_brief",
        "schema_version": 1,
        "batch_id": batch_id,
        "user_input": user_input or "",
        "keyframe_prompt_length_range": keyframe_prompt_length_range or "",
        "max_reference_images": max_reference_images,
        "detected_language": (detected_language or "en").strip() or "en",
        "prompts": prompts,
        "expected_keys": [{"shot_number": a, "frame_index": b} for a, b in expected],
    }
    name = f"keyframe_eval_brief_{batch_id}.json"
    write_input_json(thread_id, run_id, name, payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {
        "brief": f"{prefix}/inputs/{name}",
        "artifact_name": f"keyframe_eval_{batch_id}.json",
        "expected_keys": expected,
        "image_urls": urls,
    }
