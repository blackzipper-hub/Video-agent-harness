"""Storyboard detail artifact — OM-style schema: enums + field descriptions for tool args."""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Align with app.models.video_state.ShotLanguage (+ OM scene_plan extras kept optional)
ShotSize = Literal[
    "extreme_wide",
    "wide",
    "medium_wide",
    "medium",
    "medium_close",
    "close_up",
    "extreme_close_up",
    "over_shoulder",
    "insert",
    "establishing",
]
CameraMovement = Literal[
    "static",
    "pan_left",
    "pan_right",
    "tilt_up",
    "tilt_down",
    "dolly_in",
    "dolly_out",
    "tracking",
    "crane_up",
    "crane_down",
    "handheld",
    "crash_zoom",
    "whip_pan",
    "orbital",
    "zoom_in",
    "zoom_out",
]
_LENS_MM = frozenset({24, 35, 50, 85})


class ShotLanguageDraft(BaseModel):
    """Structured cinematography vocabulary (OM shot_language)."""

    model_config = {"extra": "allow"}

    shot_size: ShotSize = Field(
        default="medium",
        description="Framing size: extreme_wide|wide|medium_wide|medium|medium_close|close_up|extreme_close_up|over_shoulder|insert|establishing",
    )
    camera_movement: CameraMovement = Field(
        default="static",
        description="Camera move enum; use static when no intentional move",
    )
    # int + validator — not Literal[24,35,…] (tool JSON schema treats int enums as strings)
    lens_mm: int = Field(
        default=35,
        description="Lens focal length in mm; allowed: 24|35|50|85",
    )

    @field_validator("lens_mm", mode="before")
    @classmethod
    def _lens(cls, v: Any) -> int:
        try:
            n = int(v)
        except (TypeError, ValueError) as e:
            raise ValueError("lens_mm must be int 24|35|50|85") from e
        if n not in _LENS_MM:
            raise ValueError(f"lens_mm {n} not in {sorted(_LENS_MM)}")
        return n


class ShotDraft(BaseModel):
    shot_number: int = Field(description="Must equal the source scene_number for this shot")
    shot_type: str = Field(
        default="",
        description="Human-readable shot type / 景别 label (e.g. 中景, 特写, OTS)",
    )
    camera_position: Optional[str] = Field(
        default=None,
        description="Camera position: direction + height, e.g. 斜侧高机位",
    )
    camera_angle: Optional[str] = Field(
        default=None,
        description="Camera angle: 平视/仰拍/俯拍/顶拍…",
    )
    subject_angle: Optional[str] = Field(
        default=None,
        description="Subject facing: 正面/侧面/背面…; empty OK for wide/no clear subject",
    )
    subject_pose: Optional[str] = Field(
        default=None,
        description="Visible pose with contact potential, not vague standing",
    )
    scene_description: str = Field(
        description="Shootable frame content: subject + action + setting; dense, not mood-only",
    )
    camera_movement: str = Field(
        default="",
        description="Free-text camera move for editors (complement shot_language.camera_movement)",
    )
    lighting: str = Field(default="", description="Lighting mood/key for this shot")
    visual_effects: str = Field(default="", description="VFX notes; empty string if none")
    transition: str = Field(default="cut", description="Transition out: cut|dissolve|fade…")
    dialogue: str = Field(
        default="",
        description="On-camera spoken lines for lip-sync / Seedance quotes; NOT TTS narration",
    )
    sound_effects: str = Field(default="", description="SFX notes")
    narration: Optional[str] = Field(
        default=None,
        description="Off-screen TTS narration only; leave null when dialogue carries speech",
    )
    narration_gender: Optional[Literal["f", "m"]] = Field(
        default=None,
        description="Required only if narration is set: f=female TTS, m=male TTS; else omit/null",
    )
    speaker_directions: Optional[str] = Field(
        default=None,
        description="A/V delivery hint, e.g. Seedance quoted dialogue / silence / TTS旁白",
    )
    delivery_note: Optional[str] = Field(
        default=None,
        description="Delivery constraint, e.g. No TTS. Seedance quoted dialogue.",
    )
    shot_language: ShotLanguageDraft = Field(
        description="Required machine enums for downstream (shot_size / camera_movement / lens_mm)",
    )
    action_beats: List[str] = Field(
        min_length=2,
        max_length=6,
        description="2–6 contact/prop event beats (拍/递/鞠躬/推…); ban skirt-sway-only fillers",
    )
    hero_moment: bool = Field(
        default=False,
        description="True if this is a visual peak frame for the piece",
    )

    @field_validator("narration_gender", mode="before")
    @classmethod
    def _coerce_gender(cls, v: Any) -> Any:
        if v is None:
            return None
        s = str(v).strip().lower()
        if not s or s in ("null", "none", "n/a"):
            return None
        if s in ("f", "female", "女", "女声"):
            return "f"
        if s in ("m", "male", "男", "男声"):
            return "m"
        return None


class StoryboardAgentDraft(BaseModel):
    shots: List[ShotDraft] = Field(
        min_length=1,
        description="One detailed shot per expected scene/shot_number",
    )


class StoryboardArtifact(StoryboardAgentDraft):
    schema_version: int = 1
    artifact: Literal["storyboard"] = "storyboard"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    batch_id: Optional[str] = None
