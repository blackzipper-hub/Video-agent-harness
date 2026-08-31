"""
VideoCompanionContextSchema — Companion Agent 的自定义上下文。

对标 SurfSense 的 SurfSenseContextSchema。
在 create_agent 的 context_schema 参数中传入，
Agent 运行时自动注入到 state 中。
"""
from typing import Optional
from typing_extensions import TypedDict


class VideoCompanionContextSchema(TypedDict, total=False):
    """Agent 运行时上下文（非序列化字段，由调用方注入）。

    默认 state 已包含：
    - messages: 对话历史
    - todos: TodoListMiddleware 管理的任务列表

    我们额外添加：
    - run_id: 当前视频生成任务的 run_id
    - user_id: 用户 ID
    - thread_id: 线程 ID
    """
    run_id: str
    user_id: str
    thread_id: str
