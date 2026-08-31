from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json


def export_video_consistency_inputs(
    *,
    thread_id: str,
    run_id: str,
    i2v_prompt: str,
    character_ref_labels_text: str = "",
    image_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    urls = [u for u in (image_urls or []) if u]
    payload = {
        "artifact": "video_consistency_brief",
        "schema_version": 1,
        "i2v_prompt": i2v_prompt or "",
        "character_ref_labels_text": character_ref_labels_text or "",
    }
    name = "video_consistency_brief.json"
    write_input_json(thread_id, run_id, name, payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {
        "brief": f"{prefix}/inputs/{name}",
        "artifact_name": "video_consistency.json",
        "image_urls": urls,
    }
