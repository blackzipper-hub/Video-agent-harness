from __future__ import annotations

from typing import Any, Dict, List

from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json


def export_keyframe_reflection_inputs(
    *,
    thread_id: str,
    run_id: str,
    batch_id: str,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Export reflection brief. Each item may include character_ref_images + keyframe_url."""
    image_urls: List[str] = []
    seen = set()
    for item in items:
        for ref in item.get("character_ref_images") or []:
            url = ref.get("url") if isinstance(ref, dict) else None
            if url and url not in seen:
                seen.add(url)
                image_urls.append(url)
        kf = item.get("keyframe_url")
        if kf and kf not in seen:
            seen.add(kf)
            image_urls.append(kf)

    payload = {
        "artifact": "keyframe_reflection_brief",
        "schema_version": 1,
        "batch_id": batch_id,
        "items": items,
        "expected_shot_numbers": [i.get("shot_number") for i in items],
        "reference_image_urls": image_urls,
    }
    name = f"keyframe_reflection_brief_{batch_id}.json"
    write_input_json(thread_id, run_id, name, payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {
        "brief": f"{prefix}/inputs/{name}",
        "artifact_name": f"keyframe_reflection_{batch_id}.json",
        "expected_shot_numbers": payload["expected_shot_numbers"],
        "image_urls": image_urls,
    }
