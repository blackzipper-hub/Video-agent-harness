"""Script artifact — OM Production Script + short-drama beat extensions.

Aligned with OpenMontage `schemas/artifacts/script.schema.json`, plus:
- beat_map / beat_role (short-drama rhythm)
- chapter_id (Cuti chapter binding)
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ScriptDeliveryCues(BaseModel):
    """OM delivery_cues — structured voice-performance hints for TTS."""

    pace: Optional[
        Literal["slow", "measured", "conversational", "brisk", "fast", "custom"]
    ] = Field(default=None, description="Speaking pace band for TTS")
    energy: str = Field(default="", description="Energy adjective, e.g. tense / warm / clipped")
    emphasis_words: List[str] = Field(
        default_factory=list,
        description="Words to stress in performance",
    )
    pause_before_seconds: Optional[float] = Field(
        default=None, description="Silence before this section starts"
    )
    pause_after_seconds: Optional[float] = Field(
        default=None, description="Silence after this section ends"
    )
    delivery_note: str = Field(default="", description="Free-text acting note for voice")
    provider_text: str = Field(
        default="",
        description="Provider-specific TTS markup if needed; else leave empty",
    )


class ScriptEnhancementCue(BaseModel):
    type: Literal["overlay", "broll", "diagram", "stat_card", "code_snippet", "animation"] = Field(
        default="broll",
        description="Visual enhancement type for this beat",
    )
    description: str = Field(description="What to show / overlay (no readable fake UI digits)")
    timestamp_seconds: Optional[float] = Field(
        default=None, description="Optional offset within section"
    )


class ScriptPronunciationGuide(BaseModel):
    word: str = Field(description="Word/name that needs pronunciation help")
    phonetic: str = Field(description="Phonetic or TTS-friendly spelling")


class ScriptSectionDraft(BaseModel):
    id: str = Field(description="Stable section id, e.g. sec_hook_01 — scenes bind via this")
    label: str = Field(
        default="",
        description="Human label, e.g. HOOK / ESCALATION / DIALOGUE / REVEAL / LANDING",
    )
    beat_role: Literal[
        "hook", "escalation", "reveal", "landing", "midpoint", "narration", "dialogue"
    ] = Field(
        default="dialogue",
        description="Short-drama rhythm role of this section",
    )
    text: str = Field(
        description="Performable dialogue and/or narration lines for this beat",
    )
    start_seconds: float = Field(description="Section start on timeline (seconds)")
    end_seconds: float = Field(description="Section end on timeline (seconds)")
    speaker_directions: str = Field(
        default="",
        description="A/V delivery: Seedance lip-sync / TTS narration / silence title only",
    )
    delivery_cues: Optional[ScriptDeliveryCues] = Field(
        default=None, description="Optional TTS performance block"
    )
    enhancement_cues: List[ScriptEnhancementCue] = Field(
        default_factory=list,
        description="Optional broll/overlay hints",
    )
    pronunciation_guides: List[ScriptPronunciationGuide] = Field(
        default_factory=list,
        description="Optional pronunciation list for names/terms",
    )
    source_ref: str = Field(
        default="",
        description="Optional pointer back to outline chapter / brief",
    )
    chapter_id: Optional[str] = Field(
        default=None, description="Cuti chapter binding id when known"
    )


class ScriptVoicePerformance(BaseModel):
    """OM voice_performance block (TTS contract)."""

    performance_intent: str = Field(
        default="", description="Overall vocal intent for the piece"
    )
    pacing_profile: Optional[
        Literal[
            "contemplative",
            "conversational",
            "energetic",
            "technical",
            "cinematic",
            "custom",
        ]
    ] = Field(default=None, description="Global pacing profile")
    energy_curve: str = Field(default="", description="How energy rises/falls across the piece")
    pause_policy: str = Field(default="", description="When/where to leave silence")
    sample_section_id: str = Field(
        default="", description="Section id that best exemplifies the voice"
    )
    provider_notes: Dict[str, str] = Field(
        default_factory=dict,
        description="Per-provider TTS notes keyed by provider name",
    )


class ScriptAgentDraft(BaseModel):
    """Args for write_script_artifact."""

    title: str = Field(description="Script title (can match outline title)")
    total_duration_seconds: int = Field(description="Total script length in seconds")
    beat_map: List[str] = Field(
        default_factory=list,
        description="Ordered beat labels across the piece: hook, escalation, reveal, landing…",
    )
    sections: List[ScriptSectionDraft] = Field(
        min_length=2,
        description="Timed sections; ≥2; cover the full duration without huge gaps",
    )
    voice_performance: Optional[ScriptVoicePerformance] = Field(
        default=None, description="Optional global TTS performance contract"
    )
    voice_performance_intent: str = Field(
        default="",
        description="Flat intent string (compat); mirrors voice_performance.performance_intent",
    )


class ScriptArtifact(ScriptAgentDraft):
    schema_version: int = 1
    version: str = "1.0"  # OM script.schema.json const
    artifact: Literal["script"] = "script"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Dict[str, str] = Field(default_factory=dict)
