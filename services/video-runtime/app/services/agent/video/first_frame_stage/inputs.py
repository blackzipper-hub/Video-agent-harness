from __future__ import annotations
from typing import Any, Dict, List
from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json

def export_first_frame_inputs(*, thread_id: str, run_id: str, batch_id: str, shots: List[Dict[str, Any]],
    allow_lipsync: bool = True) -> Dict[str, Any]:
    payload = {"artifact": "first_frame_brief", "schema_version": 1, "batch_id": batch_id,
               "allow_lipsync": allow_lipsync, "shots": shots,
               "expected_shot_numbers": [s.get("shot_number") for s in shots]}
    name = f"first_frame_brief_{batch_id}.json"
    write_input_json(thread_id, run_id, name, payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {"brief": f"{prefix}/inputs/{name}", "batch_id": batch_id,
            "artifact_name": f"first_frame_{batch_id}.json",
            "expected_shot_numbers": payload["expected_shot_numbers"]}
