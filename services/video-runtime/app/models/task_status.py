"""
任务状态与计费状态枚举
"""
from enum import Enum


class RunType(str, Enum):
    """conversation_run.run_type：主流程与二级动作"""
    MAIN = "main"
    RESUME = "resume"
    REGENERATE_KEYFRAMES = "regenerate_keyframes"
    REGENERATE_VIDEOS = "regenerate_videos"
    REGENERATE_TIMELINE = "regenerate_timeline"
    REGENERATE_CHARACTERS = "regenerate_characters"
    COMPANION_CHAT = "companion_chat"


class TaskStatus(str, Enum):
    """任务状态枚举"""
    QUEUED = "queued"  # 队列中（新任务）
    RESUME_QUEUED = "resume_queued"  # resume 已入队，等待 worker 拉取（仅 API 写入，Worker 拉取后置为 RUNNING）
    RUNNING = "running"  # 运行中（仅 Worker _process_task_internal 写入）
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消
    INTERRUPTED = "interrupted"  # 已暂停（如 video 流程 gate 处等待用户继续）


class BillingStatus(str, Enum):
    """计费核实状态（LangSmith 成本异步上报，需延后核实）"""
    PENDING = "pending"      # 待核实、未扣费
    COMPLETED = "completed"  # 已核实并扣费（含无图片生成无需扣费的情况）
    FAILED = "failed"        # 核实失败（如 LangSmith 无数据）


class LangsmithStatus(str, Enum):
    """LangSmith 成本写入状态（独立于扣款流程，所有 agent 统一管理）"""
    PENDING = "pending"      # 待写入 langsmith_cost
    COMPLETED = "completed"  # 已写入 langsmith_cost
    FAILED = "failed"        # LangSmith 无数据或写入失败
