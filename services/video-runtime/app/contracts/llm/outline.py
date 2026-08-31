"""
Outline LLM output contracts.

Canonical *names* for new code: ``OutlineLLMOutput`` / ``OutlineChapterLLM``.
Implementation currently lives in ``app.models.video_state`` (legacy); this module
is the import surface. Later we can move class bodies here and re-export from video_state.
"""
from app.models.video_state import (
    EnhancementCue,
    StoryChapterForLLMMode as OutlineChapterLLM,
    StoryOutlineForLLMMode as OutlineLLMOutput,
    StoryStructureForLLMMode as OutlineStructureLLM,
)

__all__ = [
    "EnhancementCue",
    "OutlineChapterLLM",
    "OutlineLLMOutput",
    "OutlineStructureLLM",
]
