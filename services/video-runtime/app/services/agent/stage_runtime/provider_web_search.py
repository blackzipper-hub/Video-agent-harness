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

from langchain_core.callbacks import BaseCallbackHandler
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


_TRACKING_PARAMS = ("utm_source", "utm_medium", "utm_campaign", "utm_term")


def canonical_url(url: str) -> str:
    """Strip the tracking params providers bolt onto cited links.

    OpenAI appends ``?utm_source=openai`` to every citation. Left in place the
    same page is two different strings depending on who found it, which breaks
    any set membership test built on the citation record.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    text = str(url or "").strip()
    if not text.startswith(("http://", "https://")):
        return text
    parts = urlsplit(text)
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def search_queries(message: Any) -> List[str]:
    """Queries the provider actually ran on one model turn.

    OpenAI puts them on ``web_search_call`` content blocks; Gemini puts them
    on ``response_metadata.grounding_metadata.web_search_queries``. Either
    list is the search budget — text the model *says* it searched does not
    count.
    """
    found: List[str] = []

    def add(value: object) -> None:
        text = str(value or "").strip()
        if text and text not in found:
            found.append(text)

    metadata = getattr(message, "response_metadata", None) or {}
    grounding = metadata.get("grounding_metadata") or {}
    if isinstance(grounding, dict):
        for query in grounding.get("web_search_queries") or []:
            add(query)
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "web_search_call":
                continue
            action = block.get("action") or {}
            add(action.get("query") if isinstance(action, dict) else action)
    return found


def _source_url(item: Any) -> str:
    if isinstance(item, str):
        return canonical_url(item)
    if isinstance(item, dict):
        return canonical_url(item.get("url") or item.get("uri") or "")
    return ""


def search_sources(message: Any) -> Dict[str, str]:
    """URLs OpenAI put on ``web_search_call.action.sources``.

    This is the full result list for the turn. It is only present when the
    request asked for ``include=["web_search_call.action.sources"]``. Unlike
    ``url_citation`` annotations, it survives a same-turn function call.
    """
    found: Dict[str, str] = {}
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return found
    for block in content:
        if not isinstance(block, dict):
            continue
        raw: Any = []
        action = block.get("action")
        if isinstance(action, dict):
            raw = action.get("sources") or []
        if not raw and block.get("type") in ("web_search_result", "server_tool_result"):
            output = block.get("output")
            if isinstance(output, dict):
                raw = output.get("sources") or []
        for item in raw or []:
            url = _source_url(item)
            if url:
                title = item.get("title") if isinstance(item, dict) else ""
                found.setdefault(url, str(title or ""))
    return found


def url_citations(message: Any) -> Dict[str, str]:
    """Map canonical URL to title for every citation annotation on one message.

    A subset of ``search_sources``: only pages the model cited in prose.
    OpenAI reports the real destination here; Gemini grounding chunks only
    ever expose an expiring redirect.
    """
    found: Dict[str, str] = {}
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return found
    for block in content:
        if not isinstance(block, dict):
            continue
        for annotation in block.get("annotations") or []:
            if not isinstance(annotation, dict):
                continue
            if annotation.get("type") not in ("url_citation", "citation"):
                continue
            url = canonical_url(annotation.get("url") or "")
            if url:
                found.setdefault(url, str(annotation.get("title") or ""))
    return found


class CitationRegistry(BaseCallbackHandler):
    """Every URL provider search returned during one stage run.

    Attached as a callback rather than read off the final message list because
    the write tool has to consult it *during* the run: rejecting a fabricated
    citation is only useful if the agent still has a turn left to fix it.
    """

    def __init__(self) -> None:
        self.urls: Dict[str, str] = {}
        self.queries: List[str] = []

    def on_llm_end(self, response: Any, **_: Any) -> None:  # noqa: ANN401
        for batch in getattr(response, "generations", None) or []:
            for generation in batch or []:
                message = getattr(generation, "message", None)
                if message is None:
                    continue
                self.urls.update(search_sources(message))
                self.urls.update(url_citations(message))
                for query in search_queries(message):
                    if query not in self.queries:
                        self.queries.append(query)

    def allows(self, url: str) -> bool:
        """True when search actually returned this page, or nothing searched.

        An empty registry means the provider reported no citations at all —
        Gemini's grounding path never populates one — so the gate stays open
        rather than rejecting every source on a provider it cannot audit.
        """
        return not self.urls or canonical_url(url) in self.urls


_OPENAI_SEARCH_SOURCES = "web_search_call.action.sources"


def _with_search_sources(model: BaseChatModel) -> BaseChatModel:
    """Ask Responses API for the full search-result URL list.

    Default OpenAI output only stamps ``url_citation`` on prose. A same-turn
    ``write_research`` call produces no prose, so the list never arrives unless
    this include is set.
    """
    current = list(getattr(model, "include", None) or [])
    if _OPENAI_SEARCH_SOURCES in current:
        return model
    try:
        return model.model_copy(update={"include": [*current, _OPENAI_SEARCH_SOURCES]})
    except Exception:
        return model


def resolve_provider_web_search(
    model: BaseChatModel,
) -> Tuple[BaseChatModel, List[ProviderSearchTool], str]:
    """Pick official search tool(s) for this chat model.

    Returns:
        (possibly_wrapped_model, search_tools, strategy_name)
    """
    if is_openai_model(model):
        return (
            _with_search_sources(model),
            [{"type": "web_search"}],
            "openai_web_search",
        )

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
