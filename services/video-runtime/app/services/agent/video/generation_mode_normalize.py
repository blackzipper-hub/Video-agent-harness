"""Normalize LLM / UI generation_mode strings → GenerationMode values.

Canonical set: normal | lipsync | empty_shot.
"""
from __future__ import annotations

from typing import Optional

from app.models.tool_enums import GenerationMode


def normalize_generation_mode(raw: Optional[str]) -> Optional[str]:
    """Return canonical mode or None if unrecognized."""
    if raw is None:
        return None
    s = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    if s in (GenerationMode.EMPTY_SHOT.value, "empty", "broll", "b_roll", "空镜"):
        return GenerationMode.EMPTY_SHOT.value
    if s in (
        GenerationMode.LIPSYNC.value,
        "lip_sync",
        "lip_sync_mv",
        "talking_head",
        "对嘴型",
        "对口型",
        "口型",
    ):
        return GenerationMode.LIPSYNC.value
    if s in (
        GenerationMode.NORMAL.value,
        "video",  # common LLM mistake — not a mode
        "i2v",
        "t2v",
        "standard",
        "default",
        "普通",
        "普通镜头",
    ):
        return GenerationMode.NORMAL.value
    return None


def coerce_generation_mode(raw: Optional[str]) -> str:
    """Always return a valid GenerationMode value (default normal)."""
    return normalize_generation_mode(raw) or GenerationMode.NORMAL.value
