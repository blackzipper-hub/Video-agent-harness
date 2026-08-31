"""
视频其他相关Schema - 使用msgspec.Struct

- VideoLipsyncGenerationDB + VersionDB: 唇形同步 + 版本
- VideoAnalysisDB: 视频分析
- VideoAssemblyDB: 视频合成
"""

import msgspec
from datetime import datetime
from typing import Optional, Dict, Any, List


class VideoLipsyncGenerationDB(msgspec.Struct, kw_only=True):
    """视频唇形同步生成数据库模型 - 对应 video_lipsync_generations 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 唇形同步生成唯一标识
    
    # === 元数据字段 ===
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 关联关系字段 ===
    video_segment_id: str  # 关联的视频片段UUID
    music_generation_id: str  # 关联的音乐生成UUID
    
    # === 唇形同步核心信息字段 ===
    segment_number: int  # 片段编号
    current_version_index: int = 0  # 当前版本索引
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoLipsyncGenerationVersionDB(msgspec.Struct, kw_only=True):
    """视频唇形同步生成版本数据库模型 - 对应 video_lipsync_generation_versions 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 唇形同步版本唯一标识
    
    # === 关联字段 ===
    lipsync_generation_id: str  # 关联的唇形同步生成UUID (外键)
    video_segment_id: str  # 关联的视频片段UUID
    video_segment_version_id: str  # 关联的视频片段版本UUID
    music_generation_version_id: str  # 关联的音乐生成版本UUID
    
    # === 元数据字段 ===
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 版本信息 ===
    version_number: int  # 版本号
    segment_number: int  # 片段编号
    
    # === 唇形同步结果 ===
    video_url: str  # 唇形同步后的视频URL
    audio_url: str  # 用于唇形同步的音频URL
    original_video_url: str  # 原始视频URL（合并后的视频片段）
    
    # === 服务提供商信息 ===
    provider: str  # 唇形同步服务提供商
    model: str  # 使用的唇形同步模型
    
    # === 执行结果 ===
    success: bool = True  # 唇形同步是否成功
    error_msg: Optional[str] = None  # 用户友好的错误消息（不含技术细节）
    raw_error_msg: Optional[str] = None  # 原始错误信息（仅供调试，不返回前端）
    duration: Optional[float] = None  # 视频时长（秒）
    
    # === 生成参数 ===
    params: Optional[Dict[str, Any]] = None  # 唇形同步参数
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoAnalysisDB(msgspec.Struct, kw_only=True):
    """视频分析结果数据库模型 - 对应 video_analysis 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 唯一标识
    
    # === 分析结果字段 ===
    video_type: str  # 视频类型（animated_short, promotional等）
    duration: float  # 视频时长（秒），读自 COALESCE(duration_sec, duration)
    main_character: str  # 主要角色
    purpose: str  # 视频目的
    key_elements: Optional[str] = None  # 关键元素（JSON字符串）
    style_preferences: Optional[str] = None  # 风格偏好（JSON字符串）
    target_audience: Optional[str] = None  # 目标受众
    next_action: str  # 下一步操作
    content_category: Optional[str] = None  # 内容类别：Default / Lip-Sync MV 等（与 video_analysis 表一致）
    
    # === 元数据 ===
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    
    # === 精选风格（可选，DB 未加列时读路径安全） ===
    hidden_style_description: Optional[str] = None  # 从风格库取出的描述，供下游生成用
    curated_style_prompt_id: Optional[str] = None  # 关联的精选风格 uuid（追溯用）

    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外分析数据

    # === 时间戳 ===
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间


class VideoAssemblyDB(msgspec.Struct, kw_only=True):
    """视频合成数据库模型 - 对应 video_assemblies 表（与SQLModel完全对齐）"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 视频合成唯一标识
    
    # === 元数据字段 ===
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    story_outline_id: str  # 关联的故事大纲UUID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 视频合成核心信息字段 ===
    final_video_url: str  # 最终合成的视频URL（有字幕）
    final_video_url_no_subtitle: Optional[str] = None  # 最终合成的视频URL（无字幕版本）
    total_duration: float  # 总时长（秒）- 对应SQLModel必需字段
    success: bool = True  # 合成是否成功
    error_msg: Optional[str] = None  # 用户友好的错误消息（不含技术细节）
    raw_error_msg: Optional[str] = None  # 原始错误信息（仅供调试，不返回前端）
    
    # === 拼接使用的源头资源版本ID ===
    source_video_versions: Optional[Dict[int, str]] = None  # 镜头编号 -> 视频版本UUID
    source_narration_versions: Optional[Dict[int, str]] = None  # 镜头编号 -> 旁白版本UUID
    source_audio_effect_versions: Optional[Dict[int, str]] = None  # 镜头编号 -> 音效版本UUID
    source_music_versions: Optional[Dict[str, str]] = None  # 音频片段ID -> 音乐版本UUID
    
    # === 音乐和音频资源 ===
    uploaded_audio_files: Optional[List[str]] = None  # 用户上传的音频文件URL列表
    
    # === 源头资源URL ===
    source_video_urls: Optional[Dict[int, str]] = None  # 镜头编号 -> 原始视频URL
    source_narration_urls: Optional[Dict[int, str]] = None  # 镜头编号 -> 旁白音频URL
    source_audio_effect_urls: Optional[Dict[int, str]] = None  # 镜头编号 -> 音效音频URL
    source_music_urls: Optional[Dict[str, str]] = None  # 音频片段ID -> 音乐音频URL
    
    # === 拼接模式记录 ===
    assembly_mode: Optional[str] = None  # 拼接模式: narration_driven, audio_driven, video_driven
    title: Optional[str] = None  # 视频标题
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据
