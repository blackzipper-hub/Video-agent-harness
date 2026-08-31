"""
视频故事相关Schema - 使用msgspec.Struct

- VideoStoryOutlineDB: 故事大纲
- VideoSceneDB: 场景
- VideoStoryboardDetailDB: 详细分镜
- VideoDetailedShotDB: 详细镜头
- VideoChapterDB: 章节
"""

import msgspec
from datetime import datetime
from typing import Optional, Dict, Any, List


class VideoStoryOutlineDB(msgspec.Struct, kw_only=True):
    """视频故事大纲数据库模型 - 对应 video_story_outline 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 唯一标识
    
    # === 故事大纲核心字段 ===
    title: str  # 故事标题
    description: str  # 故事描述
    themes: List[str]  # 故事主题列表
    target_audience: str  # 目标受众
    narrative_structure: str  # 叙事结构
    total_duration: float  # 总时长（秒），读自 COALESCE(total_duration_sec, total_duration)
    
    # === 旧字段兼容 ===
    theme: Optional[str] = None  # 主题（旧字段）
    key_message: Optional[str] = None  # 核心信息（旧字段）
    style_guide: Optional[str] = None  # 视觉风格（旧字段）
    structure: Optional[str] = None  # 结构（旧字段）
    
    # === 分析引用 ===
    analysis_id: Optional[str] = None  # 视频分析UUID引用
    audio_transcription_uuid: Optional[str] = None  # 关联转录 UUID（无外键）
    
    # === 版本 ===
    current_version_index: Optional[int] = 0  # 当前版本索引（NULL 视为 0）

    # === 元数据 ===
    run_id: str  # 执行ID
    user_id: str  # 用户ID
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据（characters, scenes等）
    
    # === 时间戳 ===
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间


class VideoSceneDB(msgspec.Struct, kw_only=True):
    """视频场景数据库模型 - 对应 video_scenes 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 场景唯一标识
    
    # === 元数据字段 ===
    user_id: str  # 用户ID
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 场景信息字段 ===
    scene_number: int  # 场景编号
    title: str  # 场景标题
    description: str  # 场景描述
    duration: float  # 场景时长（秒），读自 COALESCE(duration_sec, duration)
    camera_angle: str  # 镜头角度
    character_action: str  # 角色动作
    visual_style: str  # 视觉风格
    transition_style: str  # 转场风格
    is_bridge: bool = False  # 是否是衔接片段
    character_ids: Optional[List[str]] = None  # 该场景使用的角色ID列表
    audio_segment_ids: Optional[List[str]] = None  # 对应的音频片段ID列表（音频驱动模式）
    chapter_id: Optional[str] = None  # 所属章节ID
    
    # === 生成模式 ===
    generation_mode: Optional[str] = None  # "normal" | "lipsync" | "empty_shot"，决定视频生成方式
    
    # === 版本 ===
    current_version_index: Optional[int] = 0  # 当前版本索引（NULL 视为 0）
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoStoryOutlineVersionDB(msgspec.Struct, kw_only=True):
    """故事大纲版本 - 对应 video_story_outline_versions 表"""
    id: int
    uuid: str
    story_outline_id: str
    version_number: int
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    theme: Optional[str] = None
    key_message: Optional[str] = None
    total_duration: Optional[float] = None
    style_guide: Optional[str] = None
    analysis_id: Optional[str] = None
    additional_data: Optional[Dict[str, Any]] = None
    created_at: datetime


class VideoChapterVersionDB(msgspec.Struct, kw_only=True):
    """章节版本 - 对应 video_chapter_versions 表"""
    id: int
    uuid: str
    chapter_id: str
    version_number: int
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    story_outline_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    duration: Optional[float] = None
    order: Optional[int] = None
    audio_segment_ids: Optional[List[str]] = None
    additional_data: Optional[Dict[str, Any]] = None
    created_at: datetime


class VideoSceneVersionDB(msgspec.Struct, kw_only=True):
    """场景版本 - 对应 video_scene_versions 表"""
    id: int
    uuid: str
    scene_id: str
    version_number: int
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    scene_number: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    duration: Optional[float] = None
    camera_angle: Optional[str] = None
    character_action: Optional[str] = None
    visual_style: Optional[str] = None
    transition_style: Optional[str] = None
    is_bridge: Optional[bool] = None
    character_ids: Optional[List[str]] = None
    audio_segment_ids: Optional[List[str]] = None
    chapter_id: Optional[str] = None
    generation_mode: Optional[str] = None
    additional_data: Optional[Dict[str, Any]] = None
    created_at: datetime


class VideoStoryboardDetailDB(msgspec.Struct, kw_only=True):
    """视频详细分镜数据库模型 - 对应 video_storyboard_details 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 详细分镜唯一标识
    
    # === 元数据字段 ===
    user_id: str  # 用户ID
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    story_outline_id: str  # 关联的故事大纲UUID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 详细分镜信息字段 ===
    total_duration: float  # 总时长（秒），读自 COALESCE(total_duration_sec, total_duration)
    visual_style: str  # 整体视觉风格
    shots_count: int  # 镜头总数
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoDetailedShotDB(msgspec.Struct, kw_only=True):
    """视频详细镜头数据库模型 - 对应 video_detailed_shots 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 详细镜头唯一标识
    
    # === 元数据字段 ===
    user_id: str  # 用户ID
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    storyboard_detail_id: str  # 关联的详细分镜UUID
    scene_id: str  # 关联的场景UUID
    chapter_id: Optional[str] = None  # 关联的章节UUID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 详细镜头信息字段 ===
    shot_number: int  # 镜头编号
    duration: float  # 时长（秒），读自 COALESCE(duration_sec, duration)
    
    # 1️⃣ 镜头构图（Shot Composition）
    shot_type: str  # 景别类型 - 9种专业景别
    camera_position: Optional[str] = None  # 相机机位
    camera_angle: Optional[str] = None  # 相机角度
    subject_angle: Optional[str] = None  # 主体角度
    subject_pose: Optional[str] = None  # 主体姿势
    
    # 2️⃣ 画面内容（Scene Content）
    scene_description: str  # 画面描述
    
    # 3️⃣ 技术执行（Technical Execution）
    camera_movement: str  # 镜头运动
    lighting: str  # 光影
    visual_effects: str  # 特效
    transition: str  # 转场
    
    # 4️⃣ 声音设计（Sound Design）
    dialogue: str  # 台词
    sound_effects: str  # 音效
    narration: Optional[str] = None  # 旁白
    is_bridge: bool = False  # 是否是衔接片段
    character_ids: Optional[List[str]] = None  # 该镜头使用的角色ID列表
    audio_segment_ids: Optional[List[str]] = None  # 对应的音频片段ID列表
    
    style_guide: Optional[str] = None  # 整体视觉风格指导

    # === 生成模式 ===
    generation_mode: Optional[str] = None  # "normal" | "lipsync" | "empty_shot"，决定视频生成方式

    # === Per-shot 路由（LLM + 工具候选；JSONB，与 additional_data 区分：主决策存此列）===
    generation_routing: Optional[Dict[str, Any]] = None

    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据


class VideoChapterDB(msgspec.Struct, kw_only=True):
    """视频章节数据库模型 - 对应 video_chapters 表"""
    
    # === 基础字段 ===
    id: int
    uuid: str  # 章节唯一标识
    
    # === 元数据字段 ===
    user_id: str  # 用户ID
    conversation_id: str  # 对话ID
    thread_id: str  # 线程ID
    run_id: str  # 执行ID
    story_outline_id: str  # 关联的故事大纲UUID
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
    
    # === 章节信息字段 ===
    title: str  # 章节标题
    description: str  # 章节描述
    duration: float  # 章节时长（秒）- 对应SQLModel
    order: int  # 章节顺序（0-based，第一章 order=0）
    
    # === 音频片段关联（仅用于 audio driven 模式）===
    audio_segment_ids: Optional[List[str]] = None  # 音频片段UUID列表
    audio_section_uuid: Optional[str] = None  # 关联 video_audio_section.uuid（无外键）
    
    # === 版本 ===
    current_version_index: Optional[int] = 0  # 当前版本索引（NULL 视为 0）
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = None  # 额外数据
