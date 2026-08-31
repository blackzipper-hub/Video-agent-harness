"""Shared create_deep_agent factory for LangGraph stage nodes (Companion-like harness)."""
from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models import BaseChatModel

from .paths import AGENT_SERVICE_ROOT
from .provider_web_search import merge_tools_with_web_search, model_identifier

logger = logging.getLogger(__name__)

_EXCLUDED_TOOLS = frozenset({"execute", "write_file", "edit_file"})
_HARNESS_PROFILE = HarnessProfile(
    excluded_tools=_EXCLUDED_TOOLS,
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
)
_PROFILES_REGISTERED = False


def _ensure_harness_profile() -> None:
    global _PROFILES_REGISTERED
    if _PROFILES_REGISTERED:
        return
    for provider in ("openai", "anthropic", "google_genai"):
        register_harness_profile(provider, _HARNESS_PROFILE)
    _PROFILES_REGISTERED = True


def create_stage_deep_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[Any],
    skills_virtual_paths: List[str],
    system_prompt: str,
    name: str,
    response_format: Any = None,
    checkpointer: Optional[Any] = None,
    enable_web_search: bool = False,
):
    """
    Backend root = services/agent so virtual paths work:
      /kit/skills/...  /kit/schemas/...  /data/run_workspaces/{thread}/{run}/...

    ``response_format`` is the create_agent structured-output knob. Passing a
    Pydantic model (or ``ToolStrategy(model)``) makes the agent emit
    ``structured_response``. Stage nodes usually pass ``None`` and rely on
    ``write_*`` tool ``args_schema`` instead — do not dual-bind both.

    ``enable_web_search``: attach the official provider search tool for the
    model (OpenAI ``web_search`` / Gemini 3 ``google_search`` + server-side
    flag). See ``provider_web_search.py``.
    """
    _ensure_harness_profile()
    model, tools_list = merge_tools_with_web_search(
        model, tools, enable_web_search=enable_web_search
    )
    backend = FilesystemBackend(root_dir=str(AGENT_SERVICE_ROOT), virtual_mode=True)
    kwargs: dict = dict(
        model=model,
        tools=tools_list,
        system_prompt=system_prompt,
        skills=skills_virtual_paths,
        backend=backend,
        checkpointer=checkpointer,
        name=name,
    )
    if response_format is not None:
        kwargs["response_format"] = response_format
    agent = create_deep_agent(**kwargs)
    agent = agent.with_config({"recursion_limit": 80})
    logger.info(
        "stage deep agent created name=%s model=%s skills=%s tools=%d "
        "web_search=%s response_format=%s root=%s",
        name,
        model_identifier(model),
        skills_virtual_paths,
        len(tools_list),
        enable_web_search,
        getattr(response_format, "__name__", type(response_format).__name__)
        if response_format is not None
        else None,
        AGENT_SERVICE_ROOT,
    )
    return agent
