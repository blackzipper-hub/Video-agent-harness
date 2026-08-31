"""Voice delivery contract — speaker_directions / delivery_cues.

Practice for short-drama / cinematic:
- Character dialogue → Seedance in-clip (quoted lines), NOT TTS
- Narration → TTS only when delivery says so
- Silence → no TTS, no dialogue

Stored on shot.additional_data (no new DB columns).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

SPEAKER_DIRECTIONS_KEY = "speaker_directions"
DELIVERY_CUES_KEY = "delivery_cues"
DELIVERY_NOTE_KEY = "delivery_note"
PROVIDER_TEXT_KEY = "provider_text"

# Internal parse labels (not product enums — derived from delivery text)
VOICE_SILENCE = "silence"
VOICE_SEEDANCE_DIALOGUE = "seedance_dialogue"
VOICE_TTS_NARRATION = "tts_narration"
VOICE_UNKNOWN = "unknown"


def build_delivery_cues(
    *,
    delivery_note: Optional[str] = None,
    provider_text: Optional[str] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if delivery_note and str(delivery_note).strip():
        out[DELIVERY_NOTE_KEY] = str(delivery_note).strip()
    if provider_text and str(provider_text).strip():
        out[PROVIDER_TEXT_KEY] = str(provider_text).strip()
    return out


def voice_contract_patch(
    *,
    speaker_directions: Optional[str] = None,
    delivery_note: Optional[str] = None,
    provider_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Build additional_data patch for voice delivery fields."""
    patch: Dict[str, Any] = {}
    if speaker_directions and str(speaker_directions).strip():
        patch[SPEAKER_DIRECTIONS_KEY] = str(speaker_directions).strip()
    cues = build_delivery_cues(
        delivery_note=delivery_note,
        provider_text=provider_text,
    )
    if cues:
        patch[DELIVERY_CUES_KEY] = cues
    return patch


def get_voice_contract(obj: Any) -> Dict[str, Any]:
    ad = getattr(obj, "additional_data", None)
    if not isinstance(ad, dict):
        return {}
    out: Dict[str, Any] = {}
    if ad.get(SPEAKER_DIRECTIONS_KEY):
        out[SPEAKER_DIRECTIONS_KEY] = ad[SPEAKER_DIRECTIONS_KEY]
    if isinstance(ad.get(DELIVERY_CUES_KEY), dict):
        out[DELIVERY_CUES_KEY] = ad[DELIVERY_CUES_KEY]
    return out


def _contract_blob(obj: Any) -> str:
    ad = getattr(obj, "additional_data", None) if not isinstance(obj, dict) else obj
    if not isinstance(ad, dict):
        ad = get_voice_contract(obj)
    parts = [
        str(ad.get(SPEAKER_DIRECTIONS_KEY) or ""),
    ]
    cues = ad.get(DELIVERY_CUES_KEY) if isinstance(ad.get(DELIVERY_CUES_KEY), dict) else {}
    parts.append(str(cues.get(DELIVERY_NOTE_KEY) or ""))
    return " ".join(parts).lower()


def classify_voice_delivery(obj: Any) -> str:
    """Derive delivery class from speaker_directions / delivery_note text."""
    blob = _contract_blob(obj)
    if not blob.strip():
        # Fallback: narration text present → treat as TTS; dialogue-only → seedance dialogue
        narration = (getattr(obj, "narration", None) or "").strip()
        dialogue = (getattr(obj, "dialogue", None) or "").strip()
        if narration:
            return VOICE_TTS_NARRATION
        if dialogue:
            return VOICE_SEEDANCE_DIALOGUE
        return VOICE_UNKNOWN

    if re.search(r"\btts\b|旁白|narration", blob) and not re.search(
        r"no tts|不要.*旁白|旁白静默|not tts", blob
    ):
        return VOICE_TTS_NARRATION
    if re.search(
        r"seedance|口型对白|quoted dialogue|in-clip|片内对白|character lines",
        blob,
    ):
        return VOICE_SEEDANCE_DIALOGUE
    if re.search(r"silence|silent|无旁白|无对白|no dialogue|no tts|title only", blob):
        return VOICE_SILENCE
    return VOICE_UNKNOWN


def should_generate_narration_tts(obj: Any) -> bool:
    """Only TTS for narration delivery — never for Seedance dialogue / silence."""
    narration = (getattr(obj, "narration", None) or "").strip()
    if not narration:
        return False
    kind = classify_voice_delivery(obj)
    if kind == VOICE_SEEDANCE_DIALOGUE:
        return False
    if kind == VOICE_SILENCE:
        return False
    if kind == VOICE_TTS_NARRATION:
        return True
    # unknown + has narration text → keep legacy TTS (backward compatible)
    return True


def should_preserve_video_audio_in_assemble(shots_or_ads: Any) -> bool:
    """If any shot is Seedance dialogue, mix must not mute video track."""
    items = shots_or_ads
    if items is None:
        return False
    for item in items:
        if classify_voice_delivery(item) == VOICE_SEEDANCE_DIALOGUE:
            return True
    return False


def should_use_seedance_in_clip_dialogue(obj: Any) -> bool:
    """镜头有角色台词且非 silence → 走 Seedance 片内对白（不限 Short Drama）。"""
    dial = (getattr(obj, "dialogue", None) or "").strip()
    if not dial:
        return False
    if classify_voice_delivery(obj) == VOICE_SILENCE:
        return False
    return True


def narration_text_for_tts(obj: Any) -> str:
    """Prefer delivery_cues.provider_text then narration field."""
    ad = getattr(obj, "additional_data", None)
    if isinstance(ad, dict):
        cues = ad.get(DELIVERY_CUES_KEY)
        if isinstance(cues, dict):
            pt = (cues.get(PROVIDER_TEXT_KEY) or "").strip()
            if pt:
                return pt
    return (getattr(obj, "narration", None) or "").strip()


def merge_voice_into_additional_data(
    existing: Optional[Dict[str, Any]],
    *,
    speaker_directions: Optional[str] = None,
    delivery_note: Optional[str] = None,
    provider_text: Optional[str] = None,
) -> Dict[str, Any]:
    from .visual_field_contract import merge_additional_data

    return merge_additional_data(
        existing,
        voice_contract_patch(
            speaker_directions=speaker_directions,
            delivery_note=delivery_note,
            provider_text=provider_text,
        ),
    )


def infer_contract_from_dialogue_narration(
    dialogue: Optional[str],
    narration: Optional[str],
) -> Tuple[str, str]:
    """When LLM omits directions, infer defaults (non-Product-Launch)."""
    d = (dialogue or "").strip()
    n = (narration or "").strip()
    if d and not n:
        return (
            "Seedance口型对白；旁白静默",
            "No TTS. Seedance quoted dialogue in-clip; narration silent.",
        )
    if n and not d:
        return (
            "TTS旁白",
            "TTS narration only. No character dialogue TTS.",
        )
    if d and n:
        return (
            "Seedance口型对白 + 另有画外旁白",
            "Character lines via Seedance; separate TTS narration if provider_text/narration set.",
        )
    return (
        "silence / title only",
        "No TTS. No dialogue.",
    )
