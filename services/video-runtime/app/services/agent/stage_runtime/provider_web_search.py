"""Provider-native web search for deepagents stages (official deepagents quickstart).

OpenAI:  tools=[{"type": "web_search"}]
Gemini 3+: tools=[{"google_search": {}}] + tool_config.include_server_side_tool_invocations
           (Google: built-in + function calling combo is Gemini 3 only)
Gemini 2.5: cannot mix built-in google_search with FC in one request; use
            ``gemini_grounded_search`` (separate SDK call) as a normal function tool,
            or prefer Gemini 3 / OpenAI as the deep-agent host.

Refs:
  https://docs.langchain.com/oss/python/deepagents/quickstart
  https://ai.google.dev/gemini-api/docs/generate-content/tool-combination
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool

logger = logging.getLogger(__name__)

ProviderSearchTool = Union[Dict[str, Any], BaseTool]


def model_identifier(model: BaseChatModel) -> str:
    for attr in ("model", "model_name"):
        val = getattr(model, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return type(model).__name__


def _mid(model: BaseChatModel) -> str:
    return model_identifier(model).lower()


def is_openai_model(model: BaseChatModel) -> bool:
    mid = _mid(model)
    return mid.startswith("gpt-") or mid.startswith("o1") or mid.startswith("o3") or mid.startswith("o4")


def is_gemini_model(model: BaseChatModel) -> bool:
    return "gemini" in _mid(model)


def is_gemini3_model(model: BaseChatModel) -> bool:
    """Gemini 3.x can combine built-in tools + function calling (with flag)."""
    mid = _mid(model)
    return bool(re.search(r"gemini-3", mid))


_MARKER = "_cuti_server_side_tool_invocations"


def enable_gemini_server_side_tool_invocations(model: BaseChatModel) -> BaseChatModel:
    """Patch Gemini so every generate passes include_server_side_tool_invocations=True.

    Required by Gemini 3 when ``google_search`` is combined with deepagents FC tools
    (read_file / write_*). ``.bind(tool_config=…)`` breaks deepagents ``resolve_model``
    (binding is not a ``BaseChatModel``), so we MethodType-patch the instance.
    """
    import types

    if not is_gemini_model(model):
        return model
    if getattr(model, _MARKER, False):
        return model

    orig_generate = model._generate
    orig_agenerate = model._agenerate

    def _generate(self: Any, *args: Any, **kwargs: Any):  # noqa: ANN401
        return orig_generate(*args, **_inject_server_side_tool_config(kwargs))

    async def _agenerate(self: Any, *args: Any, **kwargs: Any):  # noqa: ANN401
        return await orig_agenerate(*args, **_inject_server_side_tool_config(kwargs))

    model._generate = types.MethodType(_generate, model)  # type: ignore[method-assign]
    model._agenerate = types.MethodType(_agenerate, model)  # type: ignore[method-assign]
    setattr(model, _MARKER, True)
    logger.info(
        "gemini web_search: enabled include_server_side_tool_invocations model=%s",
        model_identifier(model),
    )
    return model


def _inject_server_side_tool_config(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(kwargs)
    tc = out.get("tool_config")
    if tc is None:
        out["tool_config"] = {"include_server_side_tool_invocations": True}
        return out
    if isinstance(tc, dict):
        if not tc.get("include_server_side_tool_invocations"):
            tc = dict(tc)
            tc["include_server_side_tool_invocations"] = True
            out["tool_config"] = tc
        return out
    # ToolConfig pydantic object
    try:
        if getattr(tc, "include_server_side_tool_invocations", None) is not True:
            out["tool_config"] = tc.model_copy(
                update={"include_server_side_tool_invocations": True}
            )
    except Exception:
        out["tool_config"] = {"include_server_side_tool_invocations": True}
    return out


def gemini_grounded_search(query: str, *, model: str = "gemini-2.5-flash") -> Dict[str, Any]:
    """One-shot Gemini Grounding (no deepagents). Safe for Gemini 2.5."""
    import os

    from google import genai
    from google.genai import types

    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=key) if key else genai.Client()
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2,
    )
    resp = client.models.generate_content(
        model=model,
        contents=(
            "Search the web and return concise factual notes with source URLs.\n"
            f"Query: {query}"
        ),
        config=config,
    )
    text = getattr(resp, "text", None) or ""
    urls: List[str] = []
    try:
        cands = getattr(resp, "candidates", None) or []
        gm = getattr(cands[0], "grounding_metadata", None) if cands else None
        for ch in getattr(gm, "grounding_chunks", None) or []:
            web = getattr(ch, "web", None)
            uri = getattr(web, "uri", None) if web else None
            if uri:
                urls.append(str(uri))
    except Exception:
        pass
    return {"notes": text[:6000], "urls": urls[:12]}


def make_gemini_grounding_search_tool(
    *, search_model: Optional[str] = None
) -> BaseTool:
    """FC-compatible search tool: runs Grounding in a separate generate_content call."""

    def _search(query: str) -> Dict[str, Any]:
        """Run a web search via Gemini Google Search grounding."""
        mid = search_model or "gemini-2.5-flash"
        return gemini_grounded_search(query, model=mid)

    return StructuredTool.from_function(
        func=_search,
        name="internet_search",
        description=(
            "Web search via Gemini Grounding with Google Search. "
            "Call at least once before writing research conclusions."
        ),
    )


def resolve_provider_web_search(
    model: BaseChatModel,
) -> Tuple[BaseChatModel, List[ProviderSearchTool], str]:
    """Pick official search tool(s) for this chat model.

    Returns:
        (possibly_wrapped_model, search_tools, strategy_name)
    """
    if is_openai_model(model):
        return model, [{"type": "web_search"}], "openai_web_search"

    if is_gemini3_model(model):
        wrapped = enable_gemini_server_side_tool_invocations(model)
        return wrapped, [{"google_search": {}}], "gemini3_google_search+server_side_flag"

    if is_gemini_model(model):
        # 2.5 / other: built-in dict cannot mix with deepagents FC tools.
        logger.warning(
            "gemini web_search: model=%s cannot combine built-in google_search with "
            "function calling; using grounding-as-fn-tool. Prefer gemini-3.x or OpenAI "
            "for deepagents research stages.",
            model_identifier(model),
        )
        return (
            model,
            [make_gemini_grounding_search_tool(search_model=model_identifier(model))],
            "gemini_grounding_fn_tool",
        )

    raise ValueError(
        f"No provider web_search adapter for model={model_identifier(model)!r}. "
        "Supported: OpenAI gpt-* / Gemini. Or pass a custom Tavily tool."
    )


def merge_tools_with_web_search(
    model: BaseChatModel,
    tools: Sequence[Any],
    *,
    enable_web_search: bool,
) -> Tuple[BaseChatModel, List[Any]]:
    """If enabled, prepend provider search tools and adapt the model."""
    if not enable_web_search:
        return model, list(tools)
    adapted, search_tools, strategy = resolve_provider_web_search(model)
    logger.info(
        "provider web_search enabled strategy=%s model=%s search_tools=%d",
        strategy,
        model_identifier(adapted),
        len(search_tools),
    )
    return adapted, [*search_tools, *list(tools)]
