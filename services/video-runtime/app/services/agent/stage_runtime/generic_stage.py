"""Shared helpers for stage deep-agent packages."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ValidationError

from app.services.agent.stage_runtime.deep_agent_factory import create_stage_deep_agent
from app.services.agent.stage_runtime.paths import (
    virtual_skills_stage,
)
from app.services.agent.stage_runtime.workspace import (
    artifact_exists,
    artifact_relpath,
    read_artifact_json,
    write_artifact_json,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def make_write_json_tool(
    *,
    thread_id: str,
    run_id: str,
    artifact_name: str,
    draft_model: Type[T],
    tool_name: str,
    extra_validate: Optional[Callable[[T], Optional[str]]] = None,
    stamp: Optional[Dict[str, Any]] = None,
):
    """Write tool with Pydantic ``args_schema`` (tool-args structured output)."""

    def _write(**kwargs: Any) -> str:
        try:
            draft = draft_model.model_validate(kwargs)
        except ValidationError as e:
            return f"VALIDATION_ERROR: {e}"
        if extra_validate:
            err = extra_validate(draft)
            if err:
                return f"VALIDATION_ERROR: {err}"
        data = draft.model_dump(mode="json")
        if stamp:
            data.update(stamp)
        try:
            write_artifact_json(thread_id, run_id, artifact_name, data)
        except OSError as e:
            # Surface as tool error so the agent can retry in-turn (don't crash ainvoke).
            return f"VALIDATION_ERROR: write failed ({e}); call again"
        return f"OK wrote {artifact_relpath(thread_id, run_id, artifact_name)}"

    return StructuredTool.from_function(
        func=_write,
        name=tool_name,
        description=(
            f"Validate and persist {artifact_name}. Arguments MUST match "
            f"{draft_model.__name__}. On VALIDATION_ERROR, fix and call again "
            f"in the same turn."
        ),
        args_schema=draft_model,
    )


def resolve_response_format(draft_model: Type[BaseModel]) -> Any:
    """Prefer ToolStrategy(handle_errors) so schema misses retry inside the agent."""
    try:
        from langchain.agents.structured_output import ToolStrategy

        return ToolStrategy(draft_model, handle_errors=True)
    except Exception:
        return draft_model


_resolve_response_format = resolve_response_format


def _payload_from_structured(sr: Any) -> Dict[str, Any]:
    if hasattr(sr, "model_dump"):
        return sr.model_dump(mode="json")
    if isinstance(sr, dict):
        return sr
    raise TypeError(f"unsupported structured_response type: {type(sr)}")


def ensure_artifact_from_result(
    *,
    result: Dict[str, Any],
    write_tool: Any,
    thread_id: str,
    run_id: str,
    artifact_name: str,
    stage: str,
) -> None:
    """If write_* did not persist, route structured_response through write tool (gates apply)."""
    if artifact_exists(thread_id, run_id, artifact_name):
        return
    sr = result.get("structured_response")
    if sr is None:
        raise RuntimeError(
            f"{stage} deep agent finished without {artifact_name} or structured_response"
        )
    payload = _payload_from_structured(sr)
    out = write_tool.invoke(payload)
    out_s = str(out)
    if not out_s.startswith("OK"):
        raise RuntimeError(
            f"{stage} structured_response failed write gate: {out_s[:500]}"
        )
    logger.info(
        "stage=%s persisted structured_response via write tool → %s",
        stage,
        artifact_relpath(thread_id, run_id, artifact_name),
    )


async def run_stage_deep_agent(
    *,
    stage: str,
    agent_name: str,
    prompt_name: Any,
    tools: list,
    human_text: str,
    detected_language: Optional[str] = None,
    image_urls: Optional[List[str]] = None,
    media_parts: Optional[List[Dict[str, Any]]] = None,
    system_extra: str = "",
    max_images: int = 24,
    response_format: Any = None,
    draft_model: Optional[Type[BaseModel]] = None,
    artifact_name: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    content_category: Optional[str] = None,
) -> List[Any]:
    """One ``ainvoke``. Structured output = write_* ``args_schema`` (not dual ``response_format``).

    ``create_deep_agent(..., response_format=…)`` accepts a Pydantic model or
    ``ToolStrategy(model)``. That path fills ``result["structured_response"]``.
    Stage agents already expose the same schema on ``write_*`` — binding both
    creates two structured exits and can recurse. So when ``tools`` is non-empty
    we leave ``response_format=None`` unless the caller passes it explicitly.
    """
    from prompts.prompt_config import PROMPTS_CONFIG
    from prompts.prompt_loader import create_llm_from_model_config

    entry = PROMPTS_CONFIG[prompt_name]
    mc = dict(entry.get("model_config") or {"model": "gpt-4.1-mini"})
    llm = create_llm_from_model_config(mc)

    # With write_* tools: schema lives on tool args. Only auto-bind ToolStrategy
    # when there is no write tool (rare). Never dual-bind by default.
    rf = response_format
    if rf is None and draft_model is not None and not tools:
        rf = resolve_response_format(draft_model)

    system_prompt = (
        f"You are a video production stage agent for {stage}. "
        f"Activate the {stage}-director skill (process + quality bar live there). "
        f"Read inputs with read_file limit=2000, then call the write_* tool "
        f"(arguments MUST match the tool schema). "
        f"If write_* returns VALIDATION_ERROR, fix and call again in the same turn. "
        f"Do not stop after only reading. "
        f"{system_extra}"
    ).strip()
    skill_paths = [virtual_skills_stage(stage)]
    agent = create_stage_deep_agent(
        model=llm,
        tools=tools,
        skills_virtual_paths=skill_paths,
        system_prompt=system_prompt,
        name=agent_name,
        response_format=rf,
    )
    lang_note = f" Write creative text in language: {detected_language}." if detected_language else ""
    text = (
        f"{human_text}{lang_note} "
        f"Use read_file with limit=2000 for input JSON and skill files, then call the write_* tool."
    )
    content: Any = text
    extra_media = [p for p in (media_parts or []) if isinstance(p, dict)]
    urls = [u for u in (image_urls or []) if u]
    if urls or extra_media:
        parts: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        for url in urls[: max(1, max_images)]:
            parts.append({"type": "image_url", "image_url": {"url": url}})
        parts.extend(extra_media)
        content = parts

    result = await agent.ainvoke({"messages": [HumanMessage(content=content)]})
    messages = list(result.get("messages") or [])
    _log_write_tool_retries(stage, messages)

    if thread_id and run_id and artifact_name and tools:
        ensure_artifact_from_result(
            result=result if isinstance(result, dict) else dict(result),
            write_tool=tools[0],
            thread_id=thread_id,
            run_id=run_id,
            artifact_name=artifact_name,
            stage=stage,
        )

    return messages


def log_write_tool_retries(stage: str, messages: List[Any]) -> None:
    _log_write_tool_retries(stage, messages)


def _log_write_tool_retries(stage: str, messages: List[Any]) -> None:
    errors = 0
    oks = 0
    last_err = ""
    for m in messages:
        content_s = str(getattr(m, "content", "") or "")
        name = getattr(m, "name", None) or ""
        is_tool = getattr(m, "type", "") == "tool" or "write_" in name
        if not is_tool:
            continue
        if content_s.startswith("VALIDATION_ERROR") or "VALIDATION_ERROR:" in content_s[:40]:
            errors += 1
            last_err = content_s[:240]
        elif content_s.startswith("OK"):
            oks += 1
    if errors or oks:
        logger.info(
            "stage=%s write_tool in_turn validation_errors=%d ok_writes=%d last_err=%s",
            stage,
            errors,
            oks,
            last_err or "-",
        )


def load_artifact(thread_id: str, run_id: str, name: str, model: Type[T]) -> T:
    raw = read_artifact_json(thread_id, run_id, name)
    return model.model_validate(raw)
