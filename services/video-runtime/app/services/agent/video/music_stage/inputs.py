from __future__ import annotations
from typing import Any, Dict, Optional
from app.services.agent.stage_runtime.paths import virtual_run_prefix
from app.services.agent.stage_runtime.workspace import write_input_json

def export_music_intent_inputs(*, thread_id: str, run_id: str, user_input: str,
    history_summary: str = "") -> Dict[str, Any]:
    payload = {"artifact": "music_intent_brief", "schema_version": 1, "user_input": user_input or "",
               "allowed_intents": ["lyrics_provided", "auto_lyrics_song", "instrumental_bgm"],
               "history_summary": history_summary or ""}
    write_input_json(thread_id, run_id, "music_intent_brief.json", payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {"brief": f"{prefix}/inputs/music_intent_brief.json", "artifact_name": "music_intent.json"}

def export_music_bgm_inputs(*, thread_id: str, run_id: str, user_input: str,
    target_duration: Optional[float] = None, image_urls: Optional[list] = None) -> Dict[str, Any]:
    urls = [u for u in (image_urls or []) if u]
    payload = {"artifact": "bgm_brief", "schema_version": 1, "user_input": user_input or "",
               "target_duration_seconds": target_duration, "target_instrumental": True,
               "image_urls": urls, "has_images": bool(urls)}
    write_input_json(thread_id, run_id, "bgm_brief.json", payload)
    prefix = virtual_run_prefix(thread_id, run_id)
    return {"brief": f"{prefix}/inputs/bgm_brief.json", "artifact_name": "bgm.json",
            "image_urls": urls}
