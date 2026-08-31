"""
视频关键帧相关Schema - 使用msgspec.Struct

- VideoKeyframeDB: 关键帧主表（按镜头/场景）
- VideoKeyframeVersionDB: 关键帧版本表
- VideoKeyframeReflectionDB: 关键帧反思记录
"""

import msgspec
from datetime import datetime
from typing import Optional, Dict, Any, List


class VideoKeyframeDB(msgspec.Struct, kw_only=True):
    """视频关键帧数据库模型 - 对应 video_keyframes 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 关键帧唯一标识
    
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
    
    # === 关键帧核心信息字段 ===
    shot_number: int  # 镜头编号
    is_bridge: bool = False  # 是否是衔接片段
    frame_index: int = 0  # 关键帧位置索引：0=首帧（起始图像），-1=尾帧（结束图像），1=中间帧
    reference_image_urls: Optional[List[str]] = None  # 参考图片URL列表（兼容老数据）
    character_ids: Optional[List[str]] = None  # 关联的角色ID列表
    current_version_index: int = 0  # 当前版本索引
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoKeyframeVersionDB(msgspec.Struct, kw_only=True):
    """视频关键帧版本数据库模型 - 对应 video_keyframe_versions 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 关键帧版本唯一标识
    
    # === 元数据字段 ===
    keyframe_id: str  # 关联的关键帧UUID (外键)
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 版本核心信息 ===
    version_number: int  # 版本号
    shot_number: int  # 镜头编号
    keyframe_url: str  # 关键帧图片URL
    t2i_prompt: str  # 文本到图像的提示词
    provider: str  # 生成服务提供商
    is_bridge: bool = False  # 是否是衔接片段
    reference_image_urls: Optional[List[str]] = None  # 参考图片URL列表
    character_version_ids: Optional[List[str]] = None  # 关联的角色版本ID列表
    success: bool = True  # 生成是否成功
    error_msg: Optional[str] = None  # 用户友好的错误消息
    raw_error_msg: Optional[str] = None  # 原始错误信息
    audio_segment_ids: Optional[List[str]] = None  # 对应的音频片段ID列表
    
    # === 生成参数（regenerate 时用于重建 UserOption） ===
    aspect_ratio: Optional[str] = None  # 宽高比 16:9 / 9:16 / 1:1
    resolution: Optional[str] = None  # 分辨率 480p / 720p / 1080p
    seed: Optional[int] = None  # 随机种子
    model: Optional[str] = None  # 模型类型
    image_generation_tool: Optional[str] = None  # 图像工具
    
    # === AI / 扩展 ===
    ai_messages: Optional[str] = None  # AI生成过程消息（JSON）

    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据

    # === Tool 维度（当次 image 调用，读路径 row_to_struct_safe 防御 DB 多列）===
    image_tool_metrics: Optional[Dict[str, Any]] = None  # 当次 image wrapper metrics
    tool_duration_sec: Optional[float] = None  # 当次调用耗时（秒）
    tool_cost: Optional[float] = None  # 当次调用成本（美元）


class VideoKeyframeReflectionDB(msgspec.Struct, kw_only=True):
    """关键帧反思记录 - 对应 video_keyframe_reflections 表（与 models/video/video_keyframe_reflection.py 一致）"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 反思记录唯一标识
    
    # === 元数据字段 ===
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    story_outline_id: str  # 关联的故事大纲UUID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 反思迭代信息 ===
    iteration_number: int  # 迭代次数
    total_keyframes: int  # 总关键帧数
    analyzed_keyframes: int  # 分析的关键帧数
    regenerated_keyframes: int  # 重新生成的关键帧数
    
    # === 统计信息 ===
    consistency_score: float = 0.0  # 一致性评分 (0-1)
    analysis_duration_seconds: float = 0.0  # 分析耗时（秒）
    regeneration_duration_seconds: float = 0.0  # 重新生成耗时（秒）
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoKeyframeReflectionResultDB(msgspec.Struct, kw_only=True):
    """关键帧反思结果 - 对应 video_keyframe_reflection_results 表（与 models/video/video_keyframe_reflection.py 一致）"""

    # === 基础字段 ===
    id: int
    uuid: str  # 反思结果唯一标识

    # === 元数据字段 ===
    reflection_id: str  # 关联的反思记录UUID
    keyframe_id: str  # 关联的关键帧UUID
    keyframe_version_id: str  # 被分析的关键帧版本UUID
    conversation_id: str
    thread_id: str
    run_id: str
    user_id: str
    created_at: datetime  # 无 updated_at

    # === 镜头信息 ===
    shot_number: int
    shot_uuid: str

    # === 分析结果 ===
    needs_regeneration: bool = False
    issues: Optional[List[Dict[str, Any]]] = None  # 发现的问题列表
    analysis_summary: Optional[str] = None
    improvement_points: Optional[List[str]] = None

    # === 优化信息 ===
    original_description: Optional[str] = None
    improved_description: Optional[str] = None

    # === 重新生成信息 ===
    new_keyframe_version_id: Optional[str] = None
    regeneration_success: Optional[bool] = None
    regeneration_error: Optional[str] = None

    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None
