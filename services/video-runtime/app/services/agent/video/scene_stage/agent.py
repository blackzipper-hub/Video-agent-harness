"""Scene stage deep agent — thin runtime; craft rules live in scene-director skill.

description = user-facing visual prose.
Typed craft lives in additional_data (OM-aligned). No LABEL: dumps in description.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.contracts.artifacts.scene import ScenesAgentDraft, ScenesArtifact
from app.models.video_state import StoryboardScene
from app.services.agent.stage_runtime.generic_stage import (
    load_artifact,
    make_write_json_tool,
    run_stage_deep_agent,
)
from prompts.prompt_config import PromptName

logger = logging.getLogger(__name__)

# Internal labels that must NOT appear in user-facing description
_DESC_LABEL_RE = re.compile(
    r"(?m)^\s*("
    r"NARRATIVE_ROLE|INFORMATION_ROLE|SHOT_INTENT|SCRIPT_SECTION|"
    r"DIALOGUE|BEATS|SUBJECT_MOTION|SUBJECT|SPATIAL|CAMERA|OVERLAYS|"
    r"SCENE\s*:"
    r")\s*",
    re.I,
)


def _validate_scenes(
    draft: ScenesAgentDraft,
    *,
    chapter_id: str,
    chapter_duration: float,
    known_character_ids: List[str],
    require_script_bind: bool,
    allowed_script_section_ids: Optional[List[str]] = None,
    scene_structure: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """Schema/tool gate only — denseness rationale belongs in SKILL.md."""
    known = set(known_character_ids)
    allowed_ids = {str(x).strip() for x in (allowed_script_section_ids or []) if str(x).strip()}
    dur_sum = sum(float(s.duration or 0) for s in draft.scenes)
    if abs(dur_sum - float(chapter_duration)) > 0.6:
        return f"scene duration sum={dur_sum} != chapter_duration={chapter_duration}"

    if scene_structure:
        if len(draft.scenes) != len(scene_structure):
            return (
                f"audio mode expects {len(scene_structure)} scenes "
                f"(precomputed structure), got {len(draft.scenes)}"
            )
        for s, slot in zip(draft.scenes, scene_structure):
            exp_dur = float(slot["duration"])
            if abs(float(s.duration or 0) - exp_dur) > 0.15:
                return (
                    f"scene {s.scene_number} duration must be {exp_dur}s "
                    f"(precomputed structure)"
                )
            exp_sn = int(slot["scene_number"])
            if int(s.scene_number) != exp_sn:
                return f"scene_number must be {exp_sn} for audio slot (got {s.scene_number})"

    for s in draft.scenes:
        if float(s.duration or 0) <= 0:
            return f"scene {s.scene_number} missing positive duration"
        s.chapter_id = chapter_id
        for cid in s.character_ids or []:
            if known and cid not in known:
                return (
                    f"scene {s.scene_number} unknown character_id={cid}; "
                    f"must copy from characters.json"
                )

        desc = (s.description or "").strip()
        if len(desc) < 20:
            return f"scene {s.scene_number} description too short (user-facing visual prose)"
        if _DESC_LABEL_RE.search(desc):
            return (
                f"scene {s.scene_number} description must be user-facing prose only — "
                f"put NARRATIVE_ROLE/DIALOGUE/BEATS/SUBJECT/… in additional_data, not description"
            )

        if s.additional_data is None:
            return f"scene {s.scene_number} needs additional_data (ScenePlanMeta)"
        meta = s.additional_data
        if require_script_bind:
            sid = (meta.script_section_id or "").strip()
            if not sid:
                return f"scene {s.scene_number} additional_data.script_section_id required"
            if sid.lower().startswith("none"):
                return (
                    f"scene {s.scene_number} script_section_id={sid!r} invalid "
                    f"(must be a real script.sections[].id)"
                )
            if allowed_ids and sid not in allowed_ids:
                return (
                    f"scene {s.scene_number} script_section_id={sid!r} not in "
                    f"script.sections ids={sorted(allowed_ids)}"
                )
        if not (meta.narrative_role or "").strip():
            return f"scene {s.scene_number} additional_data.narrative_role required"
        if not (meta.shot_intent or "").strip():
            return f"scene {s.scene_number} additional_data.shot_intent required"
        if not (meta.information_role or "").strip():
            return f"scene {s.scene_number} additional_data.information_role required"
        if not (meta.framing or "").strip() or (
            "→" not in meta.framing and "->" not in meta.framing
        ):
            return f"scene {s.scene_number} framing must show change (e.g. OTS → CU)"
        if not (meta.subject or "").strip():
            return f"scene {s.scene_number} additional_data.subject required"
        if not (meta.subject_motion or "").strip():
            return f"scene {s.scene_number} additional_data.subject_motion required"
        beats = [b for b in (meta.beats or []) if str(b).strip()]
        if len(beats) < 2:
            return f"scene {s.scene_number} additional_data.beats needs ≥2 timed beats"
        sl = meta.shot_language
        if sl is None or not (sl.shot_size and sl.camera_movement):
            return f"scene {s.scene_number} shot_language needs shot_size + camera_movement"
        if sl.lens_mm is None or not (sl.lighting_key or "").strip():
            return f"scene {s.scene_number} shot_language needs lens_mm + lighting_key"

        # Craft density (contact verbs, subject specificity, UI bans) → scene-director SKILL.md
        if (meta.dialogue or "").strip() and not (s.character_ids or []):
            return f"scene {s.scene_number} has dialogue but empty character_ids"
        # Coerce aliases (video/i2v → normal) so write succeeds; skill teaches canonical set.
        from app.services.agent.video.generation_mode_normalize import coerce_generation_mode

        s.generation_mode = coerce_generation_mode(s.generation_mode)
    return None


def artifact_to_storyboard_scenes(art: ScenesArtifact) -> List[StoryboardScene]:
    from app.services.agent.video.generation_mode_normalize import coerce_generation_mode

    out: List[StoryboardScene] = []
    for s in art.scenes:
        meta = s.additional_data
        ad = meta.model_dump(mode="json") if meta is not None else None
        # Prefer typed motion for character_action if empty
        action = (s.character_action or "").strip()
        if not action and meta is not None:
            action = (meta.subject_motion or "").strip()
        out.append(
            StoryboardScene(
                scene_number=int(s.scene_number),
                title=s.title,
                description=s.description,
                duration=float(s.duration),
                camera_angle=s.camera_angle or "",
                character_action=action,
                visual_style=s.visual_style or "",
                transition_style=s.transition_style or "cut",
                is_bridge=bool(s.is_bridge),
                character_ids=list(s.character_ids or []),
                chapter_id=s.chapter_id or art.chapter_id,
                generation_mode=coerce_generation_mode(s.generation_mode),
                additional_data=ad,
            )
        )
    return out


async def generate_scenes_for_chapter_via_deep_agent(
    *,
    thread_id: str,
    run_id: str,
    input_paths: Dict[str, Any],
    detected_language: Optional[str] = None,
) -> Tuple[List[StoryboardScene], List[Any]]:
    chapter_id = input_paths["chapter_id"]
    chapter_duration = float(input_paths["chapter_duration"])
    artifact_name = input_paths["artifact_name"]
    require_script_bind = bool(input_paths.get("require_script_bind"))
    allowed_script_section_ids = list(input_paths.get("allowed_script_section_ids") or [])
    known_ids = list(input_paths.get("character_ids") or [])
    mode = input_paths.get("mode") or "video_driven"
    scene_structure = input_paths.get("scene_structure")
    is_audio = mode == "audio_driven"

    def extra(d: ScenesAgentDraft) -> Optional[str]:
        return _validate_scenes(
            d,
            chapter_id=chapter_id,
            chapter_duration=chapter_duration,
            known_character_ids=known_ids,
            require_script_bind=require_script_bind,
            allowed_script_section_ids=allowed_script_section_ids,
            scene_structure=scene_structure if is_audio else None,
        )

    tool = make_write_json_tool(
        thread_id=thread_id,
        run_id=run_id,
        artifact_name=artifact_name,
        draft_model=ScenesAgentDraft,
        tool_name="write_scenes_artifact",
        extra_validate=extra,
        stamp={
            "artifact": "scenes",
            "schema_version": 1,
            "version": "1.0",
            "thread_id": thread_id,
            "run_id": run_id,
            "chapter_id": chapter_id,
        },
    )
    prompt_name = (
        PromptName.VIDEO_SCENE_GENERATION_AUDIO_DRIVEN
        if is_audio
        else PromptName.VIDEO_SCENE_GENERATION_VIDEO_DRIVEN
    )
    msgs = await run_stage_deep_agent(
        stage="scene",
        agent_name="scene_stage",
        prompt_name=prompt_name,
        tools=[tool],
        human_text=(
            f"Read brief. Follow scene-director. Call write_scenes_artifact. "
            f"Read {input_paths['chapter_brief']} and {input_paths['characters']} "
            f"with read_file limit=2000."
        ),
        detected_language=detected_language,
        image_urls=input_paths.get("image_urls") or [],
        max_images=8,
        draft_model=ScenesAgentDraft,
        artifact_name=artifact_name,
        thread_id=thread_id,
        run_id=run_id,
    )
    art = load_artifact(thread_id, run_id, artifact_name, ScenesArtifact)
    scenes = artifact_to_storyboard_scenes(art)
    logger.info("scene deep agent OK chapter=%s scenes=%d", chapter_id, len(scenes))
    return scenes, msgs
