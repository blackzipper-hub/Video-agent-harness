"""
视频角色相关Schema - 使用msgspec.Struct

- VideoCharacterDB: 角色主表
- VideoCharacterGenerationVersionDB: 角色版本表
- VideoCharacterMultiViewImageDB / VersionDB: 角色多视角图主表与版本表
"""

import msgspec
from datetime import datetime
from typing import Optional, Dict, Any, List


class VideoCharacterDB(msgspec.Struct, kw_only=True):
    """视频角色数据库模型 - 对应 video_characters 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 角色唯一标识
    
    # === 元数据字段 ===
    user_id: str  # 用户ID
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 角色信息字段 ===
    type: str = "character"  # 视觉元素类型：character（人物角色）、object（重要物品）、location（核心场所）
    name: str  # 角色名称
    description: str  # 角色描述
    personality: str  # 性格特点
    appearance: str  # 外观特征
    role: str  # 角色作用
    style: Optional[str] = None  # 角色风格
    body_type: Optional[str] = None  # 体型大小
    image_url: Optional[str] = ""  # 角色图片URL（兼容老数据）
    current_version_index: int = 0  # 当前版本索引
    
    # === 选中状态字段 ===
    selected_version_id: Optional[str] = None  # 选中的角色版本UUID
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoCharacterGenerationVersionDB(msgspec.Struct, kw_only=True):
    """视频角色生成版本数据库模型 - 对应 video_character_generation_versions 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 角色版本唯一标识
    
    # === 元数据字段 ===
    video_character_id: str  # 关联的角色UUID (外键)
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 版本核心信息 ===
    version_number: int  # 版本号
    character_image_url: str  # 角色图片URL
    t2i_prompt: str  # 文本到图像的提示词
    provider: str  # 生成服务提供商
    reference_image_urls: Optional[List[str]] = None  # 参考图片URL列表
    success: bool = True  # 生成是否成功
    error_msg: Optional[str] = None  # 用户友好的错误消息
    raw_error_msg: Optional[str] = None  # 原始错误信息
    
    # === 生成参数字段（用于重新生成时保持一致）===
    aspect_ratio: Optional[str] = None  # 图片的宽高比
    resolution: Optional[str] = None  # 图片的分辨率
    model: Optional[str] = None  # 使用的模型类型
    seed: Optional[int] = None  # 随机种子
    image_generation_tool: Optional[str] = None  # 图像生成工具
    
    # === AI生成信息 ===
    ai_messages: Optional[str] = None  # AI生成过程的消息记录（JSON格式）
    
    # === 多视角图关联 ===
    multi_view_image_version_id: Optional[str] = None  # 关联的多视角图版本UUID
    selected_multi_view_version_id: Optional[str] = None  # 选中的多视角图版本UUID
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据

    # === Tool 维度（当次 image 调用，读路径 row_to_struct_safe 防御 DB 多列）===
    image_tool_metrics: Optional[Dict[str, Any]] = None  # 当次 image wrapper metrics
    tool_duration_sec: Optional[float] = None  # 当次调用耗时（秒）
    tool_cost: Optional[float] = None  # 当次调用成本（美元）


class VideoCharacterMultiViewImageDB(msgspec.Struct, kw_only=True):
    """角色多视角图主表 - 对应 video_character_multi_view_images 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 多视角图唯一标识
    
    # === 元数据字段 ===
    video_character_id: str  # 关联的角色UUID（对应SQLModel字段名）
    user_id: str  # 用户ID
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 版本管理 ===
    current_version_index: int = 0  # 当前版本索引
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoCharacterMultiViewImageVersionDB(msgspec.Struct, kw_only=True):
    """角色多视角图版本表 - 对应 video_character_multi_view_image_versions 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 版本唯一标识
    
    # === 元数据字段 ===
    multi_view_image_id: str  # 关联的多视角图UUID (外键)
    video_character_id: str  # 关联的角色UUID（冗余字段，便于查询）
    video_character_version_id: str  # 关联的角色版本UUID（绑定到特定角色版本）
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 版本核心信息 ===
    version_number: int  # 版本号
    multi_view_image_url: str  # 多视角图片URL
    multi_view_prompt: Optional[str] = None  # 多视角图的生成prompt（对应SQLModel字段名）
    provider: Optional[str] = None  # 生成服务提供商（对应SQLModel类型）
    success: bool = True  # 生成是否成功
    error_msg: Optional[str] = None  # 错误消息
    raw_error_msg: Optional[str] = None  # 原始错误信息
    
    # === 生成参数字段（用于重新生成时保持一致）===
    aspect_ratio: Optional[str] = None  # 图片的宽高比
    resolution: Optional[str] = None  # 图片的分辨率
    model: Optional[str] = None  # AI模型
    seed: Optional[int] = None  # 随机种子
    ai_messages: Optional[str] = None  # AI生成过程的消息记录
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoCharacterFusionImageDB(msgspec.Struct, kw_only=True):
    """角色融合图 - 对应 video_character_fusion_images 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 融合图唯一标识
    
    # === 元数据字段 ===
    user_id: str  # 用户ID
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 融合图核心信息字段 ===
    fusion_key: str  # 融合图唯一key
    image_type: str  # 图片类型：'main' 或 'multiview'
    character_ids: List[str]  # 参与融合的角色ID列表
    fusion_image_url: str  # 融合图URL
    
    # === 生成参数字段 ===
    aspect_ratio: Optional[str] = None  # 图片的宽高比
    resolution: Optional[str] = None  # 图片的分辨率
    model: Optional[str] = None  # 使用的模型类型
    seed: Optional[int] = None  # 随机种子
    provider: Optional[str] = None  # 生成服务提供商
    
    # === 生成信息字段 ===
    fusion_prompt: Optional[str] = None  # 融合图生成prompt
    success: bool = True  # 生成是否成功
    error_msg: Optional[str] = None  # 错误消息
    raw_error_msg: Optional[str] = None  # 原始错误信息
    ai_messages: Optional[str] = None  # AI生成过程的消息记录
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据
