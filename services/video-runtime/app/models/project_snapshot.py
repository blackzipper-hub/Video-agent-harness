"""
ProjectSnapshot — Agent Companion 的项目感知快照。

给 Agent Loop（LLM）使用的轻量视图，只存枚举 + 数字 + ID 列表，
完整数据始终在 DB 里。

对标：
- Claude Code 的 toolResultStorage（大结果落盘只留引用）
- SurfSense 的 files state（虚拟文件系统）
"""
from enum import Enum
from typing import Dict, List, Optional
from typing_extensions import TypedDict


class PhaseStatus(str, Enum):
    """单个阶段的状态"""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class PipelinePhase(str, Enum):
    """DAG 流水线当前主阶段"""
    USER_INPUT = "user_input_analysis"
    MUSIC = "music_generation"
    ANALYSIS = "video_analysis"
    OUTLINE = "outline_generation"
    CHARACTER = "main_character_design"
    GATE_CHARACTER = "gate_after_character"
    SCENE = "scene_generation"
    BGM = "music_bgm_generation"
    VISUAL_MATCH = "visual_elements_matching"
    STORYBOARD = "storyboard_detail_generation"
    FIRST_FRAME_REVISION = "storyboard_first_frame_revision"
    ROUTING = "per_shot_generation_routing"
    FUSION = "character_fusion"
    KEYFRAME = "keyframe_generation"
    REFLECTION = "keyframe_reflection"
    GATE_KEYFRAME = "gate_after_keyframe_reflection"
    VIDEO = "video_generation"
    GATE_SHOTS = "gate_after_shots"
    NARRATION = "narration_generation"
    SEGMENTS = "video_segments"
    ASSEMBLY = "video_assembly"
    COMPLETED = "completed"


class StageStats(TypedDict, total=False):
    """单个阶段的统计——纯数据，不含任何渲染文本"""
    status: str              # PhaseStatus 值
    total: int               # 总数
    succeeded: int           # 成功数
    failed_items: List[int]  # 失败的 shot_number 列表
    versions_count: Dict[int, int]  # shot_number → 版本数（只记有多版本的）


class OutlinePreview(TypedDict, total=False):
    """大纲文案短预览——供 LLM 在不调 get_artifact_detail 时判断「涉及就要改」。"""
    title: str
    theme: str
    description: str
    key_message: str
    style_tags: str


class ProjectSnapshot(TypedDict, total=False):
    """Agent Companion 的项目感知快照。

    存在 Agent State (LangGraph checkpoint) 里，
    每次工具执行后通过 Command(update=...) 更新。

    LLM 不直接操作此结构，通过 render_snapshot_for_llm() 渲染后
    注入到 system prompt 动态段。
    """
    run_id: str
    phase: str                     # PipelinePhase 值
    task_status: str               # TaskStatus 值
    total_shots: int
    total_duration_sec: float

    # 各阶段统计
    outline: StageStats
    characters: StageStats
    scenes: StageStats
    keyframes: StageStats
    videos: StageStats
    narrations: StageStats
    music: StageStats
    segments: StageStats
    assembly: StageStats

    # 大纲文案短预览（标题/主题/描述等）
    outline_preview: OutlinePreview

    # 当前阻塞点（after_* 门控或 failed_* 失败暂停）
    pending_gate: Optional[str]
