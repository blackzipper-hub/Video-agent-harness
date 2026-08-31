"""
视频生成相关Schema - 使用msgspec.Struct

- VideoGenerationDB: 视频生成主表（按镜头）
- VideoGenerationVersionDB: 视频版本表；含生成参数字段，供 regenerate 时重建 UserOption
"""

import msgspec
from datetime import datetime
from typing import Optional, Dict, Any, List


class VideoGenerationDB(msgspec.Struct, kw_only=True):
    """视频生成数据库模型 - 对应 video_generations 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 视频生成唯一标识
    
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
    keyframe_id: Optional[str] = None  # 关联的关键帧UUID；reference_t2v 跳过关键帧时可为空
    keyframe_ids: Optional[List[str]] = None  # 关联的关键帧UUID列表（新架构使用：首帧+尾帧）
    
    # === 视频生成核心信息字段 ===
    shot_number: int  # 镜头编号
    is_bridge: bool = False  # 是否是衔接片段
    current_version_index: int = 0  # 当前版本索引
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoGenerationVersionDB(msgspec.Struct, kw_only=True):
    """视频生成版本数据库模型 - 对应 video_generation_versions 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 视频生成版本唯一标识
    
    # === 元数据字段 ===
    video_generation_id: str  # 关联的视频生成UUID (外键)
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 版本核心信息 ===
    version_number: int  # 版本号
    shot_number: int  # 镜头编号
    video_url: str  # 生成的视频URL
    provider: str  # 生成服务提供商
    is_bridge: bool = False  # 是否是衔接片段
    success: bool = True  # 生成是否成功
    error_msg: Optional[str] = None  # 用户友好的错误消息
    raw_error_msg: Optional[str] = None  # 原始错误信息（不返回前端）
    
    # === 生成参数（prompt / 输入 / 时长；regenerate 时用于重建 UserOption） ===
    keyframe_url: Optional[str] = None  # 使用的关键帧URL
    keyframe_version_ids: Optional[List[str]] = None  # 关键帧版本UUID列表（首帧+尾帧）
    motion_prompt: Optional[str] = None  # 运动提示词
    duration: Optional[float] = None  # 视频时长（秒）
    fps: Optional[int] = None  # 帧率
    audio_segment_ids: Optional[List[str]] = None  # 关联的音频片段ID列表
    
    # === 生成细节参数 ===
    model: Optional[str] = None  # 使用的AI模型名称
    aspect_ratio: Optional[str] = None  # 宽高比
    resolution: Optional[str] = None  # 分辨率 480p / 720p / 1080p
    negative_prompt: Optional[str] = None  # 负面提示词
    style: Optional[str] = None  # 风格
    seed: Optional[int] = None  # 随机种子
    raw_generation_params: Optional[Dict[str, Any]] = None  # 原始生成参数
    video_generation_tool: Optional[str] = None  # 视频工具 pollo_seedance / openai_sora 等
    
    # === 生成模式与音频 ===
    generation_mode: Optional[str] = None   # "normal" | "lipsync" | "empty_shot"，与 shot 的 generation_mode 统一
    audio_url: Optional[str] = None         # lipsync 模式下使用的音频 URL（用于审计追踪）
    
    # === AI生成信息 ===
    ai_messages: Optional[str] = None  # AI生成过程消息（JSON）

    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据

    # === Tool 维度（当次 video 调用，读路径 row_to_struct_safe 防御 DB 多列）===
    video_tool_metrics: Optional[Dict[str, Any]] = None  # 当次 video 工具 metrics
    tool_duration_sec: Optional[float] = None  # 当次调用耗时（秒）
    tool_cost: Optional[float] = None  # 当次调用成本（美元）
