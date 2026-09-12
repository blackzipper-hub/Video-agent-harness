"""
图像和视频生成的 Context Schema 定义

用于 create_agent 的 context_schema 参数，通过 context=Context(...) 传递
使用 dataclass 定义，供轻量运行时上下文注入。
"""
from dataclasses import dataclass, field
from typing import Optional, List, Any, Dict

from ..models.tool_enums import AspectRatio, Resolution, ToolType, DefaultValues


@dataclass
class ImageGenerationContext:
    """图像生成上下文
    
    用于 create_agent 的 context_schema 参数，通过 context=ImageGenerationContext(...) 传递
    在 tool 中通过 ToolRuntime[ImageGenerationContext] 和 runtime.context 访问
    """
    aspect_ratio: AspectRatio = field(default=DefaultValues.IMAGE_ASPECT_RATIO)  # 宽高比
    resolution: Resolution = field(default=DefaultValues.IMAGE_RESOLUTION)  # 分辨率
    reference_image_urls: Optional[List[str]] = None  # 参考图片URL列表（用于 i2i 或风格参考）
    reference_image_labels: Optional[List[Dict[str, Any]]] = None  # 与 reference_image_urls 一一对应：index/name/type_label/description/appearance/style/body_type/role，供一致性校验 prompt 使用
    model: Optional[ToolType] = None  # 具体模型类型（可选），如 ToolType.GEMINI_2_5_FLASH_IMAGE
    language: Optional[str] = None  # 用户语言（ISO 639-1），供 tool 内 i18n 文案使用，由 runtime 穿透
    skip_consistency_check: bool = False  # 跳过角色一致性校验（角色图片生成时 prompt 会主动改变角色，校验必定失败）


@dataclass
class VideoGenerationContext:
    """视频生成上下文
    
    用于 create_agent 的 context_schema 参数，通过 context=VideoGenerationContext(...) 传递
    在 tool 中通过 ToolRuntime[VideoGenerationContext] 和 runtime.context 访问
    """
    aspect_ratio: AspectRatio = field(default=DefaultValues.VIDEO_ASPECT_RATIO)  # 宽高比
    resolution: Resolution = field(default=DefaultValues.VIDEO_RESOLUTION)  # 分辨率
    start_image_url: Optional[str] = None  # 首帧图片URL（优先级高于 LLM 传入的参数）
    end_image_url: Optional[str] = None  # 尾帧图片URL（优先级高于 LLM 传入的参数）
    audio_url: Optional[str] = None  # 音频URL（lipsync 模式：传给 wan tool 的 audio_url）
    reference_images: Optional[List[str]] = None  # T2V 参考图片URL列表（用户上传/历史，优先级高于 LLM 传入的参数）
    reference_videos: Optional[List[str]] = None  # T2V 参考视频URL列表（总时长 ≤ 15s，优先级高于 LLM 传入的参数）
    reference_audios: Optional[List[str]] = None  # T2V 参考音频URL列表（总时长 ≤ 15s，优先级高于 LLM 传入的参数）
    duration: Optional[int] = None  # 视频时长秒数（优先级高于 LLM 传入的参数；lipsync 模式用 ceil(audio_duration)）
    language: Optional[str] = None  # 用户语言（ISO 639-1），供 tool 内 i18n 文案使用，由 runtime 穿透
    character_ref_image_urls: Optional[List[str]] = None  # 本镜头依赖的角色参考图 URL 列表，仅用于视频一致性校验（不参与生成）
    character_ref_labels: Optional[List[Dict[str, Any]]] = None  # 与 character_ref_image_urls 一一对应：index/name/type_label/description/appearance/style/body_type/role，供一致性校验 prompt 使用
    skip_consistency_check: bool = False  # 跳过视频一致性校验（regenerate 场景下不跑 wrapper 内置 check）
    # 片内对白：有角色台词时 Seedance I2V 传 generate_audio=True，上传 S3 时不剥音轨。
    # 对白正文由 video-director skill 写入 i2v_prompt（说：「」/ Character says），不在 Python 注入。
    generate_audio: bool = False


@dataclass
class SpeechGenerationContext:
    """语音合成上下文

    用于 create_agent 的 context_schema 参数，通过 context=SpeechGenerationContext(...) 传递
    在 tool 中通过 ToolRuntime[SpeechGenerationContext] 和 runtime.context 访问
    """
    target_duration: Optional[float] = None  # 镜头目标时长（秒），wrapper 用于 ±0.5s 校验与 speed 重试
    detected_language: Optional[str] = None  # 用户语言（ISO 639-1），供默认 voice 选择
    default_voice_id: Optional[str] = None  # 按镜头角色推断的声线；优先级最高，覆盖 LLM 选择
    speaker_gender: Optional[str] = None  # 讲解者性别：'f' 女声 / 'm' 男声；用于校验 LLM 是否选错声线
    shot_number: Optional[int] = None  # 镜头编号（metrics / 日志）
