"""
通用 LLM 重试 / fallback：异常分桶、resilience 配置合并、线性路由展开、执行器骨架。

与 PROMPTS_CONFIG 的衔接方式（设计稿 §3）：
- 每条 prompt 条目可有可选 ``resilience`` dict，与 ``model_config`` 并列；
- 结构化 **单策略**：``structured_output_strategy``（``provider`` / ``tool``）或 ``structured_strategy_order`` 仅首项有效；fallback 模型沿用同一策略。
- **首选模型**始终来自 ``model_config["model"]``；多模型仅通过 ``resilience.model_fallback_chain`` 显式列出（无默认启发式对端）。
- 对外统一入口 ``ainvoke_structured_resilient``（``StructuredResilienceKind`` 选分支）。均通过 ``create_llm_from_model_config`` 建模型，与 ``load_prompt_with_fallback`` 同源（见 ``docs/design/llm_invocation_patterns.md``）。

**建议阅读顺序**（从配置到调用）：

1. ``merge_resilience_config`` → ``parse_model_fallback_chain`` / ``resolve_structured_output_strategy`` →
   ``build_linear_structured_routes`` 或 ``build_model_only_routes``：把单条 prompt 的 ``resilience`` 转成
   ``routes``（每条是 ``(model_id, strategy_or_None)``）。
2. ``execute_with_resilience``：按 ``routes`` 顺序调用传入的 ``invoke_fn(route)``；单条路由上按异常桶做
   「同路由额外重试」，耗尽再进下一条；``AUTH_CONFIG`` 不重试、不换路。
3. ``ainvoke_structured_resilient``：按 ``kind`` 拼 ``invoke_fn``（建 LLM / agent），再包上一层
   ``execute_with_resilience``。

三种「恢复」层次（勿混为一谈）：

- **外层路由**：换模型（``model_fallback_chain``）+ 按桶重试（``same_route_extra_retries`` / ``per_bucket``）。
- **Tool 策略图内**：``ToolStrategy(..., handle_errors=True)``，LangChain 在 agent 图里处理工具/结构化相关错误。
- **可选**：``wrap_agent_parse_fallback``，Provider 结构化解析失败后再尝试从文本里解析。

详见: docs/design/llm_universal_retry_fallback_design.md
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Sequence, Type, TypeVar

from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.chat.prompts.prompt_loader import create_llm_from_model_config

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _prompt_entry_log_label(entry: Mapping[str, Any]) -> str:
    f = entry.get("file")
    if isinstance(f, str) and f.strip():
        return f.strip()[:160]
    d = entry.get("description")
    if isinstance(d, str) and d.strip():
        return d.strip()[:160]
    return "prompt_entry"


def _log_ctx_suffix(ctx: Mapping[str, Any] | None) -> str:
    """供 performance 日志串联：run_id / thread_id / caller 等（调用方传入 dict，键可扩展）。"""
    if not ctx:
        return ""
    parts: list[str] = []
    for k in ("run_id", "thread_id", "conversation_id", "caller", "batch_index"):
        if k not in ctx:
            continue
        v = ctx[k]
        if v is None or v == "":
            continue
        parts.append(f"{k}={v}")
    return (" | " + " ".join(parts)) if parts else ""


class StructuredResilienceKind(str, Enum):
    """``ainvoke_structured_resilient`` 分支：``create_agent`` / ``langgraph.prebuilt.create_react_agent`` / 已拼好的 messages + structured。"""

    CREATE_AGENT = "create_agent"
    STRUCTURED_CHAT_MESSAGES = "structured_chat_messages"
    CREATE_REACT_AGENT = "create_react_agent"


class ErrorBucket:
    """``classify_llm_exception`` 的返回值；供 ``execute_with_resilience`` 决定能否同路由重试、是否换模型。"""
    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    BAD_REQUEST = "bad_request"
    STRUCTURED_PARSE = "structured_parse"
    AUTH_CONFIG = "auth_config"
    UNKNOWN = "unknown"


DEFAULT_RESILIENCE: dict[str, Any] = {
    "same_route_extra_retries": 1,
    "max_fallback_steps": 1,
    # 单策略：不在 provider 失败后换 tool（同一路由下重试仍用同一策略；换模型见 model_fallback_chain）。
    "structured_strategy_order": ("provider",),
    "per_bucket": {
        "bad_request": {"same_route_extra_retries": 0},
        "auth_config": {"same_route_extra_retries": 0},
    },
}


def classify_llm_exception(exc: BaseException) -> str:
    """将异常归入策略表使用的桶。"""
    from langchain.agents.structured_output import StructuredOutputValidationError

    if isinstance(exc, StructuredOutputValidationError):
        return ErrorBucket.STRUCTURED_PARSE
    if isinstance(exc, TimeoutError):
        return ErrorBucket.TRANSIENT
    try:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            RateLimitError,
        )
    except ImportError:
        return ErrorBucket.UNKNOWN
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return ErrorBucket.TRANSIENT
    if isinstance(exc, RateLimitError):
        return ErrorBucket.RATE_LIMIT
    if isinstance(exc, BadRequestError):
        return ErrorBucket.BAD_REQUEST
    if isinstance(exc, AuthenticationError):
        return ErrorBucket.AUTH_CONFIG
    if isinstance(exc, InternalServerError):
        return ErrorBucket.SERVER
    return ErrorBucket.UNKNOWN


def merge_resilience_config(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """合并默认 resilience 与 prompt 条目覆盖（浅合并 + per_bucket 深合并）。"""
    out = dict(base)
    if not override:
        return out
    for k, v in override.items():
        if k == "per_bucket" and isinstance(v, dict) and isinstance(out.get("per_bucket"), dict):
            merged = dict(out["per_bucket"])
            merged.update(v)
            out["per_bucket"] = merged
        else:
            out[k] = v
    return out


def expand_structured_routes(
    *,
    primary_model: str,
    fallback_model: str | None,
    strategy_order: tuple[str, ...],
) -> list[tuple[str, str]]:
    """
    展开「先在同一模型上按顺序试 strategy，再换备用模型试首选 strategy」的路由列表。
    与 design 文档 §5.2 一致；max_fallback_steps 可在调用方切片截断。

    注：主代码路径已改为 ``build_linear_structured_routes``（单策略 + 模型链）；本函数仍保留供单测
    与文档对照，新特性优先改 ``build_linear_structured_routes``。
    """
    routes: list[tuple[str, str]] = []
    for s in strategy_order:
        routes.append((primary_model, s))
    if fallback_model:
        first = strategy_order[0] if strategy_order else "provider"
        routes.append((fallback_model, first))
    return routes


def should_retry_same_route(bucket: str, extra_retries: int, attempt_index: int, per_bucket: dict[str, Any] | None) -> bool:
    """attempt_index：同一 route 上已连续失败次数（不含即将进行的那次重试）。"""
    if extra_retries <= 0:
        return False
    pb = (per_bucket or {}).get(bucket, {})
    if pb.get("same_route_extra_retries") is not None:
        extra_retries = int(pb["same_route_extra_retries"])
    if extra_retries <= 0:
        return False
    return attempt_index < extra_retries


def _normalize_model_id(model: Any) -> str:
    if model is None:
        return ""
    if hasattr(model, "value"):
        return str(model.value)
    return str(model)


def parse_model_fallback_chain(
    primary_model: str,
    merged: Mapping[str, Any],
) -> tuple[str, ...]:
    """返回完整链 ``(primary, *fallbacks)``；仅 ``resilience.model_fallback_chain`` 可扩展，否则单模型。

    链项可为模型 id 字符串，或含 ``model`` / ``role`` 的 dict（与 VA 侧一致）。
    若配置里链首元素与 ``primary_model``（来自 ``model_config.model``）不一致，会在前面补上
    ``primary_model``，避免漏掉当前条目指定的主模型。
    """
    from prompts.llm_model_profiles import resolve_model_config

    def _item_to_model_id(x: Any) -> str:
        if isinstance(x, Mapping):
            return _normalize_model_id(resolve_model_config(dict(x)).get("model"))
        return _normalize_model_id(x)

    raw = merged.get("model_fallback_chain")
    if raw:
        chain = tuple(_item_to_model_id(x) for x in raw if _item_to_model_id(x))
        if not chain:
            return (primary_model,)
        if chain[0] != primary_model:
            return (primary_model,) + tuple(x for x in chain if x != primary_model)
        return chain
    return (primary_model,)


def build_model_only_routes(
    *,
    primary_model: str,
    merged: Mapping[str, Any],
) -> list[tuple[str, None]]:
    """``create_react_agent`` 等无 provider/tool 双策略时：仅按模型链换路，每路由 ``(model_id, None)``。"""
    chain = parse_model_fallback_chain(primary_model, merged)
    max_fb = int(merged.get("max_fallback_steps", 1))
    kept: list[str] = [chain[0]]
    for m in chain[1:][:max_fb]:
        kept.append(m)
    return [(m, None) for m in kept]


def resolve_structured_output_strategy(merged: Mapping[str, Any]) -> str:
    """
    每条 prompt 只用**一种**结构化策略（``provider`` 或 ``tool``）。

    - ``resilience.structured_output_strategy``：显式 ``\"provider\"`` / ``\"tool\"``（推荐新配置）。
    - 否则取 ``structured_strategy_order`` 的**第一项**；若有多项则打警告并忽略其余（不再做 provider→tool 自动换档）。
    """
    explicit = merged.get("structured_output_strategy")
    if explicit in ("provider", "tool"):
        return str(explicit)
    order = merged.get("structured_strategy_order")
    if isinstance(order, (list, tuple)) and len(order) > 0:
        s = str(order[0])
        if len(order) > 1:
            logger.warning(
                "resilience: structured_strategy_order has %s items; only first %r is used "
                "(cross-strategy fallback disabled; use model_fallback_chain to change model)",
                len(order),
                s,
            )
        if s in ("provider", "tool"):
            return s
    return "provider"


def build_linear_structured_routes(
    *,
    primary_model: str,
    merged: Mapping[str, Any],
) -> list[tuple[str, str]]:
    """
    结构化：``primary`` 与每个 fallback 模型各一路由，**策略相同**（由 ``resolve_structured_output_strategy`` 决定）。
    """
    strat = resolve_structured_output_strategy(merged)
    max_fb = int(merged.get("max_fallback_steps", 1))
    chain = parse_model_fallback_chain(primary_model, merged)
    fallback_only = list(chain[1:][:max_fb])
    routes = [(primary_model, strat)]
    for m in fallback_only:
        routes.append((m, strat))
    return routes


@dataclass(frozen=True)
class ResilienceContext:
    """随子调用传递时可 ``narrowed()`` 收紧上限，避免指数爆炸（设计稿 §6）。"""

    same_route_extra_retries: int = 1
    max_fallback_steps: int = 1
    structured_strategy_order: tuple[str, ...] = ("provider",)
    per_bucket: Mapping[str, Any] = field(default_factory=dict)

    def narrowed(self, **kwargs: Any) -> ResilienceContext:
        return replace(self, **kwargs)


def resilience_context_from_merged(merged: Mapping[str, Any]) -> ResilienceContext:
    pb = merged.get("per_bucket")
    if not isinstance(pb, dict):
        pb = {}
    eff = resolve_structured_output_strategy(merged)
    return ResilienceContext(
        same_route_extra_retries=int(merged.get("same_route_extra_retries", 1)),
        max_fallback_steps=int(merged.get("max_fallback_steps", 1)),
        structured_strategy_order=(eff,),
        per_bucket=dict(pb),
    )


def build_resilience_bundle_from_prompt_entry(
    entry: Mapping[str, Any],
    *,
    structured_schema: Type[BaseModel] | None = None,
    model_only_routes: bool = False,
) -> tuple[ResilienceContext, list[tuple[str, str | None]]]:
    """
    从单条 ``PROMPTS_CONFIG`` 风格条目构建 ``(ResilienceContext, routes)``。

    - **与 model_config 的关系**：``primary_model`` 仅来自 ``entry["model_config"]["model"]``，
      不与 ``resilience`` 合并；多模型仅 ``resilience.model_fallback_chain``。
    - ``structured_schema``：覆盖 ``entry["schema"]``（条目里为 None 时由调用方传入）。
    - 无有效 schema 时视为纯 chat：单路由 ``(model, None)``（仅用于非结构化 execute 扩展；结构化入口会先行校验 schema）。
    - ``model_only_routes=True``：不做 provider/tool 展开，仅模型链（供 ``create_react_agent``）。
    """
    from prompts.llm_model_profiles import resolve_model_config

    # role-only model_config（LLM_QUALITY 档位）需先解析成具体 model
    mc = resolve_model_config(entry.get("model_config") or {})
    primary = _normalize_model_id(mc.get("model", ""))
    merged = merge_resilience_config(DEFAULT_RESILIENCE, dict(entry.get("resilience") or {}))
    ctx = resilience_context_from_merged(merged)
    eff_schema: Type[BaseModel] | None = structured_schema
    if eff_schema is None:
        sch = entry.get("schema")
        if isinstance(sch, type) and issubclass(sch, BaseModel):
            eff_schema = sch
    has_schema = eff_schema is not None
    if not has_schema:
        if not primary:
            raise ValueError("prompt entry missing model_config.model (or role)")
        mo = build_model_only_routes(primary_model=primary, merged=merged)
        return ctx, [(m, s) for m, s in mo]
    if not primary:
        raise ValueError("prompt entry missing model_config.model (or role)")
    if model_only_routes:
        routes_raw = build_model_only_routes(primary_model=primary, merged=merged)
        routes: list[tuple[str, str | None]] = [(m, s) for m, s in routes_raw]
    else:
        routes_typed = build_linear_structured_routes(primary_model=primary, merged=merged)
        routes = [(m, s) for m, s in routes_typed]
    return ctx, routes


def effective_same_route_retries(merged_or_ctx: Mapping[str, Any] | ResilienceContext, bucket: str) -> int:
    if isinstance(merged_or_ctx, ResilienceContext):
        base = merged_or_ctx.same_route_extra_retries
        per_bucket = dict(merged_or_ctx.per_bucket)
    else:
        base = int(merged_or_ctx.get("same_route_extra_retries", 1))
        per_bucket = dict(merged_or_ctx.get("per_bucket") or {})
    pb = per_bucket.get(bucket, {})
    if isinstance(pb, dict) and pb.get("same_route_extra_retries") is not None:
        return int(pb["same_route_extra_retries"])
    return base


async def execute_with_resilience(
    invoke_fn: Callable[[tuple[str, str | None]], Awaitable[T]],
    *,
    routes: Sequence[tuple[str, str | None]],
    context: ResilienceContext,
    log_context: Mapping[str, Any] | None = None,
) -> T:
    """
    按线性路由依次尝试；每条路由上对「当前异常桶」允许的同路由重试耗尽后进入下一路由。

    - ``AUTH_CONFIG``：不重试、不换路由，直接抛出（设计稿 §4.1）。

    ``route`` 元组语义：``(model_id, strategy_or_None)``。``invoke_fn`` 必须用当前 ``route`` 建 LLM/agent
    再调用，这样换路由时才真正换模型/策略实现。

    控制流：``for route`` 为最外层；内层 ``while True`` 为「同一路由」上的重试，成功则 return，失败则
    ``classify_llm_exception`` → ``effective_same_route_retries``，未超限则 continue，否则 ``break`` 进
    下一条 ``route``。
    """
    merged_proxy = {
        "same_route_extra_retries": context.same_route_extra_retries,
        "per_bucket": context.per_bucket,
    }
    _lc = _log_ctx_suffix(log_context)
    route_list = list(routes)
    if not route_list:
        logger.warning("llm_resilience execute_with_resilience: empty routes%s", _lc)
    else:
        logger.info(
            "llm_resilience execute start routes=%s same_route_extra_retries=%s%s",
            route_list,
            context.same_route_extra_retries,
            _lc,
        )
    last_exc: BaseException | None = None
    route_index = 0
    for route in route_list:
        route_index += 1
        failures_on_route = 0
        while True:
            try:
                out = await invoke_fn(route)
                logger.info(
                    "llm_resilience success route_index=%s/%s model=%s strat=%s%s",
                    route_index,
                    len(route_list),
                    route[0],
                    route[1],
                    _lc,
                )
                return out
            except BaseException as e:
                last_exc = e
                bucket = classify_llm_exception(e)
                if bucket == ErrorBucket.AUTH_CONFIG:
                    logger.warning(
                        "llm_resilience auth/config abort route=%s exc_type=%s msg=%s%s",
                        route,
                        type(e).__name__,
                        str(e)[:300],
                        _lc,
                    )
                    raise
                extra = effective_same_route_retries(merged_proxy, bucket)
                if failures_on_route < extra:
                    failures_on_route += 1
                    logger.warning(
                        "llm_resilience same_route_retry route=%s bucket=%s failures_on_route=%s max_extra=%s exc_type=%s msg=%s%s",
                        route,
                        bucket,
                        failures_on_route,
                        extra,
                        type(e).__name__,
                        str(e)[:300],
                        _lc,
                    )
                    continue
                logger.warning(
                    "llm_resilience leaving_route route=%s bucket=%s exhausted_same_route_retries=%s exc_type=%s msg=%s%s",
                    route,
                    bucket,
                    failures_on_route,
                    type(e).__name__,
                    str(e)[:300],
                    _lc,
                )
                break
    if last_exc is not None:
        logger.error(
            "llm_resilience all routes failed last_exc_type=%s msg=%s%s",
            type(last_exc).__name__,
            str(last_exc)[:500],
            _lc,
        )
        raise last_exc
    raise RuntimeError("execute_with_resilience: empty routes")


def _resolve_structured_schema(
    prompt_entry: Mapping[str, Any],
    structured_schema: Type[BaseModel] | None,
) -> Type[BaseModel]:
    if structured_schema is not None:
        return structured_schema
    sch = prompt_entry.get("schema")
    if isinstance(sch, type) and issubclass(sch, BaseModel):
        return sch
    raise ValueError(
        "结构化调用需要 prompt_entry['schema'] 或显式传入 structured_schema=YourPydanticModel"
    )


def _bind_structured_output_to_llm(
    llm: Any,
    *,
    model_id: str,
    schema: type[BaseModel],
    include_raw: bool,
) -> Any:
    """与 ``prompt_loader._load_prompt_from_local`` 一致：Gemini 3 使用 ``method=json_schema``。"""
    mid = str(model_id).lower()
    if "gemini-3" in mid:
        return llm.with_structured_output(schema, include_raw=include_raw, method="json_schema")
    return llm.with_structured_output(schema, include_raw=include_raw)


async def ainvoke_structured_resilient(
    *,
    prompt_entry: Mapping[str, Any],
    kind: StructuredResilienceKind,
    agent_inputs: dict[str, Any] | None = None,
    structured_chat_messages: Sequence[BaseMessage] | None = None,
    include_raw: bool = True,
    log_context: Mapping[str, Any] | None = None,
    structured_schema: Type[BaseModel] | None = None,
    agent_tools: Sequence[Any] | None = None,
    context_schema: Any | None = None,
    agent_invoke_context: Any | None = None,
    wrap_agent_parse_fallback: bool = False,
    structured_output: bool = True,
    llm_invoke_config: Mapping[str, Any] | None = None,
) -> Any:
    """
    结构化调用的统一入口：``create_agent`` / ``create_react_agent`` / 已拼好的 ``structured_chat_messages``。

    - ``CREATE_AGENT``：``agent_inputs``；可选 ``agent_tools``、``context_schema``；``agent_invoke_context`` 传给 ``ainvoke(..., context=...)``；``wrap_agent_parse_fallback=True`` 时用 ``invoke_agent_with_parse_fallback``（Provider 解析失败兜底）。
    - ``CREATE_REACT_AGENT``：``langgraph.prebuilt.create_react_agent``；须 ``agent_tools``；路由仅换模型（无 provider/tool 双策略）。
    - ``STRUCTURED_CHAT_MESSAGES``：须 ``structured_chat_messages``。
    - ``structured_schema``：当 ``prompt_entry['schema']`` 为 None 时必须传入（避免 prompt_config 循环依赖）。
    - ``structured_output=False``：仅 ``CREATE_AGENT``；``create_agent`` 不绑定 ``response_format``（流式子 Agent 等）。
    - ``llm_invoke_config``：传给 ``ainvoke(..., config=...)``（如 ``RunnableConfig(recursion_limit=...)``）。

    实现上：先 ``build_resilience_bundle_from_prompt_entry`` 得到 ``(ctx, routes)``，再为每个 ``kind`` 定义
    闭包 ``invoke_fn(route)``，最后 ``return await execute_with_resilience(invoke_fn, routes=..., context=ctx)``。
    """
    if not structured_output and kind != StructuredResilienceKind.CREATE_AGENT:
        raise ValueError("structured_output=False is only supported for kind=CREATE_AGENT")
    schema: Type[BaseModel] | None
    if structured_output:
        schema = _resolve_structured_schema(prompt_entry, structured_schema)
    else:
        schema = None
    model_only = kind == StructuredResilienceKind.CREATE_REACT_AGENT
    ctx, routes = build_resilience_bundle_from_prompt_entry(
        prompt_entry,
        structured_schema=schema,
        model_only_routes=model_only,
    )
    from prompts.llm_model_profiles import resolve_model_config

    mc_base = resolve_model_config(prompt_entry.get("model_config") or {})
    base_model = mc_base.get("model")
    pe_label = _prompt_entry_log_label(prompt_entry)
    if kind in (
        StructuredResilienceKind.CREATE_AGENT,
        StructuredResilienceKind.CREATE_REACT_AGENT,
    ):
        _msgs = (agent_inputs or {}).get("messages")
        _n_in = len(_msgs) if isinstance(_msgs, list) else None
    else:
        _n_in = len(structured_chat_messages) if structured_chat_messages is not None else None
    _lc = _log_ctx_suffix(log_context)
    _schema_log = schema.__name__ if schema is not None else "unstructured"
    logger.info(
        "ainvoke_structured_resilient start kind=%s schema=%s prompt=%s base_model=%s routes=%s input_messages=%s include_raw=%s structured_output=%s%s",
        kind.value,
        _schema_log,
        pe_label,
        base_model,
        list(routes),
        _n_in,
        include_raw,
        structured_output,
        _lc,
    )
    _cfg = llm_invoke_config

    if kind == StructuredResilienceKind.CREATE_REACT_AGENT:
        if agent_inputs is None:
            raise ValueError("kind=CREATE_REACT_AGENT requires agent_inputs")
        bound_react_inputs = agent_inputs
        from langgraph.prebuilt import create_react_agent

        async def invoke_react_fn(route: tuple[str, str | None]) -> Any:
            model_id, _strat = route
            mc = {**mc_base, "model": model_id}
            llm = create_llm_from_model_config(mc)
            _tools = list(agent_tools or [])

            def _build_react():
                return create_react_agent(
                    llm,
                    tools=_tools,
                    response_format=schema,
                )

            agent = await asyncio.to_thread(_build_react)
            if _cfg:
                return await agent.ainvoke(bound_react_inputs, config=_cfg)
            return await agent.ainvoke(bound_react_inputs)

        return await execute_with_resilience(
            invoke_react_fn, routes=routes, context=ctx, log_context=log_context
        )

    if kind == StructuredResilienceKind.CREATE_AGENT:
        if agent_inputs is None:
            raise ValueError("kind=CREATE_AGENT requires agent_inputs")
        bound_agent_inputs = agent_inputs
        tools_list = list(agent_tools or [])

        # CREATE_AGENT + structured：response_format 用 ProviderStrategy 或 ToolStrategy；与外层换模型无关，
        # 每条 route 的 strat 相同（均来自 resolve_structured_output_strategy）。
        async def invoke_fn(route: tuple[str, str | None]) -> Any:
            model_id, strat = route
            mc = {**mc_base, "model": model_id}
            llm = create_llm_from_model_config(mc)
            if not structured_output:
                ca_u: dict[str, Any] = dict(model=llm, tools=tools_list)
                if context_schema is not None:
                    ca_u["context_schema"] = context_schema
                agent = await asyncio.to_thread(create_agent, **ca_u)
                if _cfg:
                    if agent_invoke_context is not None:
                        return await agent.ainvoke(
                            bound_agent_inputs,
                            context=agent_invoke_context,
                            config=_cfg,
                        )
                    return await agent.ainvoke(bound_agent_inputs, config=_cfg)
                if agent_invoke_context is not None:
                    return await agent.ainvoke(
                        bound_agent_inputs, context=agent_invoke_context
                    )
                return await agent.ainvoke(bound_agent_inputs)
            # strat is None：defensive，兼容 (model, None) 路由；正式结构化路由应为 "provider" | "tool"。
            # tool：LangChain 图内 handle_errors=True；provider：无图内 tool 恢复，可配合 wrap_agent_parse_fallback。
            if strat is None:
                rf: ProviderStrategy | ToolStrategy = ToolStrategy(schema, handle_errors=True)
            elif strat == "provider":
                rf = ProviderStrategy(schema)
            else:
                rf = ToolStrategy(schema, handle_errors=True)
            ca_kw: dict[str, Any] = dict(
                model=llm, tools=tools_list, response_format=rf
            )
            if context_schema is not None:
                ca_kw["context_schema"] = context_schema
            agent = await asyncio.to_thread(create_agent, **ca_kw)
            if wrap_agent_parse_fallback:
                from app.chat.services.agent.utils.prompt_utils import (
                    invoke_agent_with_parse_fallback,
                )

                return await invoke_agent_with_parse_fallback(
                    agent,
                    bound_agent_inputs,
                    context=agent_invoke_context,
                    result_model=schema,
                )
            if _cfg:
                if agent_invoke_context is not None:
                    return await agent.ainvoke(
                        bound_agent_inputs,
                        context=agent_invoke_context,
                        config=_cfg,
                    )
                return await agent.ainvoke(bound_agent_inputs, config=_cfg)
            if agent_invoke_context is not None:
                return await agent.ainvoke(
                    bound_agent_inputs, context=agent_invoke_context
                )
            return await agent.ainvoke(bound_agent_inputs)

        return await execute_with_resilience(
            invoke_fn, routes=routes, context=ctx, log_context=log_context
        )

    if kind == StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES:
        if structured_chat_messages is None:
            raise ValueError("kind=STRUCTURED_CHAT_MESSAGES requires structured_chat_messages")
        bound_msgs = list(structured_chat_messages)

        # provider：直接 llm.with_structured_output；tool：无业务 tools 的 create_agent + ToolStrategy（与 CREATE_AGENT 一致策略语义）。
        async def invoke_fn_msgs(route: tuple[str, str | None]) -> Any:
            model_id, strat = route
            mc = {**mc_base, "model": model_id}
            llm = create_llm_from_model_config(mc)
            if strat is None or strat == "provider":
                structured = _bind_structured_output_to_llm(
                    llm, model_id=model_id, schema=schema, include_raw=include_raw
                )
                if _cfg:
                    return await structured.ainvoke(bound_msgs, config=_cfg)
                return await structured.ainvoke(bound_msgs)
            if strat == "tool":
                agent = await asyncio.to_thread(
                    create_agent,
                    model=llm,
                    tools=[],
                    response_format=ToolStrategy(schema, handle_errors=True),
                )
                if _cfg:
                    out = await agent.ainvoke({"messages": bound_msgs}, config=_cfg)
                else:
                    out = await agent.ainvoke({"messages": bound_msgs})
                sr = out.get("structured_response")
                if include_raw:
                    return {"parsed": sr, "raw": None}
                return sr
            raise ValueError(f"unknown structured route strategy: {strat!r}")

        return await execute_with_resilience(
            invoke_fn_msgs, routes=routes, context=ctx, log_context=log_context
        )

    raise ValueError(f"unknown StructuredResilienceKind: {kind!r}")
