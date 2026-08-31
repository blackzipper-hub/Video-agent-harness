from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json


def export_video_eval_inputs(
    *,
    thread_id: str,
    run_id: str,
    batch_id: str,
    prompts: List[Dict[str, Any]],
    detected_language: Optional[str] = None,
    has_lipsync_shots: bool = False,
    image_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Export eval brief. image_urls: per-shot start then optional end, in prompt order."""
    urls = [u for u in (image_urls or []) if u]
    payload = {
        "artifact": "video_eval_brief",
        "schema_version": 1,
        "batch_id": batch_id,
        "detected_language": (detected_language or "en").strip() or "en",
        "has_lipsync_shots": has_lipsync_shots,
        "prompts": prompts,
        "expected_shot_numbers": [p.get("shot_number") for p in prompts],
    }
    name = f"video_eval_brief_{batch_id}.json"
    write_input_json(thread_id, run_id, name, payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {
        "brief": f"{prefix}/inputs/{name}",
        "artifact_name": f"video_eval_{batch_id}.json",
        "expected_shot_numbers": payload["expected_shot_numbers"],
        "image_urls": urls,
    }
