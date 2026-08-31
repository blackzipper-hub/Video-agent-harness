"""Keyframe T2I prompt eval/fix artifact."""
from __future__ import annotations

from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from app.schemas.video_llm import KeyframePromptFixResult


class KeyframeEvalAgentDraft(BaseModel):
    """Args for write_keyframe_eval_artifact."""

    fixes: List[KeyframePromptFixResult] = Field(
        min_length=1,
        description="One fix per expected (shot_number, frame_index)",
    )


class KeyframeEvalArtifact(KeyframeEvalAgentDraft):
    schema_version: int = 1
    artifact: Literal["keyframe_eval"] = "keyframe_eval"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
