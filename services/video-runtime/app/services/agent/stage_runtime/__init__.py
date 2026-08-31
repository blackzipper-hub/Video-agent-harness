"""Stage runtime: kit/ FS + deep agent factory for LangGraph nodes."""

from .deep_agent_factory import create_stage_deep_agent
from .paths import (
    AGENT_SERVICE_ROOT,
    KIT_ROOT,
    SKILLS_ROOT,
    VIRTUAL_SKILLS_OUTLINE,
    run_workspace_dir,
    virtual_skills_stage,
)
from .provider_web_search import resolve_provider_web_search
from .workspace import ensure_run_workspace, write_artifact_json, write_input_json

__all__ = [
    "AGENT_SERVICE_ROOT",
    "KIT_ROOT",
    "SKILLS_ROOT",
    "VIRTUAL_SKILLS_OUTLINE",
    "create_stage_deep_agent",
    "ensure_run_workspace",
    "resolve_provider_web_search",
    "run_workspace_dir",
    "virtual_skills_stage",
    "write_artifact_json",
    "write_input_json",
]
