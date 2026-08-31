from __future__ import annotations
from typing import Any, Dict, List
from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json

def export_narration_plan_inputs(*, thread_id: str, run_id: str, shots: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = {"artifact": "narration_brief", "schema_version": 1, "shots": shots}
    write_input_json(thread_id, run_id, "narration_brief.json", payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {"brief": f"{prefix}/inputs/narration_brief.json", "artifact_name": "narration.json"}
