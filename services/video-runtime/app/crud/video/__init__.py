"""
视频CRUD模块 - 按业务领域拆分

所有函数返回msgspec.Struct类型（或兼容dict），实现类型安全和高性能
"""

# 导出所有模块
from . import video_generation
from . import video_character
from . import video_keyframe
from . import video_segment
from . import video_story
from . import video_audio
from . import video_other

# 导出常用函数
from .video_generation import (
    create_video_generation,
    get_video_generation_by_uuid,
    get_video_generations_with_latest_versions,
)

from .video_character import (
    create_character,
    get_character_by_uuid,
    get_characters_by_run_id,
)

from .video_keyframe import (
    create_keyframe,
    get_keyframe_by_uuid,
    get_keyframes_by_story_outline_id,
)

from .video_segment import (
    create_video_segment,
    get_video_segment_by_uuid,
    get_video_segments_with_data_by_run_id,
)

from .video_story import (
    create_video_story_outline,
    get_video_story_outline_by_uuid,
    create_scene,
    get_scene_by_uuid,
)

from .video_audio import (
    create_video_audio_transcription,
    create_narration,
    create_music_generation,
    create_audio_effect,
)

from .video_other import (
    create_video_analysis,
    create_video_assembly,
    create_video_lipsync_generation,
)

__all__ = [
    # 模块
    'video_generation',
    'video_character',
    'video_keyframe',
    'video_segment',
    'video_story',
    'video_audio',
    'video_other',
    
    # 常用函数
    'create_video_generation',
    'get_video_generation_by_uuid',
    'create_character',
    'get_character_by_uuid',
    'create_keyframe',
    'get_keyframe_by_uuid',
    'create_video_segment',
    'get_video_segment_by_uuid',
    'create_video_story_outline',
    'get_video_story_outline_by_uuid',
    'create_scene',
    'get_scene_by_uuid',
    'create_video_audio_transcription',
    'create_narration',
    'create_music_generation',
    'create_audio_effect',
    'create_video_analysis',
    'create_video_assembly',
    'create_video_lipsync_generation',
]
