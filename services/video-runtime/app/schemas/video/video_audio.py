"""
视频音频相关Schema - 使用msgspec.Struct

- VideoAudioTranscriptionDB: 音频转录
- VideoAudioEffectDB + VersionDB: 音效 + 版本
- VideoMusicGenerationDB + VersionDB: 音乐 + 版本  
- VideoNarrationDB + VersionDB: 旁白 + 版本
"""

import msgspec
from datetime import datetime
from typing import Optional, Dict, Any, List


class VideoAudioTranscriptionDB(msgspec.Struct, kw_only=True):
    """音频转录数据库模型 - 对应 video_audio_transcriptions 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 音频转录唯一标识
    
    # === 元数据字段 ===
    user_id: str  # 用户ID
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 转录字段（对应SQLModel）===
    task: str  # 任务类型（transcribe, translate等）
    language: str  # 检测到/指定的语言
    duration: float  # 总音频时长（秒）
    text: str  # 完整转录文本（对应SQLModel的text字段）
    audio_url: str  # 原始音频文件URL（对应SQLModel的audio_url字段）
    filename: Optional[str] = None  # 原始音频文件名
    is_instrumental: bool = False  # 是否是纯音乐（无歌词）
    
    # === Global 整曲级（音乐 MV 三层设计）===
    song_name: Optional[str] = None  # 歌曲名称
    global_bpm: Optional[float] = None  # 整曲 BPM
    genre: Optional[str] = None  # 音乐流派
    global_emotion: Optional[str] = None  # 听觉整体基调
    suggested_global_theme: Optional[str] = None  # 建议核心设计理念
    suggested_color_palette: Optional[str] = None  # 建议整体色彩倾向
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoAudioSegmentDB(msgspec.Struct, kw_only=True):
    """音频片段数据库模型 - 对应 video_audio_segment 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 音频片段唯一标识
    
    # === 元数据字段 ===
    user_id: str  # 用户ID
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 音频片段字段 ===
    transcription_uuid: str  # 关联的转录UUID
    segment_id: int  # 片段编号
    start: float  # 开始时间（秒）
    end: float  # 结束时间（秒）
    duration: float  # 持续时间（秒）
    text: str  # 片段文本
    emotion: Optional[str] = None  # 情感标签
    tempo: Optional[str] = None  # 节奏标签
    vocal_presence: Optional[bool] = None  # 是否有人声演唱（lipsync 用）
    vocal_gender: Optional[str] = None  # 人声性别：'f' 女声，'m' 男声

    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoAudioSectionDB(msgspec.Struct, kw_only=True):
    """音频段落数据库模型 - 对应 video_audio_section 表（无外键，仅索引）"""
    
    id: int
    uuid: str
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    transcription_uuid: str
    section_type: str
    start_time: float
    end_time: float
    musical_features: Optional[str] = None
    section_emotion: Optional[str] = None
    suggested_visual_intensity: Optional[str] = None
    suggested_rhythmic_strategy: Optional[str] = None
    suggested_visual_theme: Optional[str] = None
    suggested_context: Optional[str] = None
    additional_data: Optional[Dict[str, Any]] = None


class VideoAudioEffectDB(msgspec.Struct, kw_only=True):
    """视频音效数据库模型 - 对应 video_audio_effects 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 音效唯一标识
    
    # === 元数据字段 ===
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    story_outline_id: str  # 关联的故事大纲UUID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 关联关系字段 ===
    scene_id: str  # 关联的场景UUID
    storyboard_detail_id: str  # 关联的详细分镜UUID
    detailed_shot_id: str  # 关联的详细镜头UUID
    video_generation_id: str  # 关联的视频生成UUID
    
    # === 音效核心信息字段 ===
    shot_number: int  # 镜头编号
    is_bridge: bool = False  # 是否是衔接片段
    has_audio_effect: bool = False  # 是否有音效
    current_version_index: int = 0  # 当前版本索引
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoAudioEffectVersionDB(msgspec.Struct, kw_only=True):
    """视频音效版本数据库模型 - 对应 video_audio_effect_versions 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 音效版本唯一标识
    
    # === 元数据字段 ===
    audio_effect_id: str  # 关联的音效UUID (外键)
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 版本核心信息 ===
    version_number: int  # 版本号
    shot_number: int  # 镜头编号
    video_url: str  # 输入视频URL
    audio_prompt: str  # 音效描述提示词
    enhanced_prompt: str  # 增强的音效提示词
    audio_url: Optional[str] = None  # 生成的纯音效音频URL
    video_with_audio_url: Optional[str] = None  # 视频+音效合成URL
    provider: str = "wavespeed"  # 音效生成服务提供商
    duration: Optional[float] = None  # 音频时长（秒）
    is_bridge: bool = False  # 是否是衔接片段
    success: bool = True  # 生成是否成功
    error_msg: Optional[str] = None  # 用户友好的错误消息（LLM生成，不含供应商信息）
    raw_error_msg: Optional[str] = None  # 原始错误信息（仅供调试，不返回前端）
    
    # === 通用参数字段 ===
    params: Optional[Dict[str, Any]] = None  # 生成参数（根据provider不同而不同）
    
    # === AI生成信息 ===
    ai_messages: Optional[str] = None  # AI生成过程的消息记录（JSON格式）
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoMusicGenerationDB(msgspec.Struct, kw_only=True):
    """音乐生成数据库模型 - 对应 video_music_generations 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 音乐生成唯一标识
    
    # === 元数据字段 ===
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    story_outline_id: Optional[str] = None  # 关联的故事大纲UUID（前置阶段为None）
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 关联关系字段 ===
    scene_id: Optional[str] = None  # 关联的场景UUID（用户上传音频转录时）
    
    # === 音乐生成核心信息字段 ===
    shot_number: Optional[int] = None  # 镜头编号（用户上传音频转录时对应场景）
    is_full_story_music: bool = False  # 是否是整个故事的背景音乐（Suno生成）
    is_instrumental: bool = True  # 是否是纯音乐（无歌词）
    current_version_index: int = 0  # 当前版本索引
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoMusicGenerationVersionDB(msgspec.Struct, kw_only=True):
    """音乐生成版本数据库模型 - 对应 video_music_generation_versions 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 音乐版本唯一标识
    
    # === 元数据字段 ===
    music_generation_id: str  # 关联的音乐生成UUID (外键)
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 版本核心信息 ===
    version_number: int  # 版本号
    shot_number: Optional[int] = None  # 镜头编号
    audio_segment_id: Optional[str] = None  # 对应的音频片段ID（单片段兼容）
    audio_segment_ids: Optional[List[str]] = None  # 对应的音频片段ID列表（与DB表一致）
    audio_transcription_id: Optional[str] = None  # 音频转录ID
    music_prompt: str  # 音乐生成提示词
    music_url: Optional[str] = None  # 生成的音乐URL
    provider: str = "suno"  # 音乐生成服务提供商
    duration: Optional[float] = None  # 音乐时长（秒）
    is_instrumental: bool = True  # 是否为纯音乐（无歌词）
    success: bool = True  # 生成是否成功
    error_msg: Optional[str] = None  # 错误消息
    raw_error_msg: Optional[str] = None  # 原始错误信息
    
    # === 生成参数 ===
    model: Optional[str] = None  # 使用的音乐模型
    style: Optional[str] = None  # 音乐风格
    tempo: Optional[str] = None  # 节奏
    mood: Optional[str] = None  # 情绪
    params: Optional[Dict[str, Any]] = None  # 生成参数（与DB表一致，根据provider不同而不同）

    # === 音频截断信息（用户上传音频时） ===
    original_audio_url: Optional[str] = None  # 原始完整音频URL（用户上传时）
    segment_start_time: Optional[float] = None  # 音频片段开始时间（秒）
    segment_end_time: Optional[float] = None  # 音频片段结束时间（秒）

    # === AI生成信息 ===
    ai_messages: Optional[str] = None  # AI生成过程的消息记录（JSON格式）

    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoNarrationDB(msgspec.Struct, kw_only=True):
    """视频旁白数据库模型 - 对应 video_narrations 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 旁白唯一标识
    
    # === 元数据字段 ===
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    story_outline_id: str  # 关联的故事大纲UUID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 关联关系字段 ===
    scene_id: str  # 关联的场景UUID
    storyboard_detail_id: str  # 关联的详细分镜UUID
    detailed_shot_id: str  # 关联的详细镜头UUID
    
    # === 旁白核心信息字段 ===
    shot_number: int  # 镜头编号
    is_bridge: bool = False  # 是否是衔接片段
    has_narration: bool = False  # 是否有旁白
    current_version_index: int = 0  # 当前版本索引
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoNarrationVersionDB(msgspec.Struct, kw_only=True):
    """视频旁白版本数据库模型 - 对应 video_narration_versions 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 旁白版本唯一标识
    
    # === 元数据字段 ===
    narration_id: str  # 关联的旁白UUID (外键)
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 版本核心信息 ===
    version_number: int  # 版本号
    shot_number: int  # 镜头编号
    narration_text: str  # 旁白文本
    enhanced_prompt: str  # 增强的旁白提示词
    audio_url: Optional[str] = None  # 生成的旁白音频URL
    provider: str = "wavespeed"  # 语音合成服务提供商
    duration: Optional[float] = None  # 音频时长（秒）
    is_bridge: bool = False  # 是否是衔接片段
    success: bool = True  # 生成是否成功
    error_msg: Optional[str] = None  # 错误消息
    raw_error_msg: Optional[str] = None  # 原始错误信息
    
    # === 通用参数字段 ===
    params: Optional[Dict[str, Any]] = None  # 生成参数（根据provider不同而不同，如voice_id, emotion等）
    
    # === AI生成信息 ===
    ai_messages: Optional[str] = None  # AI生成过程的消息记录（JSON格式）
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据
