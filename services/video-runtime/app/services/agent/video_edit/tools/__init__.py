"""
Video Companion Agent 工具集合。

所有工具对外暴露统一入口 `get_companion_tools()`。
"""
from typing import List

from langchain_core.tools import BaseTool

from .get_project_status import GetProjectStatusTool
from .get_artifact_detail import GetArtifactDetailTool
from .regenerate_keyframes import RegenerateKeyframesTool
from .regenerate_videos import RegenerateVideosTool
from .regenerate_characters import RegenerateCharactersTool
from .reassemble_video import ReassembleVideoTool
from .continue_pipeline import ContinuePipelineTool
from .select_version import SelectVersionTool
from .update_music_prompt import UpdateMusicPromptTool
from .modify_outline import ModifyOutlineTool
from .update_scene import UpdateSceneTool
from .analyze_image import AnalyzeImageTool
from .analyze_video import AnalyzeVideoTool


def get_companion_tools(run_id: str, user_id: str, thread_id: str) -> List[BaseTool]:
    """构建 Companion Agent 的全部工具列表。

    每个工具内部通过闭包持有 run_id / user_id / thread_id，
    LLM 调用时只需传递与该工具直接相关的参数。
    """
    return [
        GetProjectStatusTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
        GetArtifactDetailTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
        RegenerateKeyframesTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
        RegenerateVideosTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
        RegenerateCharactersTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
        ReassembleVideoTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
        ContinuePipelineTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
        SelectVersionTool(run_id=run_id, user_id=user_id),
        UpdateMusicPromptTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
        ModifyOutlineTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
        UpdateSceneTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
        AnalyzeImageTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
        AnalyzeVideoTool(run_id=run_id, user_id=user_id, thread_id=thread_id),
    ]
