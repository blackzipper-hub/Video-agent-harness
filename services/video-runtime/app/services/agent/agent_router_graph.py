"""
LangGraph Agent Router Graph Export for LangSmith Deployment
用于 langgraph dev 和 LangSmith deployment 的 graph 导出

此模块导出 agent_router 和 video_agent，让 LangGraph CLI 识别它们
"""
import logging
import sys
from pathlib import Path

# 添加项目根目录到 sys.path，确保可以进行绝对导入
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.services.agent.agent_router_service import AgentRouterService
from app.services.agent.video_agent_service import get_video_agent

logger = logging.getLogger(__name__)

# 导出 agent_router（主 graph）
_service = AgentRouterService()
graph = _service.build_graph_for_langsmith()

# 导出 video_agent（subagent）
# 注意：需要在 langgraph.json 中声明才能被识别为 subagent
video_agent = get_video_agent()

