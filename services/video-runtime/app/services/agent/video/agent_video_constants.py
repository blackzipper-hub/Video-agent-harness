"""
Video Agent 流程开关常量
- 多视角图、融合图可通过常量关闭，便于调试或缩短流水线。
"""
# 是否执行多视角图生成（主角色设计后、regenerate character 级联时）
ENABLE_MULTIVIEW = False

# 是否按 matched_style_category 从精选风格库随机取一条，写入 hidden_style_description
# 并下游强制 Style 匹配（影响 outline/storyboard/video prompt）。默认关闭，避免风格被库固定带偏。
ENABLE_CURATED_STYLE_MATCH = False

# 是否允许角色直接复用用户上传的原图作为参考图（True=单人独照可直接复用，防漂移；
# 一张图被多个角色命中时，仅第一个角色直接复用，其余角色走 I2I 提取单人独立图，避免多角色共用同一张合照导致关键帧雷同。
# False=所有命中图的角色都不直接复用原图，统一走 I2I 提取单人独立图）。
ENABLE_DIRECT_UPLOAD_REUSE = True

# 是否将角色参考图背景归一化为浅灰白渐变 studio 背景（True=所有命中用户图的角色均走 I2I，
# 保留外观并替换背景，此时 ENABLE_DIRECT_UPLOAD_REUSE 对路由不生效；False=按 DIRECT_UPLOAD_REUSE 规则是否直接复用原图）。
ENABLE_CHARACTER_STUDIO_BACKGROUND = True

# 是否执行角色融合图生成（visual_elements_matching 后、regenerate character 级联时）
ENABLE_FUSION = False

# 是否执行 Image Wrapper 角色一致性校验（check_character_consistency_llm，使用 Gemini VLM）
ENABLE_IMAGE_WRAPPER_CONSISTENCY = True

# 是否执行 Video Wrapper 视频一致性校验（check_video_consistency_llm，使用 Gemini VLM；同时覆盖 Lipsync Wrapper）
ENABLE_VIDEO_WRAPPER_CONSISTENCY = True

# 仅 image client/agent 入口：是否跳过角色一致性校验（True=跳过，仅影响 generate_image_with_agent）
SKIP_IMAGE_AGENT_CONSISTENCY_CHECK = True

# [已弃用 2026-05-12] 关键帧是否使用场所(location)参考图改为「按所选图片模型能力」决定：
# 见 keyframe_generation_service.resolve_use_venue_ref_image + tools/image/ref_utils._SUPPORTS_VENUE_REF_IMAGE_BY_MODEL。
# 仅 GPT Image 2 实测能用 location 图不出"巨人"问题，其它模型默认 False。
# 此常量已无引用，保留只为提醒后续 PR 不要重新引入"一刀切" flag。

# Image wrapper：单次工具调用内，实际文生图/图生图 API 调用次数上限（含同模型重试与跨模型降级）。
IMAGE_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS = 3

# Video wrapper：单次工具调用内，实际视频生成 API 调用次数上限（语义与 image wrapper 一致）。
VIDEO_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS = 2

# 普通视频是否走 reference-to-video（T2V + reference_images：关键帧 + 角色参考图），
# 而非 I2V 首帧硬约束。仅 REFERENCE_TO_VIDEO_VIDEO_TOOLS 内工具有效；口型镜头不受影响。
# 注意：Seedance2 系列在 should_skip_keyframe_pipeline 为真时会强制走 reference-to-video，
# 不受本开关影响（见 user_options.should_use_reference_to_video）。
USE_REFERENCE_TO_VIDEO = False

# reference-to-video 模式下 reference_images 上限（关键帧 + 角色图合计）。
MAX_REFERENCE_IMAGES_FOR_VIDEO = 3
