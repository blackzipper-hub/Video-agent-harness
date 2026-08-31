"""
VideoCompanionAgent — 构建 Companion Agent 图（Deep Agent / Skill 版）。

改造说明：
  从 langchain.agents.create_agent 迁移到 deepagents.create_deep_agent。
  - 13 个领域工具（get_companion_tools）**完全不变**。
  - 330 行工作流从 system_prompt 下沉到 video_edit/skills/ 下的 6 个 SKILL.md，
    由内置 SkillsMiddleware 按任务加载（Level 1 名称+描述常驻，Level 2 正文按需 read_file）。
  - create_deep_agent 默认会额外注入 execute(shell) / task(子 agent) / 文件写工具；
    本 agent 通过 HarnessProfile 关掉这些多余/危险工具，只保留 13 领域工具 + 只读文件工具（供读 SKILL.md）。
  - 摘要能力改用 deepagents 内置的 SummarizationMiddleware（不要再手动传，否则重复实例报错）。
"""
import logging
from pathlib import Path
from typing import Optional

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models import BaseChatModel
from langgraph.types import Checkpointer

from .context_schema import VideoCompanionContextSchema
from .middleware import SanitizeToolCallsMiddleware
from .system_prompt import build_system_prompt
from .tools import get_companion_tools
from ....models.project_snapshot import ProjectSnapshot

logger = logging.getLogger(__name__)

_VIDEO_EDIT_DIR = Path(__file__).resolve().parent
_SKILLS_DIR = str(_VIDEO_EDIT_DIR / "skills")

# deepagents 默认给 deep agent 挂满一套「代码 agent」工具（execute/task/文件写）。
# 对一个只需编排 13 个领域工具、且通过 MCP 暴露给用户的编辑 agent 来说，
# execute(shell) 与 task(通用子 agent) 是多余攻击面 / 干扰项，disk 写工具也不需要。
# 通过 HarnessProfile 在「provider 级」裁掉它们（只保留 read_file/ls/glob/grep 供读 SKILL.md）。
_EXCLUDED_TOOLS = frozenset({"execute", "write_file", "edit_file"})
_HARNESS_PROFILE = HarnessProfile(
    excluded_tools=_EXCLUDED_TOOLS,
    # 关掉自动挂载的 general-purpose 子 agent；不传 subagents，task 工具随之消失。
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
)

# 编辑 agent 模型为 gpt-4.1-mini（openai）；此处按 provider 注册，覆盖该 provider 下的 deep agent。
# 注册是幂等的（同 key 会 merge），import 时执行一次即可。
_PROFILES_REGISTERED = False


def _ensure_harness_profile() -> None:
    global _PROFILES_REGISTERED
    if _PROFILES_REGISTERED:
        return
    for _provider in ("openai", "anthropic"):
        register_harness_profile(_provider, _HARNESS_PROFILE)
    _PROFILES_REGISTERED = True


def create_video_companion_agent(
    llm: BaseChatModel,
    run_id: str,
    user_id: str,
    thread_id: str,
    snapshot: Optional[ProjectSnapshot] = None,
    checkpointer: Optional[Checkpointer] = None,
    enable_summarization: bool = True,
):
    """构建 Video Companion Agent（可编译的 LangGraph 图，Deep Agent 版）。

    Args:
        llm: 对话模型（当前为 gpt-4.1-mini；Claude 系列对 skill 正文的自动激活更可靠）。
        run_id: 视频生成任务的 run_id。
        user_id: 用户 ID。
        thread_id: 线程 ID。
        snapshot: 当前项目快照（首次调用可为 None，后续从 DB 构建）。
        checkpointer: LangGraph checkpoint 后端（Postgres / SQLite / Memory）。
        enable_summarization: 历史摘要。create_deep_agent 已内置 SummarizationMiddleware，
            默认开启；此参数保留用于兼容，False 时暂不改变内置摘要行为（如需彻底关闭，
            应在 HarnessProfile 中 excluded_middleware={"SummarizationMiddleware"}）。

    Returns:
        可 ainvoke / astream 的 LangGraph CompiledGraph。
    """
    _ensure_harness_profile()

    system_prompt = build_system_prompt(snapshot)
    tools = get_companion_tools(run_id=run_id, user_id=user_id, thread_id=thread_id)

    # 供 SkillsMiddleware 从磁盘按目录加载 SKILL.md，并提供只读文件工具（read_file 等）。
    backend = FilesystemBackend(root_dir=str(_VIDEO_EDIT_DIR))

    if not enable_summarization:
        logger.warning(
            "enable_summarization=False 但 create_deep_agent 内置摘要默认开启；"
            "如需彻底关闭请在 HarnessProfile 中排除 SummarizationMiddleware。"
        )

    agent = create_deep_agent(
        model=llm,
        tools=tools,                                  # 13 个领域工具，完全不变
        system_prompt=system_prompt,                  # 精简核心（工作流已下沉到 skills/）
        skills=[_SKILLS_DIR],                          # 6 个 SKILL.md 按任务加载
        middleware=[SanitizeToolCallsMiddleware()],   # 保留原有的孤儿 tool_call 清洗
        context_schema=VideoCompanionContextSchema,
        backend=backend,
        checkpointer=checkpointer,
        name="video_companion",
    )

    agent = agent.with_config({"recursion_limit": 200})
    logger.info(
        f"VideoCompanionAgent(deep) created: run_id={run_id}, "
        f"tools={len(tools)}, skills_dir={_SKILLS_DIR}, "
        f"excluded_tools={sorted(_EXCLUDED_TOOLS)}"
    )
    return agent
