"""
计费 / 积分操作类型（与 Cuti-VideoAgent 对齐；供 billing_worker、credit_deduction_utils 使用）
"""
from enum import Enum


class CreditOperationType(str, Enum):
    INIT = "init"
    ADD = "add"
    USE = "use"
    EXPIRE = "expire"
    INVITE = "invite"
    SYSTEM = "system"
    # Agent 主流程（计费 worker 按 run.agent_type 区分）
    VIDEO_GENERATION = "video_generation"
    STORY_GENERATION = "story_generation"
    MUSIC_GENERATION = "music_generation"
    IMAGE_GENERATION = "image_generation"
    REGENERATE_KEYFRAMES = "regenerate_keyframes"
    REGENERATE_VIDEOS = "regenerate_videos"
    REGENERATE_CHARACTERS = "regenerate_characters"
