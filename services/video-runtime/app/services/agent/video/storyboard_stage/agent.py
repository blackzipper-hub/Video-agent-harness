"""Storyboard detail deep agent (per batch) — thin runtime; craft in skill + schema descriptions."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.contracts.artifacts.storyboard import ShotDraft, StoryboardAgentDraft, StoryboardArtifact
from app.models.video_state import DetailedShotLLMOutput, ShotLanguage, StoryboardDetailLLMOutput
from app.services.agent.stage_runtime.generic_stage import (
    load_artifact,
    make_write_json_tool,
    run_stage_deep_agent,
)
from prompts.prompt_config import PromptName


def _validate(draft: StoryboardAgentDraft) -> Optional[str]:
    """Structural gates only — denseness explanations live in storyboard-director SKILL.md."""
    nums = [s.shot_number for s in draft.shots]
    if len(nums) != len(set(nums)):
        return "duplicate shot_number"
    for s in draft.shots:
        if not (s.scene_description or "").strip():
            return f"shot {s.shot_number} missing scene_description"
        if s.shot_language is None:
            return f"shot {s.shot_number} missing shot_language"
        if len([b for b in (s.action_beats or []) if str(b).strip()]) < 2:
            return f"shot {s.shot_number} needs ≥2 action_beats"
        if (s.narration or "").strip() and not s.narration_gender:
            return f"shot {s.shot_number} narration set but narration_gender missing (f|m)"
    return None


def _to_detailed_shot(s: ShotDraft) -> DetailedShotLLMOutput:
    """Map draft → DetailedShotLLMOutput. Enums already constrained by schema; no invented beats."""
    sl = s.shot_language
    data = s.model_dump(mode="json")
    data["narration_gender"] = s.narration_gender
    data["shot_language"] = ShotLanguage(
        shot_size=sl.shot_size,
        camera_movement=sl.camera_movement,
        lens_mm=sl.lens_mm,
    )
    data["action_beats"] = list(s.action_beats)
    return DetailedShotLLMOutput.model_validate(data)


async def generate_storyboard_batch_via_deep_agent(
    *,
    thread_id: str,
    run_id: str,
    input_paths: Dict[str, Any],
    detected_language: Optional[str] = None,
) -> Tuple[StoryboardDetailLLMOutput, List[Any]]:
    expected = set(input_paths.get("expected_shot_numbers") or [])
    artifact_name = input_paths["artifact_name"]

    def extra(d: StoryboardAgentDraft) -> Optional[str]:
        err = _validate(d)
        if err:
            return err
        got = {s.shot_number for s in d.shots}
        if expected and got != expected:
            return f"shot_numbers {sorted(got)} != expected {sorted(expected)}"
        return None

    tool = make_write_json_tool(
        thread_id=thread_id,
        run_id=run_id,
        artifact_name=artifact_name,
        draft_model=StoryboardAgentDraft,
        tool_name="write_storyboard_artifact",
        extra_validate=extra,
        stamp={
            "artifact": "storyboard",
            "schema_version": 1,
            "thread_id": thread_id,
            "run_id": run_id,
            "batch_id": input_paths.get("batch_id"),
        },
    )
    msgs = await run_stage_deep_agent(
        stage="storyboard",
        agent_name="storyboard_stage",
        prompt_name=PromptName.VIDEO_STORYBOARD_DETAIL_GENERATION,
        tools=[tool],
        human_text=(
            f"Run storyboard detail stage. Read {input_paths['brief']} with read_file limit=2000. "
            f"Follow storyboard-director. Call write_storyboard_artifact "
            f"(expected shot_numbers={sorted(expected)})."
        ),
        detected_language=detected_language,
        image_urls=input_paths.get("image_urls") or [],
        max_images=8,
        draft_model=StoryboardAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
    )
    art = load_artifact(thread_id, run_id, artifact_name, StoryboardArtifact)
    shots = [_to_detailed_shot(s) for s in art.shots]
    return StoryboardDetailLLMOutput(shots=shots), msgs
