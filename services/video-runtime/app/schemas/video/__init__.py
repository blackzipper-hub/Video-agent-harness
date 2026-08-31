"""
Video schemas - msgspec.Struct models for type-safe database operations
"""

from .video_generation import VideoGenerationDB, VideoGenerationVersionDB
from .video_character import (
    VideoCharacterDB,
    VideoCharacterGenerationVersionDB,
    VideoCharacterMultiViewImageDB,
    VideoCharacterMultiViewImageVersionDB
)
from .video_keyframe import (
    VideoKeyframeDB,
    VideoKeyframeVersionDB,
    VideoKeyframeReflectionDB,
    VideoKeyframeReflectionResultDB,
)
from .video_segment import VideoSegmentDB, VideoSegmentVersionDB
from .video_story import (
    VideoStoryOutlineDB,
    VideoSceneDB,
    VideoStoryboardDetailDB,
    VideoDetailedShotDB,
    VideoChapterDB
)
from .video_audio import (
    VideoAudioTranscriptionDB,
    VideoAudioEffectDB,
    VideoAudioEffectVersionDB,
    VideoMusicGenerationDB,
    VideoMusicGenerationVersionDB,
    VideoNarrationDB,
    VideoNarrationVersionDB
)
from .video_other import (
    VideoLipsyncGenerationDB,
    VideoLipsyncGenerationVersionDB,
    VideoAnalysisDB,
    VideoAssemblyDB
)
from ..video_llm import (
    KeyframePromptFixResult,
    BatchKeyframePromptFixResult,
    VideoPromptResult,
    BatchVideoPromptResult,
    VideoGenerationPrompt,
    PromptEvaluationResult,
    BatchPromptEvaluationResult,
    StyleDetectionResult
)

__all__ = [
    # Generation
    "VideoGenerationDB",
    "VideoGenerationVersionDB",
    # Character
    "VideoCharacterDB",
    "VideoCharacterGenerationVersionDB",
    "VideoCharacterMultiViewImageDB",
    "VideoCharacterMultiViewImageVersionDB",
    # Keyframe
    "VideoKeyframeDB",
    "VideoKeyframeVersionDB",
    "VideoKeyframeReflectionDB",
    "VideoKeyframeReflectionResultDB",
    # Segment
    "VideoSegmentDB",
    "VideoSegmentVersionDB",
    # Story
    "VideoStoryOutlineDB",
    "VideoSceneDB",
    "VideoStoryboardDetailDB",
    "VideoDetailedShotDB",
    "VideoChapterDB",
    # Audio
    "VideoAudioTranscriptionDB",
    "VideoAudioEffectDB",
    "VideoAudioEffectVersionDB",
    "VideoMusicGenerationDB",
    "VideoMusicGenerationVersionDB",
    "VideoNarrationDB",
    "VideoNarrationVersionDB",
    # Other
    "VideoLipsyncGenerationDB",
    "VideoLipsyncGenerationVersionDB",
    "VideoAnalysisDB",
    "VideoAssemblyDB",
    # LLM Schemas
    "KeyframePromptFixResult",
    "BatchKeyframePromptFixResult",
    "VideoPromptResult",
    "BatchVideoPromptResult",
    "VideoGenerationPrompt",
    "PromptEvaluationResult",
    "BatchPromptEvaluationResult",
    "StyleDetectionResult",
]
