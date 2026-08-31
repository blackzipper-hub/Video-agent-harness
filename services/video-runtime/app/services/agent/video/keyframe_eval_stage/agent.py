from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.contracts.artifacts.keyframe_eval import KeyframeEvalAgentDraft, KeyframeEvalArtifact
from app.services.agent.stage_runtime.generic_stage import (
    load_artifact,
    make_write_json_tool,
    run_stage_deep_agent,
)
from prompts.prompt_config import PromptName


async def evaluate_keyframe_prompts_via_deep_agent(
    *,
    thread_id: str,
    run_id: str,
    input_paths: Dict[str, Any],
    detected_language: Optional[str] = None,
) -> Tuple[KeyframeEvalArtifact, List[Any]]:
    expected = {(a, b) for a, b in (input_paths.get("expected_keys") or [])}
    artifact_name = input_paths["artifact_name"]

    def extra(d: KeyframeEvalAgentDraft) -> Optional[str]:
        got = {(f.shot_number, f.frame_index) for f in d.fixes}
        if expected and got != expected:
            return f"keys {sorted(got)} != expected {sorted(expected)}"
        for f in d.fixes:
            if not (f.fixed_prompt or "").strip():
                return f"shot {f.shot_number} frame {f.frame_index} empty fixed_prompt"
        return None

    tool = make_write_json_tool(
        thread_id=thread_id,
        run_id=run_id,
        artifact_name=artifact_name,
        draft_model=KeyframeEvalAgentDraft,
        tool_name="write_keyframe_eval_artifact",
        extra_validate=extra,
        stamp={
            "artifact": "keyframe_eval",
            "schema_version": 1,
            "thread_id": thread_id,
            "run_id": run_id,
        },
    )
    msgs = await run_stage_deep_agent(
        stage="keyframe_eval",
        agent_name="keyframe_eval_stage",
        prompt_name=PromptName.VIDEO_KEYFRAME_GENERATION_PROMPT_EVALUATION_FIX,
        tools=[tool],
        human_text=(
            f"Run keyframe prompt eval/fix. Read {input_paths['brief']} with read_file limit=2000. "
            f"Follow keyframe-eval-director. Call write_keyframe_eval_artifact."
        ),
        detected_language=detected_language,
        image_urls=input_paths.get("image_urls") or [],
        max_images=48,
        draft_model=KeyframeEvalAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
    )
    return load_artifact(thread_id, run_id, artifact_name, KeyframeEvalArtifact), msgs
