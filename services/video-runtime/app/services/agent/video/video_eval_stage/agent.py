from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.contracts.artifacts.video_eval import VideoEvalAgentDraft, VideoEvalArtifact
from app.services.agent.stage_runtime.generic_stage import (
    load_artifact,
    make_write_json_tool,
    run_stage_deep_agent,
)
from prompts.prompt_config import PromptName


async def evaluate_video_prompts_via_deep_agent(
    *,
    thread_id: str,
    run_id: str,
    input_paths: Dict[str, Any],
    detected_language: Optional[str] = None,
) -> Tuple[VideoEvalArtifact, List[Any]]:
    expected = set(input_paths.get("expected_shot_numbers") or [])
    artifact_name = input_paths["artifact_name"]

    def extra(d: VideoEvalAgentDraft) -> Optional[str]:
        got = {e.shot_number for e in d.evaluations}
        if expected and got != expected:
            return f"shot_numbers {sorted(got)} != expected {sorted(expected)}"
        for e in d.evaluations:
            if not (e.fixed_prompt or "").strip():
                return f"shot {e.shot_number} empty fixed_prompt"
        return None

    tool = make_write_json_tool(
        thread_id=thread_id,
        run_id=run_id,
        artifact_name=artifact_name,
        draft_model=VideoEvalAgentDraft,
        tool_name="write_video_eval_artifact",
        extra_validate=extra,
        stamp={
            "artifact": "video_eval",
            "schema_version": 1,
            "thread_id": thread_id,
            "run_id": run_id,
        },
    )
    msgs = await run_stage_deep_agent(
        stage="video_eval",
        agent_name="video_eval_stage",
        prompt_name=PromptName.VIDEO_VIDEO_GENERATION_PROMPT_EVALUATION_FIX,
        tools=[tool],
        human_text=(
            f"Read brief. Follow video-eval-director. Call write_video_eval_artifact. "
            f"Read {input_paths['brief']} with read_file limit=2000. "
            f"shot_numbers={sorted(expected)}."
        ),
        detected_language=detected_language,
        image_urls=input_paths.get("image_urls") or [],
        max_images=48,
        draft_model=VideoEvalAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, VideoEvalArtifact), msgs
