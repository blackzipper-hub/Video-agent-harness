"""
Video Edit MCP Server — 暴露一个 video_edit_agent tool 给外部 agent（如 VideoChatAgent）。

内部使用 VA 侧的 VideoCompanionAgent 来编排 10 个子 tool，
ChatAgent 只需调用这一个 tool 即可。
"""
import logging
import os
import sys

from fastmcp import FastMCP

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

os.environ.setdefault("ENVIRONMENT", "local")

logger = logging.getLogger(__name__)

mcp = FastMCP("video-edit-tools")

_db_initialized = False


async def _ensure_db():
    """懒初始化 asyncpg pool（仅首次调用时执行）。"""
    global _db_initialized
    if not _db_initialized:
        from app.models.database import init_asyncpg_pool
        await init_asyncpg_pool()
        _db_initialized = True


async def _get_checkpointer():
    """复用 VA 主管线的 AsyncPostgresSaver，持久化 edit agent 多轮对话。"""
    from app.services.agent.agent_router_service import apostgres_checkpointer
    return await apostgres_checkpointer()


@mcp.tool
async def video_edit_agent(
    thread_id: str,
    run_id: str,
    user_id: str,
    user_message: str,
) -> str:
    """视频编辑助手：查看项目进度、修改角色/关键帧/视频/大纲/音乐、继续生成流程、重新合成等。
    将用户消息发给 VA 内部的 Companion Agent，由它决定调用哪些子工具并返回结果。"""
    await _ensure_db()

    from langchain_core.messages import HumanMessage
    from prompts.prompt_config import PromptName, PROMPTS_CONFIG, create_llm

    from app.services.agent.video_edit.agent import create_video_companion_agent
    from app.services.agent.video_edit.snapshot_builder import build_snapshot_from_db

    snap = await build_snapshot_from_db(run_id=run_id, user_id=user_id, thread_id=thread_id)
    logger.info(
        f"video_edit_agent MCP invoke: thread_id={thread_id} anchor_run_id={run_id} "
        f"user_message={user_message!r} phase={snap.get('phase')} "
        f"task_status={snap.get('task_status')} shots={snap.get('total_shots')}"
    )

    _mc = PROMPTS_CONFIG[PromptName.VIDEO_EDIT_AGENT].get("model_config", {})
    llm = create_llm(_mc)
    checkpointer = await _get_checkpointer()
    agent = create_video_companion_agent(
        llm=llm,
        run_id=run_id,
        user_id=user_id,
        thread_id=thread_id,
        snapshot=snap,
        checkpointer=checkpointer,
        enable_summarization=True,
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=user_message)]},
        config={"configurable": {"thread_id": f"mcp-edit-{thread_id}"}},
    )

    messages = result.get("messages", [])
    if messages:
        last = messages[-1]
        if hasattr(last, "content") and last.content:
            # gpt-5.6 Responses：content 常为 list[dict]（reasoning/text blocks）
            from app.chat.services.agent.agent_router_service import _stream_chunk_to_text
            reply = _stream_chunk_to_text(last.content).strip()
            if not reply:
                reply = "(edit agent 未返回可读文本)"
            logger.info(
                f"video_edit_agent MCP reply: thread_id={thread_id} "
                f"anchor_run_id={run_id} reply_preview={reply[:200]!r}"
            )
            return reply
    return "(edit agent 未返回内容)"
