"""
错误追踪相关的数据库模型
用于记录用户编辑和任务执行情况
"""

from sqlmodel import SQLModel, Field, Column, Text, JSON
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
import uuid


class UserActionType(str, Enum):
    """用户操作类型枚举"""
    RE_GEN = "re-gen"  # 重新生成（不改prompt）
    EDIT_PROMPT = "edit-prompt"  # 修改prompt后重新生成
    EDIT_VIDEO = "edit-video"  # 基于视频编辑（未来功能）
    CASCADED_FROM_KEYFRAME = "cascaded-from-keyframe"  # 由keyframe重新生成触发的级联视频生成


class VideoTaskRecordDB(SQLModel, table=True):
    """视频任务记录表 - 记录完整的任务执行信息
    
    核心设计理念：完整镜像 VideoAgentState 的所有字段
    """
    __tablename__ = "video_task_records"
    
    # === 基础字段 ===
    id: Optional[int] = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True, description="任务记录唯一标识")
    
    # === 任务标识（对应 VideoAgentState 基础信息） ===
    task_id: str = Field(index=True, description="任务ID（使用 run_id）")
    conversation_id: str = Field(index=True, description="对话ID")
    thread_id: str = Field(index=True, description="线程ID")
    user_id: str = Field(index=True, description="用户ID")
    
    # === 语言和配置 ===
    detected_language: Optional[str] = Field(default=None, description="检测到的用户语言（ISO 639-1）")
    generation_config: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON), default=None, description="生成配置（generate_narration, generate_music, generate_audio_effect）")
    actual_target_duration: Optional[float] = Field(default=None, description="实际目标时长（秒）")
    
    # === 任务输入（对应 VideoAgentState.user_input_data） ===
    task_input: str = Field(sa_column=Column(Text), description="用户输入 Prompt")
    user_input_data: Optional[Dict[str, Any]] = Field(sa_column=Column(JSON), default=None, description="完整的用户输入数据（包含 images, audio_files, video_files, user_option）")
    
    # === 任务状态 ===
    task_start_time: datetime = Field(description="任务开始时间")
    task_finish_time: Optional[datetime] = Field(default=None, description="任务完成时间（video_assembly 完成时间）")
    task_status: str = Field(description="任务状态: processing, completed, failed, cancelled")
    
    # === 关联的 UUID 引用（完整镜像 VideoAgentState） ===
    analysis_uuid: Optional[str] = Field(default=None, index=True, description="VideoAnalysisResult UUID")
    audio_transcription_uuids: Optional[List[str]] = Field(sa_column=Column(JSON), default=None, description="AudioTranscription UUIDs")
    story_outline_uuid: Optional[str] = Field(default=None, index=True, description="StoryOutline UUID")
    character_uuids: Optional[List[str]] = Field(sa_column=Column(JSON), default=None, description="Character UUIDs")
    scene_uuids: Optional[List[str]] = Field(sa_column=Column(JSON), default=None, description="Scene UUIDs")
    shot_uuids: Optional[List[str]] = Field(sa_column=Column(JSON), default=None, description="DetailedShot UUIDs")
    keyframe_uuids: Optional[List[str]] = Field(sa_column=Column(JSON), default=None, description="Keyframe UUIDs")
    narration_uuids: Optional[List[str]] = Field(sa_column=Column(JSON), default=None, description="Narration UUIDs")
    audio_effect_uuids: Optional[List[str]] = Field(sa_column=Column(JSON), default=None, description="AudioEffect UUIDs")
    video_generation_uuids: Optional[List[str]] = Field(sa_column=Column(JSON), default=None, description="VideoGeneration UUIDs")
    music_generation_uuids: Optional[List[str]] = Field(sa_column=Column(JSON), default=None, description="MusicGeneration UUIDs")
    video_segments_uuids: Optional[List[str]] = Field(sa_column=Column(JSON), default=None, description="VideoSegments UUIDs")
    video_assembly_uuid: Optional[str] = Field(default=None, index=True, description="VideoAssembly UUID")
    final_video_url: Optional[str] = Field(sa_column=Column(Text), default=None, description="最终生成的视频 URL")

    # === 计费（与 conversation_runs 对齐 6 字段，仅 video 写此表）===
    billing_status: Optional[str] = Field(default=None, max_length=32, description="计费状态: pending, completed, failed")
    langsmith_cost: Optional[float] = Field(default=None, description="LangSmith 成本（美元）")
    cost: Optional[float] = Field(default=None, description="我方统计成本（美元）")
    cost_calculated: Optional[bool] = Field(default=None, description="是否已计算成本")
    credits_deducted: Optional[bool] = Field(default=None, description="是否已扣积分")
    credits_amount: Optional[int] = Field(default=None, description="扣减积分数")

    # === 时间戳 ===
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="更新时间")


class VideoShotEditRecordDB(SQLModel, table=True):
    """视频 Shot 编辑记录表"""
    __tablename__ = "video_shot_edit_records"
    
    # === 基础字段 ===
    id: Optional[int] = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True, description="编辑记录唯一标识")
    
    # === 关联字段 ===
    task_record_id: Optional[str] = Field(default=None, index=True, description="关联的任务记录 UUID")
    video_generation_id: str = Field(index=True, description="关联的视频生成 UUID")
    cascaded_from_storyboard_edit_id: Optional[str] = Field(default=None, index=True, description="如果是级联生成，关联的storyboard编辑记录UUID")
    
    # === 用户标识 ===
    user_id: str = Field(index=True, description="用户ID")
    conversation_id: str = Field(index=True, description="对话ID")
    thread_id: str = Field(index=True, description="线程ID")
    run_id: str = Field(index=True, description="执行ID")
    
    # === Shot 基础信息 ===
    shot_number: int = Field(description="镜头编号")
    
    # === 版本信息 ===
    old_version_id: str = Field(index=True, description="编辑前的版本 UUID")
    new_version_id: str = Field(index=True, description="编辑后的版本 UUID")
    old_version_number: int = Field(description="编辑前的版本号")
    new_version_number: int = Field(description="编辑后的版本号")
    
    # === 视频信息 ===
    old_video_url: str = Field(sa_column=Column(Text), description="编辑前的视频 URL")
    new_video_url: str = Field(sa_column=Column(Text), description="编辑后的视频 URL")
    duration: Optional[float] = Field(default=None, description="视频时长（秒）")
    
    # === 生成信息 ===
    model: str = Field(description="使用的模型型号")
    old_prompt: str = Field(sa_column=Column(Text), description="编辑前的 prompt")
    new_prompt: Optional[str] = Field(sa_column=Column(Text), default=None, description="编辑后的 prompt")
    
    # === 用户操作信息 ===
    user_action: str = Field(description="用户操作类型: re-gen, edit-prompt, edit-video")
    user_feedback: Optional[str] = Field(sa_column=Column(Text), default=None, description="用户反馈的错误描述")
    edit_instruction: Optional[str] = Field(sa_column=Column(Text), default=None, description="用户输入的修改指令")
    
    # === 生成结果 ===
    success: bool = Field(description="生成是否成功")
    error_msg: Optional[str] = Field(sa_column=Column(Text), default=None, description="错误信息")
    
    # === 时间戳 ===
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")


class VideoStoryboardEditRecordDB(SQLModel, table=True):
    """Storyboard 图片编辑记录表"""
    __tablename__ = "video_storyboard_edit_records"
    
    # === 基础字段 ===
    id: Optional[int] = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True, description="编辑记录唯一标识")
    
    # === 关联字段 ===
    task_record_id: Optional[str] = Field(default=None, index=True, description="关联的任务记录 UUID")
    keyframe_id: str = Field(index=True, description="关联的关键帧 UUID")
    
    # === 用户标识 ===
    user_id: str = Field(index=True, description="用户ID")
    conversation_id: str = Field(index=True, description="对话ID")
    thread_id: str = Field(index=True, description="线程ID")
    run_id: str = Field(index=True, description="执行ID")
    
    # === Storyboard 基础信息 ===
    shot_number: int = Field(description="镜头编号")
    
    # === 版本信息 ===
    old_version_id: str = Field(index=True, description="编辑前的版本 UUID")
    new_version_id: str = Field(index=True, description="编辑后的版本 UUID")
    old_version_number: int = Field(description="编辑前的版本号")
    new_version_number: int = Field(description="编辑后的版本号")
    
    # === 图片信息 ===
    old_image_url: str = Field(sa_column=Column(Text), description="编辑前的图片 URL")
    new_image_url: str = Field(sa_column=Column(Text), description="编辑后的图片 URL")
    
    # === 生成信息 ===
    model: str = Field(description="使用的模型型号")
    old_prompt: str = Field(sa_column=Column(Text), description="编辑前的 prompt")
    new_prompt: Optional[str] = Field(sa_column=Column(Text), default=None, description="编辑后的 prompt")
    
    # === 用户操作信息 ===
    user_action: str = Field(description="用户操作类型: re-gen, edit-prompt")
    user_feedback: Optional[str] = Field(sa_column=Column(Text), default=None, description="用户反馈的错误描述")
    edit_instruction: Optional[str] = Field(sa_column=Column(Text), default=None, description="用户输入的修改指令")
    
    # === 生成结果 ===
    success: bool = Field(description="生成是否成功")
    error_msg: Optional[str] = Field(sa_column=Column(Text), default=None, description="错误信息")
    
    # === 时间戳 ===
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")


class VideoCharacterEditRecordDB(SQLModel, table=True):
    """角色图片编辑记录表"""
    __tablename__ = "video_character_edit_records"
    
    # === 基础字段 ===
    id: Optional[int] = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True, description="编辑记录唯一标识")
    
    # === 关联字段 ===
    task_record_id: Optional[str] = Field(default=None, index=True, description="关联的任务记录 UUID")
    character_id: str = Field(index=True, description="关联的角色 UUID")
    
    # === 用户标识 ===
    user_id: str = Field(index=True, description="用户ID")
    conversation_id: str = Field(index=True, description="对话ID")
    thread_id: str = Field(index=True, description="线程ID")
    run_id: str = Field(index=True, description="执行ID")
    
    # === Character 基础信息 ===
    character_name: str = Field(description="角色名称")
    
    # === 版本信息 ===
    old_version_id: str = Field(index=True, description="编辑前的版本 UUID")
    new_version_id: str = Field(index=True, description="编辑后的版本 UUID")
    old_version_number: int = Field(description="编辑前的版本号")
    new_version_number: int = Field(description="编辑后的版本号")
    
    # === 图片信息 ===
    old_image_url: str = Field(sa_column=Column(Text), description="编辑前的图片 URL")
    new_image_url: str = Field(sa_column=Column(Text), description="编辑后的图片 URL")
    
    # === 生成信息 ===
    model: str = Field(description="使用的模型型号")
    old_prompt: str = Field(sa_column=Column(Text), description="编辑前的 prompt")
    new_prompt: Optional[str] = Field(sa_column=Column(Text), default=None, description="编辑后的 prompt")
    
    # === 用户操作信息 ===
    user_action: str = Field(description="用户操作类型: re-gen, edit-prompt")
    user_feedback: Optional[str] = Field(sa_column=Column(Text), default=None, description="用户反馈的错误描述")
    edit_instruction: Optional[str] = Field(sa_column=Column(Text), default=None, description="用户输入的修改指令")
    
    # === 生成结果 ===
    success: bool = Field(description="生成是否成功")
    error_msg: Optional[str] = Field(sa_column=Column(Text), default=None, description="错误信息")
    
    # === 时间戳 ===
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")

