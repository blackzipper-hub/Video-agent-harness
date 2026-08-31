"""Scene artifact — OM scene_plan fields + Cuti chapter scenes.

description = user-facing visual prose only.
Machine craft (narrative_role, dialogue, 5-aspect, shot_language…) lives in
additional_data / sibling fields — never dumped as LABEL: blocks into description.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ShotLanguage(BaseModel):
    """OM shot_language — prefer OM enums; accept free-text for zh agents."""

    shot_size: Optional[str] = Field(
        default=None,
        description="OM: extreme_wide|wide|medium_wide|medium|medium_close|close_up|extreme_close_up|over_shoulder|insert|establishing",
    )
    camera_movement: Optional[str] = Field(
        default=None,
        description="OM: static|pan_*|tilt_*|dolly_*|tracking_*|crane_*|handheld|steadicam|whip_pan|orbital|zoom_*|rack_focus",
    )
    lens_mm: Optional[int] = Field(default=None, description="Prefer 14|24|35|50|85|135|200")
    lighting_key: Optional[str] = Field(
        default=None,
        description="OM: high_key|low_key|natural|golden_hour|blue_hour|tungsten_warm|neon|silhouette|rim_lit|volumetric|overcast_soft",
    )
    depth_of_field: Optional[str] = Field(default=None, description="shallow|medium|deep")
    color_temperature: Optional[str] = Field(default=None, description="cool|neutral|warm|mixed")


class ScenePlanMeta(BaseModel):
    """OM scene_plan row metadata (stored under additional_data for DB bridge)."""

    model_config = {"extra": "allow"}

    script_section_id: Optional[str] = Field(
        default=None, description="Bind to script.sections[].id (required for drama binding)"
    )
    narrative_role: Optional[str] = Field(
        default=None,
        description=(
            "OM enum preferred: establish_context|introduce_subject|build_tension|"
            "deliver_payload|transition|emotional_beat|evidence|comparison|resolution|call_to_action"
        ),
    )
    information_role: str = Field(
        default="", description="What the viewer learns or feels from this scene"
    )
    shot_intent: str = Field(
        default="", description="WHY this shot exists in the video"
    )
    hero_moment: bool = Field(
        default=False, description="True if visual peak of the piece"
    )
    # Craft formerly dumped into description as LABEL: blocks — keep typed here
    dialogue: str = Field(
        default="",
        description="Spoken lines / TTS / silence note for this scene; empty if pure picture",
    )
    beats: List[str] = Field(
        default_factory=list,
        description="2–6 timed action beats, e.g. '0-2s: …', '2-4s: …'",
    )
    subject: str = Field(
        default="",
        description="Name + costume + prop anchors visible in frame",
    )
    subject_motion: str = Field(
        default="",
        description="Contact/prop motion chain with → (拍/递/鞠躬…); not skirt-sway-only",
    )
    spatial: str = Field(
        default="",
        description="Shot size + FG/MG/BG + framing change, e.g. OTS → CU",
    )
    camera_notes: str = Field(
        default="",
        description="Lens/height/DoF/steadiness free-text; complements shot_language",
    )
    overlays: List[str] = Field(
        default_factory=list,
        description="Abstract overlay notes; never readable digits/letters",
    )
    overlay_notes: str = Field(default="", description="Free-text overlay direction")
    framing: str = Field(
        default="", description="Framing with → for change, e.g. MCU → CU"
    )
    movement: str = Field(default="", description="Camera/subject motion summary")
    shot_language: Optional[ShotLanguage] = Field(
        default=None, description="Structured cinematography vocab"
    )
    texture_keywords: List[str] = Field(
        default_factory=list, description="Material/atmosphere keywords"
    )


class SceneDraft(BaseModel):
    scene_number: int = Field(description="1-based scene number; storyboard shot_number equals this")
    title: str = Field(description="Short scene title for UI")
    description: str = Field(
        description=(
            "USER-FACING only: 1–3 dense visual sentences a viewer/director can picture "
            "(who/costume/prop/action/setting/framing). "
            "NEVER dump NARRATIVE_ROLE/DIALOGUE/BEATS/SUBJECT/CAMERA labels here — "
            "those belong in additional_data."
        ),
    )
    duration: float = Field(description="Scene length in seconds")
    camera_angle: str = Field(default="", description="e.g. 平视 / 仰拍 / OTS")
    character_action: str = Field(
        default="",
        description="Primary contact action summary (may mirror additional_data.subject_motion)",
    )
    visual_style: str = Field(default="", description="Local style note if differs from global")
    transition_style: str = Field(default="cut", description="cut|dissolve|fade… into next")
    is_bridge: bool = Field(default=False, description="True if connective/bridge scene")
    character_ids: List[str] = Field(
        default_factory=list,
        description="Character ids present in this scene",
    )
    chapter_id: Optional[str] = Field(default=None, description="Owning chapter id")
    generation_mode: Optional[str] = Field(
        default=None,
        description="Only: normal | lipsync | empty_shot (never video/i2v)",
    )
    additional_data: Optional[ScenePlanMeta] = Field(
        default=None,
        description=(
            "Typed craft: script_section_id, narrative_role, dialogue, beats, "
            "subject/subject_motion/spatial, framing→, shot_language, overlays…"
        ),
    )

    @field_validator("additional_data", mode="before")
    @classmethod
    def _coerce_meta(cls, v: Any) -> Any:
        if v is None or isinstance(v, ScenePlanMeta):
            return v
        if isinstance(v, dict):
            return ScenePlanMeta.model_validate(v)
        return v


class ScenesArtifact(BaseModel):
    schema_version: int = 1
    version: str = "1.0"  # OM scene_plan.schema.json
    artifact: Literal["scenes"] = "scenes"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    chapter_id: Optional[str] = None
    style_playbook: str = ""
    scenes: List[SceneDraft] = Field(min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ScenesAgentDraft(BaseModel):
    """Args for write_scenes_artifact."""

    scenes: List[SceneDraft] = Field(
        min_length=1,
        description="Scenes for this chapter/batch; bind dialogue to script sections",
    )
    style_playbook: str = Field(
        default="",
        description="Short local style playbook for this chapter if needed",
    )
