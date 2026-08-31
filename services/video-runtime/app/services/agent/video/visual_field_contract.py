"""Visual fields stored on scene/shot additional_data.

No story hardcodes. Persist place/contact/identity hints under additional_data
(no new DB columns).

Keys:
  enhancement_cue   — already used (broll place/contact intent)
  identity_lock     — verbatim subject attrs reused across shots
  texture_keywords  — short place/material nouns (noren, steam, lantern…)
  shot_language     — cinematography dict (shot_size/camera_movement/lens_mm)
  action_beats      — list of contact events
  hero_moment       — bool, visual peak
  chai_log          — optional {pre, critique, post} after Seedance wrap
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

IDENTITY_LOCK_KEY = "identity_lock"
TEXTURE_KEYWORDS_KEY = "texture_keywords"
SHOT_LANGUAGE_KEY = "shot_language"
ACTION_BEATS_KEY = "action_beats"
HERO_MOMENT_KEY = "hero_moment"
ENHANCEMENT_CUE_KEY = "enhancement_cue"
CHAI_LOG_KEY = "chai_log"

_VISUAL_FIELD_KEYS = (
    IDENTITY_LOCK_KEY,
    TEXTURE_KEYWORDS_KEY,
    SHOT_LANGUAGE_KEY,
    ACTION_BEATS_KEY,
    HERO_MOMENT_KEY,
    ENHANCEMENT_CUE_KEY,
    CHAI_LOG_KEY,
)


def merge_additional_data(existing: Optional[Dict[str, Any]], patch: Dict[str, Any]) -> Dict[str, Any]:
    base = dict(existing) if isinstance(existing, dict) else {}
    base.update({k: v for k, v in patch.items() if v is not None})
    return base


def texture_keywords_from_cue_description(description: str, *, max_n: int = 6) -> List[str]:
    """Derive short texture tokens from an enhancement_cue description (field, not story script)."""
    text = (description or "").strip()
    if not text:
        return []
    # Split on punctuation / whitespace; keep 2+ char tokens
    parts = re.split(r"[\s,，、/;；|]+", text)
    out: List[str] = []
    seen = set()
    for p in parts:
        t = p.strip().lower().strip(".-_")
        if len(t) < 2 or t in seen:
            continue
        # Drop pure stop-ish English fillers
        if t in {"the", "a", "an", "and", "with", "from", "into", "at", "of", "to"}:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_n:
            break
    return out


def patch_from_enhancement_cue(cue: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build additional_data patch when binding a cue to a scene."""
    if not cue or not (cue.get("description") or "").strip():
        return {}
    desc = (cue.get("description") or "").strip()
    patch: Dict[str, Any] = {
        ENHANCEMENT_CUE_KEY: {
            "type": cue.get("type") or "broll",
            "description": desc,
            "timestamp_hint": cue.get("timestamp_hint"),
        },
        TEXTURE_KEYWORDS_KEY: texture_keywords_from_cue_description(desc),
    }
    return patch


def get_visual_fields(obj: Any) -> Dict[str, Any]:
    """Read known visual fields from scene/shot.additional_data."""
    ad = getattr(obj, "additional_data", None)
    if not isinstance(ad, dict):
        return {}
    return {k: ad[k] for k in _VISUAL_FIELD_KEYS if k in ad and ad[k] is not None}


def attach_identity_lock(obj: Any, identity_lock: Optional[str]) -> None:
    """Write identity_lock onto scene/shot additional_data."""
    text = (identity_lock or "").strip()
    if not text:
        return
    ad = getattr(obj, "additional_data", None)
    merged = merge_additional_data(ad if isinstance(ad, dict) else None, {IDENTITY_LOCK_KEY: text})
    obj.additional_data = merged


def fold_machine_visual_fields(
    *,
    shot_language: Any = None,
    action_beats: Any = None,
    hero_moment: Any = None,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge LLM machine fields into additional_data (no new DB columns)."""
    patch: Dict[str, Any] = {}
    if shot_language is not None:
        if hasattr(shot_language, "model_dump"):
            patch[SHOT_LANGUAGE_KEY] = shot_language.model_dump()
        elif isinstance(shot_language, dict):
            patch[SHOT_LANGUAGE_KEY] = shot_language
    if isinstance(action_beats, list) and action_beats:
        patch[ACTION_BEATS_KEY] = [str(x).strip() for x in action_beats if str(x).strip()]
    if hero_moment is not None:
        patch[HERO_MOMENT_KEY] = bool(hero_moment)
    return merge_additional_data(existing, patch)


def inherit_scene_visual_fields_to_shot_ad(
    scene: Any,
    shot_additional_data: Optional[Dict[str, Any]],
    *,
    identity_lock: Optional[str] = None,
) -> Dict[str, Any]:
    """Copy scene visual fields into detailed-shot additional_data (plus optional identity)."""
    ad = merge_additional_data(shot_additional_data, {})
    scene_fields = get_visual_fields(scene)
    for key in (
        ENHANCEMENT_CUE_KEY,
        TEXTURE_KEYWORDS_KEY,
        SHOT_LANGUAGE_KEY,
        ACTION_BEATS_KEY,
        HERO_MOMENT_KEY,
    ):
        if key in scene_fields and key not in ad:
            ad[key] = scene_fields[key]
    if identity_lock and identity_lock.strip():
        ad[IDENTITY_LOCK_KEY] = identity_lock.strip()
    elif scene_fields.get(IDENTITY_LOCK_KEY):
        ad.setdefault(IDENTITY_LOCK_KEY, scene_fields[IDENTITY_LOCK_KEY])
    return ad or {}
