"""
Video agent 下 regenerate（补槽 / 追加版本）子包。

- regenerate_keyframe_service: 关键帧补槽、从 version uuid 经 agent 再生
- regenerate_video_service: 视频行与版本的补全、追加
- regenerate_by_request_service: 按请求编排（任务记录、级联）
"""
from .regenerate_by_request_service import (
    regenerate_keyframes_by_request,
    regenerate_videos_by_request,
)
from .regenerate_keyframe_service import (
    regenerate_append_keyframe_version_resolving_parent,
    regenerate_keyframe_from_version_uuid,
    regenerate_pack_keyframe_result,
)
from .regenerate_video_service import (
    regenerate_append_video_generation_version_for_row,
    regenerate_ensure_video_generation_row_for_shot,
)

__all__ = [
    "regenerate_append_keyframe_version_resolving_parent",
    "regenerate_append_video_generation_version_for_row",
    "regenerate_ensure_video_generation_row_for_shot",
    "regenerate_keyframe_from_version_uuid",
    "regenerate_keyframes_by_request",
    "regenerate_pack_keyframe_result",
    "regenerate_videos_by_request",
]
