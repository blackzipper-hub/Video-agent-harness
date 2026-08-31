"""
视频片段相关Schema - 使用msgspec.Struct

- VideoSegmentDB: 视频片段主表
- VideoSegmentVersionDB: 视频片段版本表
"""

import msgspec
from datetime import datetime
from typing import Optional, Dict, Any, List


class VideoSegmentDB(msgspec.Struct, kw_only=True):
    """视频片段数据库模型 - 对应 video_segments 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 视频片段唯一标识
    
    # === 元数据字段 ===
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    story_outline_id: str  # 关联的故事大纲UUID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 片段核心信息 ===
    segment_number: int  # 片段编号
    
    # === 关联的资源ID列表（对应SQLModel的JSON字段）===
    video_generation_ids: List[str] = []  # 关联的视频生成UUID列表
    narration_ids: List[str] = []  # 关联的旁白UUID列表
    keyframe_ids: List[str] = []  # 关联的关键帧UUID列表
    scene_ids: List[str] = []  # 关联的场景UUID列表
    storyboard_detail_ids: List[str] = []  # 关联的详细分镜UUID列表
    
    # === 一对一关系的资源ID ===
    music_generation_id: Optional[str] = None  # 关联的音乐生成UUID
    audio_effect_id: Optional[str] = None  # 关联的音效生成UUID
    lipsync_id: Optional[str] = None  # 关联的唇形同步生成UUID
    
    # === 版本管理 ===
    current_version_index: int = 0  # 当前版本索引
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoSegmentVersionDB(msgspec.Struct, kw_only=True):
    """视频片段版本数据库模型 - 对应 video_segment_versions 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 片段版本唯一标识
    
    # === 关联字段 ===
    video_segment_id: str  # 关联的视频片段UUID (外键)
    
    # === 元数据字段 ===
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 版本信息 ===
    version_number: int  # 版本号
    segment_number: int  # 片段编号（对应SQLModel，不是shot_number）
    
    # === 合成结果 ===
    video_url: str  # 合成后的视频URL（对应SQLModel，不是segment_url）
    success: bool = True  # 合成是否成功
    error_msg: Optional[str] = None  # 用户友好的错误消息（不含技术细节）
    raw_error_msg: Optional[str] = None  # 原始错误信息（仅供调试，不返回前端）
    
    # === 关联资源版本ID列表（对应SQLModel的JSON字段）===
    video_generation_version_ids: List[str] = []  # 关联的视频生成版本UUID列表
    narration_version_ids: List[str] = []  # 关联的旁白版本UUID列表
    keyframe_version_ids: List[str] = []  # 关联的关键帧版本UUID列表
    
    # === 一对一关系的资源版本ID ===
    music_generation_version_id: Optional[str] = None  # 音乐版本UUID
    audio_effect_version_id: Optional[str] = None  # 音效版本UUID
    lipsync_version_id: Optional[str] = None  # 唇形同步版本UUID
    
    # === 合成参数 ===
    duration: Optional[float] = None  # 视频时长（秒）
    fps: Optional[int] = None  # 帧率
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据
