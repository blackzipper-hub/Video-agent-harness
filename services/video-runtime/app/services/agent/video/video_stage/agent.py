"""Video prompt stage — thin runtime; craft rules in video-director skill."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.contracts.artifacts.video import VideoAgentDraft, VideoArtifact
from app.services.agent.stage_runtime.generic_stage import (
    load_artifact,
    make_write_json_tool,
    run_stage_deep_agent,
)
from prompts.prompt_config import PromptName


async def generate_video_prompts_via_deep_agent(
    *,
    thread_id: str,
    run_id: str,
    input_paths: Dict[str, Any],
    detected_language: Optional[str] = None,
) -> Tuple[VideoArtifact, List[Any]]:
    expected = set(input_paths.get("expected_shot_numbers") or [])
    artifact_name = input_paths["artifact_name"]

    def extra(d: VideoAgentDraft) -> Optional[str]:
        # Structural only — opener / beats / contact / ban live in video-director SKILL.md
        got = {p.shot_number for p in d.prompts}
        if expected and got != expected:
            return f"shot_numbers {sorted(got)} != expected {sorted(expected)}"
        for p in d.prompts:
            if not (p.i2v_prompt or "").strip():
                return f"shot {p.shot_number} empty i2v_prompt"
        return None

    tool = make_write_json_tool(
        thread_id=thread_id,
        run_id=run_id,
        artifact_name=artifact_name,
        draft_model=VideoAgentDraft,
        tool_name="write_video_artifact",
        extra_validate=extra,
        stamp={
            "artifact": "video_prompts",
            "schema_version": 1,
            "thread_id": thread_id,
            "run_id": run_id,
        },
    )
    msgs = await run_stage_deep_agent(
        stage="video",
        agent_name="video_stage",
        prompt_name=PromptName.VIDEO_VIDEO_GENERATION_PROMPT_BATCH_GENERATION,
        tools=[tool],
        human_text=(
            f"Run video prompt stage. Read {input_paths['brief']} with read_file limit=2000. "
            f"Follow video-director. Call write_video_artifact for "
            f"shot_numbers={sorted(expected)}."
        ),
        detected_language=detected_language,
        image_urls=input_paths.get("image_urls") or [],
        max_images=24,
        draft_model=VideoAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, VideoArtifact), msgs
