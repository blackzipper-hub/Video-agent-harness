"""GetProjectStatusTool — 获取视频项目当前状态概览。"""
import logging
from typing import Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class GetProjectStatusInput(BaseModel):
    """get_project_status 无需额外输入（run_id 已绑定到工具实例）。"""
    pass


class GetProjectStatusTool(BaseTool):
    name: str = "get_project_status"
    description: str = (
        "获取视频项目当前状态概览，含各阶段完成情况、pending_gate 门控暂停点、失败镜头。"
        "推进主管线前必须先调用；有 pending_gate 且用户要继续时，应对齐 gate 调 continue_pipeline。"
    )
    args_schema: Type[BaseModel] = GetProjectStatusInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(self, **kwargs) -> str:
        from ..snapshot_builder import build_snapshot_from_db
        from ..snapshot_renderer import render_snapshot_for_llm

        snapshot = await build_snapshot_from_db(self.run_id, self.user_id, self.thread_id)
        result = render_snapshot_for_llm(snapshot)
        logger.info(
            f"get_project_status: thread_id={self.thread_id} anchor_run_id={self.run_id} "
            f"phase={snapshot.get('phase')} pending_gate={snapshot.get('pending_gate')} "
            f"task_status={snapshot.get('task_status')}"
        )
        return result

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")
