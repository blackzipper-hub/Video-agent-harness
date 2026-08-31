"""Video i2v_prompt eval/fix artifact."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.video_llm import PromptEvaluationResult


class VideoEvalAgentDraft(BaseModel):
    """Args for write_video_eval_artifact."""

    evaluations: List[PromptEvaluationResult] = Field(
        min_length=1,
        description="One evaluation per expected shot_number",
    )


class VideoEvalArtifact(VideoEvalAgentDraft):
    schema_version: int = 1
    artifact: Literal["video_eval"] = "video_eval"
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
