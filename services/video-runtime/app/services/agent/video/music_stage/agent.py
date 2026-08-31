from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from app.contracts.artifacts.music import MusicBgmAgentDraft, MusicBgmArtifact, MusicIntentAgentDraft, MusicIntentArtifact
from app.services.agent.stage_runtime.generic_stage import load_artifact, make_write_json_tool, run_stage_deep_agent
from prompts.prompt_config import PromptName

async def generate_music_intent_via_deep_agent(*, thread_id: str, run_id: str, input_paths: Dict[str, Any],
    detected_language: Optional[str] = None) -> Tuple[MusicIntentArtifact, List[Any]]:
    artifact_name = input_paths["artifact_name"]
    tool = make_write_json_tool(
        thread_id=thread_id, run_id=run_id, artifact_name=artifact_name,
        draft_model=MusicIntentAgentDraft, tool_name="write_music_artifact",
        stamp={"artifact": "music_intent", "schema_version": 1, "thread_id": thread_id, "run_id": run_id},
    )
    msgs = await run_stage_deep_agent(
        stage="music", agent_name="music_stage",
        prompt_name=PromptName.VIDEO_MUSIC_INTENT_ANALYSIS, tools=[tool],
        human_text=(f"Run music intent analysis. Read {input_paths['brief']}. "
                    f"Follow music-director. Call write_music_artifact."),
        detected_language=detected_language,
        draft_model=MusicIntentAgentDraft, artifact_name=artifact_name,
        thread_id=thread_id, run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, MusicIntentArtifact), msgs

async def generate_music_bgm_prompt_via_deep_agent(*, thread_id: str, run_id: str, input_paths: Dict[str, Any],
    detected_language: Optional[str] = None) -> Tuple[MusicBgmArtifact, List[Any]]:
    artifact_name = input_paths["artifact_name"]
    def extra(d: MusicBgmAgentDraft) -> Optional[str]:
        if not (d.suno_prompt or "").strip():
            return "suno_prompt required"
        return None
    tool = make_write_json_tool(
        thread_id=thread_id, run_id=run_id, artifact_name=artifact_name,
        draft_model=MusicBgmAgentDraft, tool_name="write_music_bgm_artifact",
        extra_validate=extra,
        stamp={"artifact": "bgm", "schema_version": 1, "thread_id": thread_id, "run_id": run_id},
    )
    msgs = await run_stage_deep_agent(
        stage="music_bgm", agent_name="music_bgm_stage",
        prompt_name=PromptName.VIDEO_MUSIC_BGM_GENERATION, tools=[tool],
        human_text=(f"Draft instrumental BGM Suno prompt. Read {input_paths['brief']}. "
                    f"Follow music-bgm-director. Call write_music_bgm_artifact. Do not call music APIs."),
        detected_language=detected_language,
        image_urls=input_paths.get("image_urls"),
        draft_model=MusicBgmAgentDraft, artifact_name=artifact_name,
        thread_id=thread_id, run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, MusicBgmArtifact), msgs
