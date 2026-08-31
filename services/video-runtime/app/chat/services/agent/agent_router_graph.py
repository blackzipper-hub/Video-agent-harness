"""
LangGraph Agent Router Graph 导出（仅 agent_router，不导出 video_agent）
迁移自 Cuti-VideoAgent；video_agent 由 VideoAgent 侧通过 WorkflowClient 注入后由 Router 调用。
"""
import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.chat.services.agent.agent_router_service import AgentRouterService

logger = logging.getLogger(__name__)

_service = AgentRouterService()
graph = _service.build_graph_for_langsmith()
