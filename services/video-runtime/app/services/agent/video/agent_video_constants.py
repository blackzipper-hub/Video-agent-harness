"""
Video Agent 流程开关常量
"""
# Image Wrapper 角色一致性校验（check_character_consistency_llm）
ENABLE_IMAGE_WRAPPER_CONSISTENCY = True

# Video Wrapper 视频一致性校验（check_video_consistency_llm；同时覆盖 Lipsync Wrapper）
ENABLE_VIDEO_WRAPPER_CONSISTENCY = True

# Image wrapper：单次工具调用内，实际文生图/图生图 API 调用次数上限（含同模型重试与跨模型降级）。
IMAGE_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS = 3

# Video wrapper：单次工具调用内，实际视频生成 API 调用次数上限（语义与 image wrapper 一致）。
VIDEO_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS = 2

# 普通视频是否走 reference-to-video（T2V + reference_images），而非 I2V 首帧硬约束。
# Seedance2 在 should_skip_keyframe_pipeline 为真时会强制走 reference-to-video。
USE_REFERENCE_TO_VIDEO = False
