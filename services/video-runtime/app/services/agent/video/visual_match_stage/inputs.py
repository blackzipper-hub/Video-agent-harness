from __future__ import annotations
from typing import Any, Dict, List
from app.models.video_state import CharacterProfile
from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json

def export_visual_match_inputs(*, thread_id: str, run_id: str, batch_id: str,
    scenes: List[Dict[str, Any]], characters: List[CharacterProfile], user_input: str = "") -> Dict[str, Any]:
    payload = {
        "artifact": "visual_match_brief", "schema_version": 1, "batch_id": batch_id, "user_input": user_input,
        "characters": [{"index": i + 1, "id": c.id, "name": c.name,
                        "type": getattr(c.type, "value", c.type), "appearance": c.appearance or "",
                        "description": c.description or ""} for i, c in enumerate(characters)],
        "scenes": scenes,
        "expected_scene_numbers": [s.get("scene_number") for s in scenes],
    }
    name = f"visual_match_brief_{batch_id}.json"
    write_input_json(thread_id, run_id, name, payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {"brief": f"{prefix}/inputs/{name}", "artifact_name": f"visual_match_{batch_id}.json",
            "expected_scene_numbers": payload["expected_scene_numbers"], "characters": characters}
