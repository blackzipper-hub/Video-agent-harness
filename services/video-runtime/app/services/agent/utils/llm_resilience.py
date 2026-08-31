"""
通用 LLM 重试 / fallback：异常分桶、resilience 配置合并、线性路由展开、执行器骨架。

与 PROMPTS_CONFIG 的衔接方式（设计稿 §3）：
- 每条 prompt 条目可有可选 ``resilience`` dict，与 ``model_config`` 并列；
- 结构化 **单策略**：``structured_output_strategy``（``provider`` / ``tool``）或 ``structured_strategy_order`` 仅首项有效；fallback 模型沿用同一策略。
- **首选模型**始终来自 ``model_config``（``model`` 或 ``role``，经 ``resolve_model_config``）；多模型通过 ``resilience.model_fallback_chain``：**list[dict]**，每项为可与主 ``model_config`` 合并的片段（须含 ``model`` 或 ``role``）。
- 对外统一入口 ``ainvoke_structured_resilient``（``StructuredResilienceKind`` 选分支）。均通过 ``create_llm_from_model_config`` 建模型，与 ``load_prompt_with_fallback`` 同源（见 ``docs/llm-and-tool-resilience.md`` 前半部分）。

**建议阅读顺序**（从配置到调用）：

1. ``merge_resilience_config`` → ``build_route_model_configs_list`` / ``resolve_structured_output_strategy`` →
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

详见: docs/llm-and-tool-resilience.md
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Sequence, Type, TypeVar

from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from prompts.prompt_loader import create_llm_from_model_config

from .observability import build_ls_run_config

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


def _is_non_production_llm_verbose() -> bool:
    """仅非 production 打印完整请求与 API 错误体，便于本地/development 排查 400 等。"""
    return os.getenv("ENVIRONMENT", "development").lower() != "production"


async def _rewrite_content_image_urls(content: Any) -> tuple[Any, bool]:
    """将 content list 中的 localhost /files image_url 内联为 data URI。返回 (new_content, changed)。"""
    if not isinstance(content, list):
        return content, False
    from app.utils.file_utils import inline_local_image_url_for_llm

    changed = False
    new_parts: list[Any] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "image_url":
            new_parts.append(part)
            continue
        iu = part.get("image_url")
        url = iu.get("url") if isinstance(iu, dict) else iu
        if not isinstance(url, str) or not url.strip() or url.startswith("data:"):
            new_parts.append(part)
            continue
        new_url = await inline_local_image_url_for_llm(url)
        if new_url == url:
            new_parts.append(part)
            continue
        changed = True
        if isinstance(iu, dict):
            new_parts.append({**part, "image_url": {**iu, "url": new_url}})
        else:
            new_parts.append({**part, "image_url": {"url": new_url}})
    return new_parts, changed


async def _inline_local_images_in_messages(
    messages: Sequence[BaseMessage] | list[Any] | None,
) -> list[Any] | None:
    """
    关键帧等多模态调用前：把指向本机 /files 的 image_url 读盘内联，避免 SDK 同步 HTTP 自调用死锁。
    """
    if not messages:
        return list(messages) if messages is not None else None
    out: list[Any] = []
    for msg in messages:
        content = getattr(msg, "content", None)
        new_content, changed = await _rewrite_content_image_urls(content)
        if not changed:
            out.append(msg)
            continue
        if isinstance(msg, HumanMessage):
            out.append(HumanMessage(content=new_content, additional_kwargs=getattr(msg, "additional_kwargs", {}) or {}))
        elif isinstance(msg, SystemMessage):
            out.append(SystemMessage(content=new_content, additional_kwargs=getattr(msg, "additional_kwargs", {}) or {}))
        elif isinstance(msg, AIMessage):
            out.append(AIMessage(content=new_content, additional_kwargs=getattr(msg, "additional_kwargs", {}) or {}))
        else:
            try:
                out.append(msg.model_copy(update={"content": new_content}))
            except Exception:
                out.append(msg)
    return out


async def _inline_local_images_in_llm_inputs(
    agent_inputs: dict[str, Any] | None,
    structured_chat_messages: Sequence[BaseMessage] | None,
) -> tuple[dict[str, Any] | None, Sequence[BaseMessage] | None]:
    new_agent = agent_inputs
    new_msgs = structured_chat_messages
    if isinstance(agent_inputs, dict) and isinstance(agent_inputs.get("messages"), list):
        inlined = await _inline_local_images_in_messages(agent_inputs["messages"])
        new_agent = {**agent_inputs, "messages": inlined}
    if structured_chat_messages is not None:
        new_msgs = await _inline_local_images_in_messages(structured_chat_messages)
    return new_agent, new_msgs


def _dev_snapshot_llm_inputs(obj: Any) -> Any:
    """将 agent_inputs / messages 中的 BaseMessage 转为可 JSON 序列化的结构。"""
    try:
        from langchain_core.messages import BaseMessage
    except ImportError:
        BaseMessage = ()  # type: ignore[misc, assignment]
    if isinstance(obj, BaseMessage):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {str(k): _dev_snapshot_llm_inputs(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dev_snapshot_llm_inputs(x) for x in obj]
    return obj


def _detail_for_llm_exception(exc: BaseException) -> str:
    """比 str(exc)[:300] 更可读：非 production 附带 HTTP 状态与响应体（若有），便于确认 400 原因。"""
    parts: list[str] = [f"{type(exc).__name__}: {exc}"]
    resp = getattr(exc, "response", None)
    if resp is not None:
        sc = getattr(resp, "status_code", None)
        if sc is not None:
            parts.append(f"http_status={sc}")
        try:
            hdrs = getattr(resp, "headers", None)
            if hdrs is not None:
                rid = hdrs.get("x-request-id") or hdrs.get("openai-request-id")
                if rid:
                    parts.append(f"request_id={rid}")
        except Exception:
            pass
        if _is_non_production_llm_verbose():
            try:
                j = resp.json()
                parts.append(f"response_json={json.dumps(j, ensure_ascii=False)[:8000]}")
            except Exception:
                try:
                    txt = getattr(resp, "text", None)
                    if txt:
                        parts.append(f"response_text={txt[:4000]!r}")
                except Exception:
                    pass
    body = getattr(exc, "body", None)
    if body is not None and _is_non_production_llm_verbose():
        try:
            if isinstance(body, (dict, list)):
                parts.append(f"body={json.dumps(body, ensure_ascii=False)[:4000]}")
            else:
                parts.append(f"body={repr(str(body)[:4000])}")
        except Exception:
            parts.append(f"body={body!r}")
    out = " | ".join(parts)
    if not _is_non_production_llm_verbose():
        return out[:800]
    return out


class StructuredResilienceKind(str, Enum):
    """``ainvoke_structured_resilient`` 分支：``create_agent`` / ``langgraph.prebuilt.create_react_agent`` / 已拼好的 messages + structured。"""

    CREATE_AGENT = "create_agent"
    STRUCTURED_CHAT_MESSAGES = "structured_chat_messages"
    CREATE_REACT_AGENT = "create_react_agent"


def _log_dev_full_llm_request(
    *,
    kind: StructuredResilienceKind,
    agent_inputs: dict[str, Any] | None,
    structured_chat_messages: Sequence[BaseMessage] | None,
    log_context: Mapping[str, Any] | None,
) -> None:
    if not _is_non_production_llm_verbose():
        return
    _lc = _log_ctx_suffix(log_context)
    try:
        if kind in (StructuredResilienceKind.CREATE_AGENT, StructuredResilienceKind.CREATE_REACT_AGENT):
            snap = _dev_snapshot_llm_inputs(agent_inputs or {})
            payload = json.dumps(snap, ensure_ascii=False, default=str)
        elif kind == StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES:
            msgs = structured_chat_messages or ()
            snap = [_dev_snapshot_llm_inputs(m) for m in msgs]
            payload = json.dumps(snap, ensure_ascii=False, default=str)
        else:
            payload = "{}"
    except Exception as e:
        logger.warning("llm_resilience dev_request_snapshot_failed exc=%s%s", type(e).__name__, _lc)
        return
    logger.info("llm_resilience dev_full_request kind=%s payload=%s%s", kind.value, payload, _lc)


def _log_dev_llm_request_digest(
    *,
    kind: StructuredResilienceKind,
    agent_inputs: dict[str, Any] | None,
    structured_chat_messages: Sequence[BaseMessage] | None,
    log_context: Mapping[str, Any] | None,
) -> None:
    """非 production：与 ``dev_full_request`` 配套，记录 UTF-8 字节长度与是否存在孤 surrogate（辅助对齐线上 400 调查）。"""
    if not _is_non_production_llm_verbose():
        return
    _lc = _log_ctx_suffix(log_context)

    def _maybe_surrogate(obj: Any) -> bool:
        if isinstance(obj, str):
            return any(0xD800 <= ord(ch) <= 0xDFFF for ch in obj)
        if isinstance(obj, dict):
            return any(_maybe_surrogate(v) for v in obj.values())
        if isinstance(obj, (list, tuple)):
            return any(_maybe_surrogate(x) for x in obj)
        return False

    try:
        if kind in (StructuredResilienceKind.CREATE_AGENT, StructuredResilienceKind.CREATE_REACT_AGENT):
            snap = _dev_snapshot_llm_inputs(agent_inputs or {})
        elif kind == StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES:
            snap = [_dev_snapshot_llm_inputs(m) for m in (structured_chat_messages or ())]
        else:
            snap = {}
        raw = json.dumps(snap, ensure_ascii=False, default=str)
        b = raw.encode("utf-8")
        logger.info(
            "llm_resilience dev_request_digest kind=%s utf8_bytes=%s maybe_lone_surrogate=%s%s",
            kind.value,
            len(b),
            _maybe_surrogate(snap),
            _lc,
        )
    except Exception as e:
        logger.warning("llm_resilience dev_request_digest_failed exc=%s%s", type(e).__name__, _lc)


class ErrorBucket:
    """``classify_llm_exception`` 的返回值；供 ``execute_with_resilience`` 决定能否同路由重试、是否换模型。"""
    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    BAD_REQUEST = "bad_request"
    STRUCTURED_PARSE = "structured_parse"
    AUTH_CONFIG = "auth_config"
    # 结构化调用成功（不抛异常）但语义为空（structured_response is None / schema.resilience_empty_reason() / 调用方传入的 result_validator 命中）。
    # 由 _wrap_invoke_with_result_check 抛 EmptyStructuredResponseError，复用同一套 same_route_extra_retries + model_fallback_chain。
    EMPTY_RESPONSE = "empty_response"
    UNKNOWN = "unknown"


class EmptyStructuredResponseError(Exception):
    """LLM 调用本身成功（无异常）但返回的结构化结果在语义上为空，归 ``EMPTY_RESPONSE`` 桶走重试 / 换模型。

    触发来源（三层任一）：
      1) 框架内置：``structured_response is None`` / ``parsed is None``；
      2) schema 自带钩子：``schema_instance.resilience_empty_reason()`` 返回非空字符串；
      3) 调用方传入：``ainvoke_structured_resilient(result_validator=...)`` 返回非空字符串。

    ``raw`` 保留原始返回值（dict / pydantic 实例），方便日志或调用方在最终失败时回退使用。
    """

    def __init__(self, reason: str, *, raw: Any = None) -> None:
        super().__init__(reason)
        self.raw = raw


DEFAULT_RESILIENCE: dict[str, Any] = {
    "same_route_extra_retries": 1,
    "max_fallback_steps": 1,
    # 单策略：不在 provider 失败后换 tool（同一路由下重试仍用同一策略；换模型见 model_fallback_chain）。
    "structured_strategy_order": ("provider",),
    "per_bucket": {
        # 400：偶发「无法解析 JSON body」等可重试一次；确定性参数错误仍会连续失败。
        "bad_request": {"same_route_extra_retries": 1},
        "auth_config": {"same_route_extra_retries": 0},
        # 语义为空（如 Gemini 偶发返回 {"scenes": []}）：同路由重试 1 次，仍空则进入 model_fallback_chain 下一跳。
        "empty_response": {"same_route_extra_retries": 1},
    },
}


def classify_llm_exception(exc: BaseException) -> str:
    """将异常归入策略表使用的桶。"""
    from langchain.agents.structured_output import StructuredOutputValidationError

    if isinstance(exc, EmptyStructuredResponseError):
        return ErrorBucket.EMPTY_RESPONSE
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


def build_route_model_configs_list(
    primary_mc: Mapping[str, Any],
    merged: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """
    由主 ``model_config`` 与 ``resilience.model_fallback_chain``（**list[dict]**，每项须含
    ``model`` 或 ``role``）展开为每条路由的完整 ``model_config``
    （主配置与链项浅合并 ``{**primary_mc, **item}``，再各自 ``resolve_model_config``）。

    若链首项 ``model`` 与主模型不同，则在前面插入「仅主配置」的一跳（与旧版 string chain 补首行为一致）。
    ``max_fallback_steps`` 只限制**链尾追加**的条目数量（主跳 + 至多 N 条 fallback）。
    """
    from prompts.llm_model_profiles import has_model_or_role, resolve_model_config

    base = resolve_model_config(primary_mc)
    primary_id = _normalize_model_id(base.get("model"))
    raw = merged.get("model_fallback_chain")
    if raw is None:
        raw_list: list[Any] = []
    elif isinstance(raw, list):
        raw_list = list(raw)
    else:
        raise TypeError("resilience.model_fallback_chain must be a list of dicts")
    max_fb = int(merged.get("max_fallback_steps", 1))

    if len(raw_list) == 0:
        return [base]

    items: list[dict[str, Any]] = []
    for i, x in enumerate(raw_list):
        if not isinstance(x, dict):
            raise TypeError(
                f"model_fallback_chain[{i}] must be dict(model_config fragment), got {type(x).__name__}"
            )
        if not has_model_or_role(x):
            raise ValueError(
                f"model_fallback_chain[{i}] must include 'model' or 'role'"
            )
        # 链项先独立 resolve（role→model），再覆盖合并到主配置；避免主 model 挡住链项 role
        frag = resolve_model_config(dict(x))
        if not frag.get("model"):
            raise ValueError(
                f"model_fallback_chain[{i}] must include 'model' or 'role'"
            )
        items.append({**base, **frag})

    first_id = _normalize_model_id(items[0].get("model"))
    if first_id != primary_id:
        full: list[dict[str, Any]] = [dict(base)]
        full.extend(items)
    else:
        full = list(items)

    if len(full) == 1:
        return full
    kept = [full[0]] + full[1 : 1 + max_fb]
    return kept


def build_model_only_routes(
    *,
    primary_mc: Mapping[str, Any],
    merged: Mapping[str, Any],
) -> list[tuple[str, None]]:
    """``create_react_agent`` 等无 provider/tool 双策略时：仅按模型链换路，每路由 ``(model_id, None)``。"""
    mcs = build_route_model_configs_list(primary_mc, merged)
    return [( _normalize_model_id(mc["model"]), None) for mc in mcs]


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
    primary_mc: Mapping[str, Any],
    merged: Mapping[str, Any],
) -> list[tuple[str, str]]:
    """
    结构化：``primary`` 与每个 fallback 模型各一路由，**策略相同**（由 ``resolve_structured_output_strategy`` 决定）。
    """
    strat = resolve_structured_output_strategy(merged)
    mcs = build_route_model_configs_list(primary_mc, merged)
    return [( _normalize_model_id(mc["model"]), strat) for mc in mcs]


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
) -> tuple[ResilienceContext, list[tuple[str, str | None]], list[dict[str, Any]]]:
    """
    从单条 ``PROMPTS_CONFIG`` 风格条目构建 ``(ResilienceContext, routes, route_model_configs)``。

    - **与 model_config 的关系**：主配置为 ``entry["model_config"]``；``model_fallback_chain`` 为 **list[dict]**，
      每项与主配置合并后得到该跳完整 ``model_config``（见 ``build_route_model_configs_list``）。
    - ``structured_schema``：覆盖 ``entry["schema"]``（条目里为 None 时由调用方传入）。
    - 无有效 schema 时视为纯 chat：路由 ``(model, None)`` 与 ``route_model_configs`` 对齐。
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
    route_mcs = build_route_model_configs_list(mc, merged)
    if not has_schema:
        if not primary:
            raise ValueError("prompt entry missing model_config.model (or role)")
        routes = [( _normalize_model_id(rmc["model"]), None) for rmc in route_mcs]
        return ctx, routes, route_mcs
    if not primary:
        raise ValueError("prompt entry missing model_config.model (or role)")
    if model_only_routes:
        routes = [( _normalize_model_id(rmc["model"]), None) for rmc in route_mcs]
    else:
        strat = resolve_structured_output_strategy(merged)
        routes = [( _normalize_model_id(rmc["model"]), strat) for rmc in route_mcs]
    return ctx, routes, route_mcs


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


def _extract_semantic_result(
    out: Any,
    *,
    kind: StructuredResilienceKind,
    include_raw: bool,
) -> Any:
    """从三种 kind 的返回包装里取出「语义结果」（pydantic 实例或 None），供空判定 / result_validator 使用。

    - ``CREATE_AGENT`` / ``CREATE_REACT_AGENT``：dict，取 ``structured_response``；
    - ``STRUCTURED_CHAT_MESSAGES`` + ``include_raw=True``：dict，取 ``parsed``；
    - ``STRUCTURED_CHAT_MESSAGES`` + ``include_raw=False``：直接是 pydantic 实例或 None。
    """
    if kind in (
        StructuredResilienceKind.CREATE_AGENT,
        StructuredResilienceKind.CREATE_REACT_AGENT,
    ):
        if isinstance(out, dict):
            return out.get("structured_response")
        return out
    if kind == StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES:
        if include_raw and isinstance(out, dict):
            return out.get("parsed")
        return out
    return out


def _wrap_invoke_with_result_check(
    invoke_fn: Callable[[tuple[str, str | None], Mapping[str, Any]], Awaitable[T]],
    *,
    kind: StructuredResilienceKind,
    include_raw: bool,
    result_validator: Callable[[Any], str | None] | None,
    builtin_empty_check: bool,
    log_context: Mapping[str, Any] | None,
) -> Callable[[tuple[str, str | None], Mapping[str, Any]], Awaitable[T]]:
    """在原始 ``invoke_fn`` 外包一层「成功后空判定」：三层串行检查，任一命中即抛 ``EmptyStructuredResponseError``。

    检查顺序（由低到高加严，越后的越业务化）：
      1) ``builtin_empty_check`` and ``sem is None``：调用根本没拿到结构化结果；
      2) ``builtin_empty_check`` and ``schema_instance.resilience_empty_reason()``：schema 自带钩子声明的语义空；
      3) ``result_validator(sem)``：调用方在 prompt 级别再加严的判定。

    抛出的 ``EmptyStructuredResponseError`` 由 ``classify_llm_exception`` 归 ``EMPTY_RESPONSE`` 桶，
    复用 ``execute_with_resilience`` 的 ``same_route_extra_retries`` + ``model_fallback_chain``。
    """
    _lc = _log_ctx_suffix(log_context)

    async def wrapped(
        route: tuple[str, str | None], mc: Mapping[str, Any]
    ) -> T:
        out = await invoke_fn(route, mc)
        sem = _extract_semantic_result(out, kind=kind, include_raw=include_raw)
        if builtin_empty_check and sem is None:
            logger.warning(
                "llm_resilience empty_check builtin_none route=%s kind=%s%s",
                route,
                kind.value,
                _lc,
            )
            raise EmptyStructuredResponseError("structured_response is None", raw=out)
        if builtin_empty_check and sem is not None:
            hook = getattr(sem, "resilience_empty_reason", None)
            if callable(hook):
                try:
                    reason = hook()
                except Exception as he:  # 钩子异常不应吞掉真正的成功返回，仅记日志走过去
                    logger.warning(
                        "llm_resilience empty_check schema_hook_raised route=%s kind=%s exc=%s%s",
                        route,
                        kind.value,
                        type(he).__name__,
                        _lc,
                    )
                    reason = None
                if reason:
                    logger.warning(
                        "llm_resilience empty_check schema_hook route=%s kind=%s reason=%s%s",
                        route,
                        kind.value,
                        str(reason)[:200],
                        _lc,
                    )
                    raise EmptyStructuredResponseError(str(reason), raw=out)
        if result_validator is not None and sem is not None:
            try:
                reason = result_validator(sem)
            except Exception as ve:
                logger.warning(
                    "llm_resilience empty_check result_validator_raised route=%s kind=%s exc=%s%s",
                    route,
                    kind.value,
                    type(ve).__name__,
                    _lc,
                )
                reason = None
            if reason:
                logger.warning(
                    "llm_resilience empty_check result_validator route=%s kind=%s reason=%s%s",
                    route,
                    kind.value,
                    str(reason)[:200],
                    _lc,
                )
                raise EmptyStructuredResponseError(str(reason), raw=out)
        return out

    return wrapped


async def execute_with_resilience(
    invoke_fn: Callable[[tuple[str, str | None], Mapping[str, Any]], Awaitable[T]],
    *,
    routes: Sequence[tuple[str, str | None]],
    route_model_configs: Sequence[Mapping[str, Any]],
    context: ResilienceContext,
    log_context: Mapping[str, Any] | None = None,
) -> T:
    """
    按线性路由依次尝试；每条路由上对「当前异常桶」允许的同路由重试耗尽后进入下一路由。

    - ``AUTH_CONFIG``：不重试、不换路由，直接抛出（设计稿 §4.1）。

    ``route`` 元组语义：``(model_id, strategy_or_None)``；``route_model_configs[i]`` 为对应跳的完整
    ``model_config``。``invoke_fn(route, mc)`` 必须用 ``create_llm_from_model_config(dict(mc))`` 建 LLM。

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
    rmc_list = list(route_model_configs)
    if len(route_list) != len(rmc_list):
        raise ValueError(
            f"execute_with_resilience: routes ({len(route_list)}) and route_model_configs ({len(rmc_list)}) length mismatch"
        )
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
    for route_i, route in enumerate(route_list):
        route_index = route_i + 1
        failures_on_route = 0
        attempt_on_route = 0
        route_mc = rmc_list[route_i]
        while True:
            attempt_on_route += 1
            logger.info(
                "llm_resilience invoke_start route_index=%s/%s attempt_on_route=%s route=%s%s",
                route_index,
                len(route_list),
                attempt_on_route,
                route,
                _lc,
            )
            try:
                out = await invoke_fn(route, route_mc)
                logger.info(
                    "llm_resilience success route_index=%s/%s attempt_on_route=%s model=%s strat=%s%s",
                    route_index,
                    len(route_list),
                    attempt_on_route,
                    route[0],
                    route[1],
                    _lc,
                )
                return out
            except BaseException as e:
                last_exc = e
                bucket = classify_llm_exception(e)
                exc_detail = _detail_for_llm_exception(e)
                if bucket == ErrorBucket.AUTH_CONFIG:
                    logger.warning(
                        "llm_resilience auth/config abort next_action=raise_auth route=%s bucket=%s attempt_on_route=%s exc_detail=%s%s",
                        route,
                        bucket,
                        attempt_on_route,
                        exc_detail,
                        _lc,
                    )
                    raise
                extra = effective_same_route_retries(merged_proxy, bucket)
                if failures_on_route < extra:
                    failures_on_route += 1
                    logger.warning(
                        "llm_resilience same_route_retry next_action=same_route_retry route=%s bucket=%s "
                        "failures_after_this=%s max_extra=%s attempt_on_route=%s exc_detail=%s%s",
                        route,
                        bucket,
                        failures_on_route,
                        extra,
                        attempt_on_route,
                        exc_detail,
                        _lc,
                    )
                    continue
                next_action = "next_route" if route_index < len(route_list) else "raise_final"
                logger.warning(
                    "llm_resilience leaving_route next_action=%s route=%s bucket=%s "
                    "exhausted_same_route_retries=%s attempt_on_route=%s exc_detail=%s%s",
                    next_action,
                    route,
                    bucket,
                    failures_on_route,
                    attempt_on_route,
                    exc_detail,
                    _lc,
                )
                break
    if last_exc is not None:
        logger.error(
            "llm_resilience all routes failed next_action=raise_final exc_detail=%s%s",
            _detail_for_llm_exception(last_exc),
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
    result_validator: Callable[[Any], str | None] | None = None,
    builtin_empty_check: bool = True,
) -> Any:
    """
    结构化调用的统一入口：``create_agent`` / ``create_react_agent`` / 已拼好的 ``structured_chat_messages``。

    - ``CREATE_AGENT``：``agent_inputs``；可选 ``agent_tools``、``context_schema``；``agent_invoke_context`` 传给 ``ainvoke(..., context=...)``；``wrap_agent_parse_fallback=True`` 时用 ``invoke_agent_with_parse_fallback``（Provider 解析失败兜底）。
    - ``CREATE_REACT_AGENT``：``langgraph.prebuilt.create_react_agent``；须 ``agent_tools``；路由仅换模型（无 provider/tool 双策略）。
    - ``STRUCTURED_CHAT_MESSAGES``：须 ``structured_chat_messages``。
    - ``structured_schema``：当 ``prompt_entry['schema']`` 为 None 时必须传入（避免 prompt_config 循环依赖）。
    - ``structured_output=False``：仅 ``CREATE_AGENT``；``create_agent`` 不绑定 ``response_format``（流式子 Agent 等）；此时也不做空判定（无 schema 可判）。
    - ``llm_invoke_config``：传给 ``ainvoke(..., config=...)``（如 ``RunnableConfig(recursion_limit=...)``）。
    - ``builtin_empty_check``（默认 True）：成功后自动跑「``structured_response`` / ``parsed`` is None」+「``schema.resilience_empty_reason()``」两层空判定，
      命中 → 抛 ``EmptyStructuredResponseError`` 走 ``EMPTY_RESPONSE`` 桶（同路由重试 + 换 ``model_fallback_chain``）。
      绝大多数 schema 想要的"非空"语义建议直接在 schema 类上定义 ``resilience_empty_reason(self) -> str | None``，所有调用方自动生效。
    - ``result_validator``：调用方在 prompt 级别再加严的判定，签名 ``(sem) -> Optional[str]``，返回非空字符串=空原因；在 schema 钩子之后跑。

    实现上：先 ``build_resilience_bundle_from_prompt_entry`` 得到 ``(ctx, routes, route_model_configs)``，再为每个 ``kind`` 定义
    闭包 ``invoke_fn(route, mc)``，外面包一层 ``_wrap_invoke_with_result_check``（``structured_output=False`` 时跳过），最后 ``execute_with_resilience(..., route_model_configs=...)``。
    """
    if not structured_output and kind != StructuredResilienceKind.CREATE_AGENT:
        raise ValueError("structured_output=False is only supported for kind=CREATE_AGENT")
    schema: Type[BaseModel] | None
    if structured_output:
        schema = _resolve_structured_schema(prompt_entry, structured_schema)
    else:
        schema = None
    model_only = kind == StructuredResilienceKind.CREATE_REACT_AGENT
    ctx, routes, route_mcs = build_resilience_bundle_from_prompt_entry(
        prompt_entry,
        structured_schema=schema,
        model_only_routes=model_only,
    )
    from prompts.llm_model_profiles import resolve_model_config

    # route_mcs 已 resolve；mc_base 仅作日志 / 兼容旧合并路径
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
    _log_dev_full_llm_request(
        kind=kind,
        agent_inputs=agent_inputs,
        structured_chat_messages=structured_chat_messages,
        log_context=log_context,
    )
    _log_dev_llm_request_digest(
        kind=kind,
        agent_inputs=agent_inputs,
        structured_chat_messages=structured_chat_messages,
        log_context=log_context,
    )
    # 本地 /files 图：先读盘内联为 data URI，再调 LLM（避免 SDK 同步回拉 localhost 堵死事件循环）
    agent_inputs, structured_chat_messages = await _inline_local_images_in_llm_inputs(
        agent_inputs, structured_chat_messages
    )
    # 把 log_context（phase/shot_number 等）+ agent_invoke_context（audio_url/duration 等
    # runtime 参数）以 metadata/tags/run_name 注入 RunnableConfig，附到「本次 invoke 新建的
    # run」上，便于在 LangSmith 按镜头/阶段查看与筛选（不改当前父 run，避免并发互相覆盖）。
    _cfg = build_ls_run_config(
        log_context, agent_invoke_context, base_config=llm_invoke_config
    ) or llm_invoke_config

    if kind == StructuredResilienceKind.CREATE_REACT_AGENT:
        if agent_inputs is None:
            raise ValueError("kind=CREATE_REACT_AGENT requires agent_inputs")
        bound_react_inputs = agent_inputs
        from langgraph.prebuilt import create_react_agent

        async def invoke_react_fn(
            route: tuple[str, str | None], mc: Mapping[str, Any]
        ) -> Any:
            model_id, _strat = route
            llm = create_llm_from_model_config(dict(mc))
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
            _wrap_invoke_with_result_check(
                invoke_react_fn,
                kind=kind,
                include_raw=include_raw,
                result_validator=result_validator,
                builtin_empty_check=builtin_empty_check,
                log_context=log_context,
            ),
            routes=routes,
            route_model_configs=route_mcs,
            context=ctx,
            log_context=log_context,
        )

    if kind == StructuredResilienceKind.CREATE_AGENT:
        if agent_inputs is None:
            raise ValueError("kind=CREATE_AGENT requires agent_inputs")
        bound_agent_inputs = agent_inputs
        tools_list = list(agent_tools or [])

        # CREATE_AGENT + structured：response_format 用 ProviderStrategy 或 ToolStrategy；与外层换模型无关，
        # 每条 route 的 strat 相同（均来自 resolve_structured_output_strategy）。
        async def invoke_fn(
            route: tuple[str, str | None], mc: Mapping[str, Any]
        ) -> Any:
            model_id, strat = route
            llm = create_llm_from_model_config(dict(mc))
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
                from app.services.agent.utils.prompt_utils import (
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

        # structured_output=False 时没有 schema 可判，跳过空判定；结构化路径正常包一层。
        _invoke_for_exec = (
            invoke_fn
            if not structured_output
            else _wrap_invoke_with_result_check(
                invoke_fn,
                kind=kind,
                include_raw=include_raw,
                result_validator=result_validator,
                builtin_empty_check=builtin_empty_check,
                log_context=log_context,
            )
        )
        return await execute_with_resilience(
            _invoke_for_exec,
            routes=routes,
            route_model_configs=route_mcs,
            context=ctx,
            log_context=log_context,
        )

    if kind == StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES:
        if structured_chat_messages is None:
            raise ValueError("kind=STRUCTURED_CHAT_MESSAGES requires structured_chat_messages")
        bound_msgs = list(structured_chat_messages)

        # provider：直接 llm.with_structured_output；tool：无业务 tools 的 create_agent + ToolStrategy（与 CREATE_AGENT 一致策略语义）。
        async def invoke_fn_msgs(
            route: tuple[str, str | None], mc: Mapping[str, Any]
        ) -> Any:
            model_id, strat = route
            llm = create_llm_from_model_config(dict(mc))
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
            _wrap_invoke_with_result_check(
                invoke_fn_msgs,
                kind=kind,
                include_raw=include_raw,
                result_validator=result_validator,
                builtin_empty_check=builtin_empty_check,
                log_context=log_context,
            ),
            routes=routes,
            route_model_configs=route_mcs,
            context=ctx,
            log_context=log_context,
        )

    raise ValueError(f"unknown StructuredResilienceKind: {kind!r}")
