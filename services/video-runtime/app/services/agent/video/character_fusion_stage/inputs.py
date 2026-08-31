from __future__ import annotations
from typing import Any, Dict, List
from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json

def export_fusion_plan_inputs(*, thread_id: str, run_id: str, scenes: List[Dict[str, Any]],
    characters: List[Dict[str, Any]], model_limit: int = 4) -> Dict[str, Any]:
    payload = {"artifact": "fusion_brief", "schema_version": 1, "model_limit": model_limit,
               "scenes": scenes, "characters": characters}
    write_input_json(thread_id, run_id, "fusion_brief.json", payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {"brief": f"{prefix}/inputs/fusion_brief.json", "artifact_name": "fusion.json"}
