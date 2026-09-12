"""
工具相关的统一枚举定义
"""
from enum import Enum
from typing import Any, Dict, Optional, Tuple


# ==================== 工具模式 ====================

class ToolMode(str, Enum):
    """工具生成模式"""
    # 图像模式
    T2I = "t2i"  # Text-to-Image
    I2I = "i2i"  # Image-to-Image
    
    # 视频模式
    T2V = "t2v"  # Text-to-Video
    I2V = "i2v"  # Image-to-Video
    
    # 通用
    ALL = "all"  # 所有模式


# ==================== LLM 模型枚举 ====================

class LLMModel(str, Enum):
    """LLM 模型枚举（用于成本计算）"""
    # OpenAI 模型
    GPT_4_1_MINI = "gpt-4.1-mini"
    GPT_5_NANO = "gpt-5-nano"
    GPT_5_MINI = "gpt-5-mini"
    GPT_5_4_MINI = "gpt-5.4-mini"
    GPT_5_4_NANO = "gpt-5.4-nano"
    GPT_5_6_SOL = "gpt-5.6-sol"
    GPT_5_6_TERRA = "gpt-5.6-terra"
    GPT_5_6_LUNA = "gpt-5.6-luna"
    
    # Google Gemini 模型
    GEMINI_2_5_FLASH = "gemini-2.5-flash"
    GEMINI_3_FLASH_PREVIEW = "gemini-3-flash-preview"
    GEMINI_3_PRO_PREVIEW = "gemini-3-pro-preview"
    GEMINI_3_1_PRO_PREVIEW = "gemini-3.1-pro-preview"
    GEMINI_2_0_FLASH = "gemini-2.0-flash"


# ==================== 工具类型（细化到具体模型级别） ====================

class ToolType(str, Enum):
    """工具类型枚举（细化到具体模型级别）
    
    通过 ToolProvider + ToolType 可以唯一定位到具体的 API 调用
    每个枚举值对应一个具体的模型名称
    """
    # ==================== 图像生成工具 ====================
    GEMINI_2_5_FLASH = "gemini-2.5-flash"

    # Nano Banana (Google Gemini, via WaveSpeed)
    GEMINI_2_5_FLASH_IMAGE = "gemini-2.5-flash-image"
    GEMINI_3_PRO_IMAGE_PREVIEW = "gemini-3-pro-image-preview"
    GEMINI_3_1_FLASH_IMAGE_PREVIEW = "gemini-3.1-flash-image-preview"  # Nano Banana 2，便宜版 Pro

    # Seedream (ByteDance, via WaveSpeed)
    SEEDREAM_V4_5 = "seedream-v4.5"

    # OpenAI GPT Image 2 (via WaveSpeed openai/gpt-image-2)
    GPT_IMAGE_2 = "gpt-image-2"
    
    # ==================== 视频生成工具 ====================
    # Seedance (ByteDance, via WaveSpeed)
    SEEDANCE_V1_PRO_FAST = "seedance-v1-pro-fast"
    SEEDANCE_V1_5_PRO_FAST = "seedance-v1.5-pro-fast"  # image-to-video-fast，720p/1080p，generate_audio 默认 False
    SEEDANCE_2_I2V = "seedance-2.0-i2v"  # image-to-video，480p/720p/1080p，4–15s，last_image，generate_audio=False
    SEEDANCE_2_I2V_TURBO = "seedance-2.0-i2v-turbo"  # image-to-video-turbo，720p/1080p，4–15s，last_image，generate_audio=False
    SEEDANCE_2_FAST_I2V = "seedance-2.0-fast-i2v"  # image-to-video，480p/720p/1080p，4–15s，last_image，generate_audio=False
    SEEDANCE_2_FAST_I2V_TURBO = "seedance-2.0-fast-i2v-turbo"  # image-to-video-turbo (fast)，720p/1080p，4–15s，last_image，generate_audio=False
    SEEDANCE_2_T2V = "seedance-2.0-t2v"  # text-to-video，480p/720p/1080p，4–15s，generate_audio 默认 True，支持 reference_images/videos/audios
    SEEDANCE_2_FAST_T2V = "seedance-2.0-fast-t2v"  # fast text-to-video，480p/720p/1080p，4–15s，generate_audio 默认 True，支持 reference_images/videos/audios，速度优化更便宜
    SEEDANCE_2_T2V_TURBO = "seedance-2.0-t2v-turbo"  # text-to-video-turbo，仅 720p/1080p（480p→720p），4–15s，generate_audio 默认 True，支持 reference_images/videos/audios，HD turbo 提速
    SEEDANCE_2_FAST_T2V_TURBO = "seedance-2.0-fast-t2v-turbo"  # fast text-to-video-turbo，仅 720p/1080p（480p→720p），4–15s，generate_audio 默认 True，支持 reference_images/videos/audios，最快最便宜的 HD turbo
    SEEDANCE_V1_LITE_I2V_480P = "seedance-v1-lite-i2v-480p"
    SEEDANCE_V1_LITE_I2V_720P = "seedance-v1-lite-i2v-720p"
    SEEDANCE_V1_LITE_I2V_1080P = "seedance-v1-lite-i2v-1080p"
    
    # Wan 2.5 (Alibaba, via WaveSpeed) - 单模型，resolution 为参数，可选 audio，无 end image
    WAN_2_5_I2V = "wan-2.5-i2v"
    # Wan 2.6 Flash (Alibaba, via WaveSpeed) - image-to-video-flash，720p/1080p，duration 3-15，enable_audio 影响计费
    WAN_2_6_FLASH_I2V = "wan-2.6-flash-i2v"
    # LTX 2.3 Lipsync (WaveSpeed) - 主流程口型，audio+image(+prompt)→video，480p/720p/1080p
    LTX_2_3_LIPSYNC = "wavespeed-ai/ltx-2.3/lipsync"
    # Kling V2 AI Avatar Pro (Kwaivgi, WaveSpeed) - audio+image(+prompt)→talking-head；计费按音频时长，min 5s
    KLING_V2_AI_AVATAR_PRO = "kwaivgi/kling-v2-ai-avatar-pro"
    # WAN 2.2 Speech-to-Video (WaveSpeed) - audio+image(+prompt)→视频；官方 resolution 480p/720p，最长约 10min
    WAN_2_2_SPEECH_TO_VIDEO = "wavespeed-ai/wan-2.2/speech-to-video"

    # Kling v3.0 Std (via WaveSpeed) - image-to-video, 3-15s, $0.90/5s, sound 1.5x
    KLING_V3_STD = "kling-v3.0-std"
    # HappyHorse 1.0 (Alibaba, via WaveSpeed) - image-to-video, 720p/1080p, 3-15s, $0.14/s (720p)
    HAPPYHORSE_1_0_I2V = "happyhorse-1.0-i2v"
    # HappyHorse 1.1 (Alibaba, via WaveSpeed) - image-to-video, 720p/1080p, 3-15s, $0.70/5s (720p)
    HAPPYHORSE_1_1_I2V = "happyhorse-1.1-i2v"

    # OpenAI Sora
    SORA_2 = "sora-2"
    SORA_2_PRO = "sora-2-pro"
    
    # ==================== 音频工具 ====================
    # Suno
    CHIRP_V4_5 = "chirp-v4-5"
    
    # MMAudio (via WaveSpeed)
    MMAUDIO_V2 = "mmaudio-v2"
    
    # Minimax Speech (via WaveSpeed)
    MINIMAX_SPEECH_2_5 = "minimax-speech-2.5"
    
    # Lipsync 2 Pro 唇形同步 (via WaveSpeed)，独立 API：video+audio→synced video
    LIPSYNC_2_PRO = "sync/lipsync-2-pro"


# ==================== Tool 函数名 ====================

class ToolName(str, Enum):
    """运行时注册的工具函数名枚举。
    
    每个值对应一个 @tool("xxx") 装饰器里的字符串，
    是 on_tool_end 的 kwargs["name"] 里拿到的名字。
    """
    # 图像生成 — Nano Banana (Google Gemini)
    NANO_BANANA_T2I = "generate_image_with_nano_banana_t2i"
    NANO_BANANA_I2I = "generate_image_with_nano_banana_i2i"

    # 图像生成 — Seedream (ByteDance)
    SEEDREAM_T2I = "generate_image_with_wavespeed_seedream_t2i"
    SEEDREAM_I2I = "edit_image_with_wavespeed_seedream"

    # 图像生成 — OpenAI GPT Image 2 (WaveSpeed)
    GPT_IMAGE_2_T2I = "generate_image_with_wavespeed_gpt_image_2_t2i"
    GPT_IMAGE_2_I2I = "edit_image_with_wavespeed_gpt_image_2"

    # 视频生成 — Seedance (ByteDance)
    SEEDANCE_V1 = "generate_video_with_wavespeed_seedance"
    SEEDANCE_V1_5 = "generate_video_with_wavespeed_seedance_v1_5"
    SEEDANCE_2_I2V = "generate_video_with_wavespeed_seedance_2_i2v"
    SEEDANCE_2_I2V_TURBO = "generate_video_with_wavespeed_seedance_2_i2v_turbo"
    SEEDANCE_2_FAST_I2V = "generate_video_with_wavespeed_seedance_2_fast_i2v"
    SEEDANCE_2_FAST_I2V_TURBO = "generate_video_with_wavespeed_seedance_2_fast_turbo"
    SEEDANCE_2_T2V = "generate_video_with_wavespeed_seedance_2_t2v"
    SEEDANCE_2_FAST_T2V = "generate_video_with_wavespeed_seedance_2_fast_t2v"
    SEEDANCE_2_T2V_TURBO = "generate_video_with_wavespeed_seedance_2_t2v_turbo"
    SEEDANCE_2_FAST_T2V_TURBO = "generate_video_with_wavespeed_seedance_2_fast_t2v_turbo"

    # 视频生成 — Wan (Alibaba) / LTX Lipsync (WaveSpeed)
    WAN_2_5 = "generate_video_with_wavespeed_wan25"
    WAN_2_6 = "generate_video_with_wavespeed_wan26"
    LIPSYNC_LTX23 = "generate_video_with_wavespeed_ltx23_lipsync"
    LIPSYNC_KLING_V2_AI_AVATAR_PRO = "generate_video_with_wavespeed_kling_v2_ai_avatar_pro"
    LIPSYNC_WAN_22_SPEECH_TO_VIDEO = "generate_video_with_wavespeed_wan22_speech_to_video"

    # 视频生成 — Kling
    KLING_V3 = "generate_video_with_wavespeed_kling"
    HAPPYHORSE_1_0_I2V = "generate_video_with_wavespeed_happyhorse_1_0_i2v"
    HAPPYHORSE_1_1_I2V = "generate_video_with_wavespeed_happyhorse_1_1_i2v"

    # 视频生成 — Sora (OpenAI)
    SORA_2_T2V = "generate_video_with_sora_2_t2v"
    SORA_2_I2V = "generate_video_with_sora_2_i2v"
    SORA_2_PRO_T2V = "generate_video_with_sora_2_pro_t2v"
    SORA_2_PRO_I2V = "generate_video_with_sora_2_pro_i2v"

    # 音乐生成 — Suno
    SUNO = "generate_music_with_suno"

    # 音效生成 — MMAudio
    MMAUDIO = "generate_audio_with_wavespeed"

    # 旁白生成 — Minimax Speech
    MINIMAX_SPEECH = "generate_speech_with_wavespeed"

    # 唇形同步 — Lipsync (独立 API)
    LIPSYNC = "generate_lipsync_with_wavespeed"

    # Wrapper 工具（fallback 链，on_tool_end 也可能触发）
    IMAGE_WRAPPER_T2I = "generate_image_with_fallback_t2i"
    IMAGE_WRAPPER_I2I = "generate_image_with_fallback_i2i"
    VIDEO_WRAPPER_I2V = "generate_video_with_fallback_i2v"
    VIDEO_WRAPPER_REF_T2V = "generate_video_with_fallback_ref_t2v"
    LIPSYNC_WRAPPER_I2V = "generate_lipsync_video_with_fallback_i2v"
    SPEECH_WRAPPER = "generate_speech_with_fallback"


# tool 函数名 → ToolType 的映射（供 callback 成本追踪使用）
# nano_banana 的 t2i / i2i 用同一个 ToolType（成本按 token 算，不区分模式）
# sora 的 t2v / i2v 用同一个 ToolType（成本按分辨率和时长算，不区分模式）
TOOL_NAME_TO_TYPE: dict["ToolName", "ToolType"] = {
    ToolName.NANO_BANANA_T2I:   ToolType.GEMINI_2_5_FLASH_IMAGE,
    ToolName.NANO_BANANA_I2I:   ToolType.GEMINI_2_5_FLASH_IMAGE,
    ToolName.SEEDREAM_T2I:      ToolType.SEEDREAM_V4_5,
    ToolName.SEEDREAM_I2I:      ToolType.SEEDREAM_V4_5,
    ToolName.GPT_IMAGE_2_T2I:   ToolType.GPT_IMAGE_2,
    ToolName.GPT_IMAGE_2_I2I:   ToolType.GPT_IMAGE_2,
    ToolName.SEEDANCE_V1:       ToolType.SEEDANCE_V1_PRO_FAST,
    ToolName.SEEDANCE_V1_5:     ToolType.SEEDANCE_V1_5_PRO_FAST,
    ToolName.SEEDANCE_2_I2V: ToolType.SEEDANCE_2_I2V,
    ToolName.SEEDANCE_2_I2V_TURBO: ToolType.SEEDANCE_2_I2V_TURBO,
    ToolName.SEEDANCE_2_FAST_I2V: ToolType.SEEDANCE_2_FAST_I2V,
    ToolName.SEEDANCE_2_FAST_I2V_TURBO: ToolType.SEEDANCE_2_FAST_I2V_TURBO,
    ToolName.SEEDANCE_2_T2V: ToolType.SEEDANCE_2_T2V,
    ToolName.SEEDANCE_2_FAST_T2V: ToolType.SEEDANCE_2_FAST_T2V,
    ToolName.SEEDANCE_2_T2V_TURBO: ToolType.SEEDANCE_2_T2V_TURBO,
    ToolName.SEEDANCE_2_FAST_T2V_TURBO: ToolType.SEEDANCE_2_FAST_T2V_TURBO,
    ToolName.WAN_2_5:           ToolType.WAN_2_5_I2V,
    ToolName.WAN_2_6:           ToolType.WAN_2_6_FLASH_I2V,
    ToolName.KLING_V3:          ToolType.KLING_V3_STD,
    ToolName.HAPPYHORSE_1_0_I2V: ToolType.HAPPYHORSE_1_0_I2V,
    ToolName.HAPPYHORSE_1_1_I2V: ToolType.HAPPYHORSE_1_1_I2V,
    ToolName.SORA_2_T2V:        ToolType.SORA_2,
    ToolName.SORA_2_I2V:        ToolType.SORA_2,
    ToolName.SORA_2_PRO_T2V:    ToolType.SORA_2_PRO,
    ToolName.SORA_2_PRO_I2V:    ToolType.SORA_2_PRO,
    ToolName.SUNO:              ToolType.CHIRP_V4_5,
    ToolName.MMAUDIO:           ToolType.MMAUDIO_V2,
    ToolName.MINIMAX_SPEECH:    ToolType.MINIMAX_SPEECH_2_5,
    ToolName.LIPSYNC:           ToolType.LIPSYNC_2_PRO,
    ToolName.LIPSYNC_LTX23:     ToolType.LTX_2_3_LIPSYNC,
    ToolName.LIPSYNC_KLING_V2_AI_AVATAR_PRO: ToolType.KLING_V2_AI_AVATAR_PRO,
    ToolName.LIPSYNC_WAN_22_SPEECH_TO_VIDEO: ToolType.WAN_2_2_SPEECH_TO_VIDEO,
    # wrapper 工具不直接映射到 ToolType（它们内部会调用叶子 tool，叶子 tool 会各自触发 on_tool_end）
}


# ==================== 提供商 ====================

class ToolProvider(str, Enum):
    """工具提供商枚举"""
    GOOGLE = "google"  # Google Gemini（Nano Banana 图像生成）
    WAVESPEED = "wavespeed"
    OPENAI = "openai"
    SUNO = "suno"
    USER_UPLOAD = "user_upload"  # 用户上传的图片


# ==================== 工具分类 ====================

class ToolCategory(str, Enum):
    """工具分类（用于区分功能领域）"""
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"
    MUSIC_GENERATION = "music_generation"
    AUDIO_EFFECT = "audio_effect"
    SPEECH_SYNTHESIS = "speech_synthesis"
    LIPSYNC = "lipsync"
    TRANSCRIPTION = "transcription"


# ToolType → ToolCategory 的映射（供 callback 按分类统计用，新增 ToolType 时在此维护）
TOOL_TYPE_TO_CATEGORY: dict[ToolType, ToolCategory] = {
    ToolType.GEMINI_2_5_FLASH_IMAGE:         ToolCategory.IMAGE_GENERATION,
    ToolType.GEMINI_3_PRO_IMAGE_PREVIEW:     ToolCategory.IMAGE_GENERATION,
    ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW: ToolCategory.IMAGE_GENERATION,
    ToolType.SEEDREAM_V4_5:                  ToolCategory.IMAGE_GENERATION,
    ToolType.GPT_IMAGE_2:                  ToolCategory.IMAGE_GENERATION,
    ToolType.SEEDANCE_V1_PRO_FAST:       ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_V1_5_PRO_FAST:     ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_2_I2V: ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_2_I2V_TURBO: ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_2_FAST_I2V: ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_2_FAST_I2V_TURBO: ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_2_T2V: ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_2_FAST_T2V: ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_2_T2V_TURBO: ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_2_FAST_T2V_TURBO: ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_V1_LITE_I2V_480P:  ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_V1_LITE_I2V_720P:  ToolCategory.VIDEO_GENERATION,
    ToolType.SEEDANCE_V1_LITE_I2V_1080P: ToolCategory.VIDEO_GENERATION,
    ToolType.WAN_2_5_I2V:                ToolCategory.VIDEO_GENERATION,
    ToolType.WAN_2_6_FLASH_I2V:          ToolCategory.VIDEO_GENERATION,
    ToolType.KLING_V3_STD:               ToolCategory.VIDEO_GENERATION,
    ToolType.HAPPYHORSE_1_0_I2V:         ToolCategory.VIDEO_GENERATION,
    ToolType.HAPPYHORSE_1_1_I2V:         ToolCategory.VIDEO_GENERATION,
    ToolType.SORA_2:                     ToolCategory.VIDEO_GENERATION,
    ToolType.SORA_2_PRO:                 ToolCategory.VIDEO_GENERATION,
    ToolType.CHIRP_V4_5:                 ToolCategory.MUSIC_GENERATION,
    ToolType.MMAUDIO_V2:                 ToolCategory.AUDIO_EFFECT,
    ToolType.MINIMAX_SPEECH_2_5:         ToolCategory.SPEECH_SYNTHESIS,
    ToolType.LIPSYNC_2_PRO:              ToolCategory.LIPSYNC,
    ToolType.LTX_2_3_LIPSYNC:            ToolCategory.LIPSYNC,
    ToolType.KLING_V2_AI_AVATAR_PRO:     ToolCategory.LIPSYNC,
    ToolType.WAN_2_2_SPEECH_TO_VIDEO:    ToolCategory.LIPSYNC,
}


# ==================== 分辨率（统一） ====================

class Resolution(str, Enum):
    """分辨率枚举（统一使用）"""
    P480 = "480p"
    P720 = "720p"
    P1080 = "1080p"


# ==================== 宽高比（统一） ====================

class AspectRatio(str, Enum):
    """宽高比枚举（统一使用）"""
    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"


# 产品目标像素：Resolution × AspectRatio → (width, height)
# 图像生成可据此选 API 档位并下采样到此处像素（Nano Banana 等）
TARGET_PIXELS: dict[tuple[Resolution, AspectRatio], tuple[int, int]] = {
    (Resolution.P480, AspectRatio.LANDSCAPE): (854, 480),
    (Resolution.P480, AspectRatio.PORTRAIT): (480, 854),
    (Resolution.P480, AspectRatio.SQUARE): (480, 480),
    (Resolution.P720, AspectRatio.LANDSCAPE): (1280, 720),
    (Resolution.P720, AspectRatio.PORTRAIT): (720, 1280),
    (Resolution.P720, AspectRatio.SQUARE): (720, 720),
    (Resolution.P1080, AspectRatio.LANDSCAPE): (1920, 1080),
    (Resolution.P1080, AspectRatio.PORTRAIT): (1080, 1920),
    (Resolution.P1080, AspectRatio.SQUARE): (1080, 1080),
}


# ==================== 内容类别（模板） ====================

class GenerationMode(str, Enum):
    """视频生成模式：决定该镜头使用何种方式生成"""
    NORMAL = "normal"           # 普通镜头：image → video（当前方式）
    LIPSYNC = "lipsync"        # 唇形同步镜头：image + audio → lipsync video（wan audio_url）
    EMPTY_SHOT = "empty_shot"   # 空镜：无人物角色，仅环境/物品，走普通视频工具，不参与 lipsync 候选


class AudioSegmentGranularity(str, Enum):
    """音频转录 segment 切分粒度（与 TRANSCRIPTION_METHOD 正交，决定边界按什么规则切）。

    - PHRASE   : 按乐句、情绪、节奏变化切；可合并多句到一段。
    - SENTENCE : 默认。完整语义句（语法/语义独立一句）≈ 1 segment；句内换气/短停顿须合并；端点对齐词边界。
    - BEAT     : 边界优先对齐 4/4 小节线 (bar_sec = 240/BPM)；
                 有人声时不切在字中间；无 BPM 时降级为 PHRASE。
    """
    PHRASE = "phrase"
    SENTENCE = "sentence"
    BEAT = "beat"

    @classmethod
    def from_value(cls, value: "str | AudioSegmentGranularity | None") -> "AudioSegmentGranularity":
        """统一将 None/str/enum 转为 enum；None 回退 SENTENCE，未知值回退 PHRASE。"""
        if value is None:
            return cls.SENTENCE
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            raw = value.strip().lower()
            for m in cls:
                if m.value == raw:
                    return m
        return cls.PHRASE


class ContentCategory(str, Enum):
    """内容类别：用于模板级规则与参数选择（与 aspect_ratio 等一致用 enum）"""
    DEFAULT = "Default"             # 默认，故事性 MV
    LIP_SYNC_MV = "Lip-Sync MV"    # 模板：全对嘴、多场景、固定正脸/拿话筒
    PRODUCT_LAUNCH = "Product Launch"  # 模板：talking head + TTS 旁白 + 字幕
    SHORT_DRAMA = "Short Drama"  # 模板：短剧 — Seedance 片内对白 + 稀疏 TTS 旁白

    @classmethod
    def from_value(cls, value: "str | ContentCategory | None") -> "ContentCategory":
        """将 LLM/前端可能返回的 str 或 enum 统一转为 enum。None/空串 -> DEFAULT。"""
        if value is None:
            return cls.DEFAULT
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if raw == cls.LIP_SYNC_MV.value:
                return cls.LIP_SYNC_MV
            if raw == cls.PRODUCT_LAUNCH.value:
                return cls.PRODUCT_LAUNCH
            if raw == cls.SHORT_DRAMA.value:
                return cls.SHORT_DRAMA
            return cls.DEFAULT
        return cls.DEFAULT

    @classmethod
    def to_value(cls, value: "str | ContentCategory | None") -> str:
        """转为字符串值（存 DB、模板用）。None/空串 -> Default。"""
        return cls.from_value(value).value


# ==================== 默认值常量 ====================

# 图像模型 -> 用户选项 tool name，改默认时只改 DefaultValues.IMAGE_MODEL，此处自动一致
_IMAGE_MODEL_TO_TOOL_NAME = {
    ToolType.GEMINI_2_5_FLASH_IMAGE: "nano_banana",
    ToolType.GEMINI_3_PRO_IMAGE_PREVIEW: "nano_banana_pro",
    ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW: "nano_banana_2",  # 独立选项，3 个 nano banana 模型
    ToolType.GPT_IMAGE_2: "gpt_image_2",
}


class DefaultValues:
    """默认值常量类"""
    # 图像生成默认值（改默认模型只改 IMAGE_MODEL 一处，其余自动一致）
    IMAGE_ASPECT_RATIO = AspectRatio.LANDSCAPE  # "16:9"
    IMAGE_RESOLUTION = Resolution.P1080  # "1080p"
    IMAGE_MODEL = ToolType.GPT_IMAGE_2  # 默认 GPT Image 2："gpt-image-2"（实测最稳，唯一支持 location 参考图不出"巨人"问题）
    IMAGE_MODEL_PRO = ToolType.GEMINI_3_PRO_IMAGE_PREVIEW  # "gemini-3-pro-image-preview"
    DEFAULT_IMAGE_TOOL_NAME = _IMAGE_MODEL_TO_TOOL_NAME[IMAGE_MODEL]  # "nano_banana" | "nano_banana_2" | "nano_banana_pro"
    
    # 视频生成默认值
    VIDEO_ASPECT_RATIO = AspectRatio.LANDSCAPE  # "16:9"
    VIDEO_RESOLUTION = Resolution.P1080  # "1080p"
    VIDEO_DURATION = 30  # 默认视频时长（秒）

    # Lipsync 视频生成默认值（fallback 工具，当用户选的工具不支持 lipsync 时使用）
    DEFAULT_LIPSYNC_TOOL_VALUE: str = "wan_2_6_flash"  # Wan 2.6 Flash（lipsync fallback 默认）
    
    # 语言默认值
    DEFAULT_LANGUAGE = "en"  # 默认语言代码（ISO 639-1）

    # 音频转录方法（灰度开关）
    # - "gemini": 仅 Gemini（与历史行为一致；不调 Suno）
    # - "hybrid": 串行 Suno→Gemini：先 Suno 拿词级时间戳，把歌词喂给 Gemini 做参考转录，
    #            然后合并（端点吸附到 Suno 词边界 + vocal_mask 校正 + 词级时间戳注入，全程以 Suno 为准）。
    #            延迟 ≈ Suno + Gemini，约 127s（user upload）/ 87s（Suno generated 复用 clip_id 跳过 upload）。
    #            Suno 失败/版权风控时自动降级到纯 Gemini，对下游零感知。
    TRANSCRIPTION_METHOD: str = "hybrid"

    # hybrid 词级对齐 provider（仅 TRANSCRIPTION_METHOD="hybrid" 生效；与纯 gemini 路径无关）
    # - "mureka": Mureka V7.6 recognize-song（默认；走 WaveSpeed，无 upload 步骤；无曲式/性别 hint，
    #            但直接给词级时间戳，reshape 后与 Suno 路径走同一条 generated_lyrics + alignment_context 注入）
    #            实测对齐质量优于 Suno，故设为默认。
    # - "suno":   Suno Sonic upload + aligned-lyrics（历史行为；带曲式/人声性别 hint）
    #            任一 provider 失败都自动降级到纯 Gemini，对下游零感知。
    ALIGNMENT_PROVIDER: str = "mureka"

    # 兜底 granularity（见 agent_config.transcription.TRANSCRIPTION_ENGINE_PROFILES）
    AUDIO_SEGMENT_GRANULARITY: str = AudioSegmentGranularity.SENTENCE.value


def get_target_pixels_for_video(
    resolution: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
) -> Tuple[int, int]:
    """根据 resolution、aspect_ratio 查表得到 (width, height)，与工具层/WaveSpeed 一致。
    供工具层与 sync 兜底迁移共用，后续改 TARGET_PIXELS 或默认只改此处。"""
    try:
        res_enum = Resolution(resolution) if resolution else DefaultValues.VIDEO_RESOLUTION
    except ValueError:
        res_enum = DefaultValues.VIDEO_RESOLUTION
    try:
        ar_enum = AspectRatio(aspect_ratio) if aspect_ratio else DefaultValues.VIDEO_ASPECT_RATIO
    except ValueError:
        ar_enum = DefaultValues.VIDEO_ASPECT_RATIO
    target = TARGET_PIXELS.get((res_enum, ar_enum))
    if target:
        return target
    return (1920, 1080)
