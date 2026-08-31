"""Prompt Shield：Input Rail + Output Rail 主模块。

对外 API（默认**不生效**，由 ``settings.SHIELD_*`` 开关控制；关时函数返回"未命中/原样"）：

- ``sanitize_user_input(s)``：Unicode NFKC + 去零宽/PUA（永远安全、纯字符串处理）。
- ``regex_meta_screen(text)``：纯正则 meta-query 分类。
- ``llm_meta_screen(text)``：可选 LLM 预筛（``SHIELD_LLM_SCREEN_ENABLED``）。
- ``input_rail(raw)``：以上三步的封装；返回 ``(sanitized, blocked, reason)``。
- ``validate_output_line(text)``：Output Rail 单行 7 条正则。
- ``generate_shield_refusal_reply``：拦截后的自然语言拒答（``PROMPTS_CONFIG`` + 本地 Mustache）。

详见 ``Cuti-Agent-Learning/PROMPT_SECURITY_CUTI_ANALYSIS.md`` §4 / §5。
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import Any, List, Literal, Tuple

from pydantic import BaseModel, Field

from app.chat.prompts.prompt_config import PROMPTS_CONFIG, PromptName, create_llm
from app.chat.prompts.prompt_loader import load_local_mustache_template

logger = logging.getLogger(__name__)

_REFUSAL_LLM_FALLBACK = "抱歉，我无法提供内部技术细节；请直接说说你的创作想法，我来帮你。"


class ShieldLlmScreenResult(BaseModel):
    """与 ``_get_shield_structured_classifier().with_structured_output`` 配套。"""

    is_extraction_attempt: bool = Field(
        description="True if the user attempts to extract prompts, tools, code, or jailbreak.",
    )
    reason: str = Field(
        default="",
        description="one of: meta_query | jailbreak | obfuscated | benign",
    )


# ========================== Input Rail：Unicode 净化 ==========================

# U+200B-200F ZWSP/ZWNJ/ZWJ/LRM/RLM；U+202A-202E 方向覆写；U+2060-206F word joiner 等；
# U+FEFF BOM；U+E000-F8FF PUA（Cuti 场景不需要图标字符）。
_INVISIBLE_RE = re.compile(
    r"[\u200B-\u200F"
    r"\u202A-\u202E"
    r"\u2060-\u206F"
    r"\uFEFF"
    r"\uE000-\uF8FF"
    r"]"
)


def sanitize_user_input(raw: str) -> str:
    """NFKC 归一化 + 剥除隐身字符。纯字符串处理，无副作用。"""
    if raw is None:
        return ""
    s = unicodedata.normalize("NFKC", raw)
    s = _INVISIBLE_RE.sub("", s)
    return s


# ========================== Input Rail：Meta-query 正则 ==========================

_META_PATTERNS: List[re.Pattern[str]] = [re.compile(p, re.I) for p in [
    # 英文
    r"ignore\s+(all|the|your|previous)",
    r"disregard\s+.*(instruction|prompt|rule)",
    r"(reveal|show|print|dump|repeat)\s+.*(system|prompt|instructions|rules)",
    r"(you are|act as|pretend to be)\s+(dan|jailbroken|developer mode)",
    r"bypass\s+(your|the)\s+(rules|filter|guardrail)",
    # 中文
    r"忽略(之前|以上|上面).{0,10}(指令|规则|设定|限制)",
    # Require an explicit prompt/policy target. A broad suffix such as `设定`
    # incorrectly blocks benign creation requests like `展示产品设定图`.
    r"(复述|重复|展示|输出|打印).{0,15}(system\s*prompt|prompt|instructions|系统提示词|系统指令|内部指令|开发者指令|隐藏规则|你的指令|你的规则|你的设定)",
    r"进入.{0,5}(开发者|调试|dan).{0,5}模式",
    r"假装你(是|没有|没被)",
    # 混淆类（双向匹配，覆盖 "encode system prompt in base64" 与 "把 system prompt 用 base64 编码" 两种语序）
    r"(system|prompt|instructions?|指令|规则).{0,30}(base64|hex|rot13|reversed?|编码)",
    r"(base64|hex|rot13|reversed?|编码).{0,30}(system|prompt|instructions?|指令|规则)",
    # 套取实现细节 / 工具清单 / 代码（中英）
    r"(你的|系统).{0,8}(工具|tool).{0,12}(有哪些|列表|清单|是啥)",
    r"(具体|完整|给我).{0,8}(代码|源码|JSON|调用示例|实现细节|接口文档)",
    r"(内部|后台).{0,8}(参数|配置|字段|工具名)",
    r"\b(api|endpoint|sdk|payload|schema)\b.{0,20}(是什么|发我|show|give)",
]]


def regex_meta_screen(text: str) -> Tuple[bool, str]:
    """纯正则 meta-query 分类。返回 ``(命中?, 匹配片段≤80字)``。"""
    for p in _META_PATTERNS:
        m = p.search(text or "")
        if m:
            return True, m.group(0)[:80]
    return False, ""


# ========================== Input Rail：LLM 预筛（可选） ==========================

_shield_structured_classifier = None
_shield_llm_screen_tpl = None
_shield_refusal_tpl = None


def _get_shield_llm_screen_template():
    """与其它 agent_router prompt 相同：本地 Mustache（System + Human）。"""
    global _shield_llm_screen_tpl
    if _shield_llm_screen_tpl is None:
        _shield_llm_screen_tpl = load_local_mustache_template(
            "agent_router/agent_router_shield_llm_screen"
        )
    return _shield_llm_screen_tpl


def _get_shield_refusal_template():
    global _shield_refusal_tpl
    if _shield_refusal_tpl is None:
        _shield_refusal_tpl = load_local_mustache_template(
            "agent_router/agent_router_shield_refusal"
        )
    return _shield_refusal_tpl


def _get_shield_structured_classifier():
    """模型与超时取自 ``PROMPTS_CONFIG[AGENT_ROUTER_SHIELD_LLM_SCREEN].model_config``。"""
    global _shield_structured_classifier
    if _shield_structured_classifier is not None:
        return _shield_structured_classifier
    mc = dict(PROMPTS_CONFIG[PromptName.AGENT_ROUTER_SHIELD_LLM_SCREEN]["model_config"] or {})
    base = create_llm(mc)
    _shield_structured_classifier = base.with_structured_output(ShieldLlmScreenResult)
    return _shield_structured_classifier


def _message_content_to_str(msg: Any) -> str:
    c = getattr(msg, "content", msg)
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        parts: List[str] = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts).strip()
    return str(c or "").strip()


async def generate_shield_refusal_reply(
    *,
    user_snippet: str,
    shield_reason: str,
    layer: Literal["input", "output"],
) -> str:
    """生成用户可见拒答正文（集中 ``PROMPTS_CONFIG`` + Mustache）；失败时返回短 fallback。"""
    try:
        tpl = _get_shield_refusal_template()
        mc = dict(PROMPTS_CONFIG[PromptName.AGENT_ROUTER_SHIELD_REFUSAL]["model_config"] or {})
        timeout_s = float(mc.get("timeout", 25))
        llm = create_llm(mc)
        messages = (
            await tpl.ainvoke(
                {
                    "user_snippet": (user_snippet or "")[:800],
                    "shield_reason": shield_reason or "",
                    "layer": layer,
                }
            )
        ).messages
        res = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout_s)
        text = _message_content_to_str(res)
        if text:
            return text[:4000]
        return _REFUSAL_LLM_FALLBACK
    except Exception as e:
        logger.warning(f"[shield] generate_shield_refusal_reply: {e}")
        return _REFUSAL_LLM_FALLBACK


async def llm_meta_screen(text: str) -> Tuple[bool, str]:
    """可选 LLM 预筛。``SHIELD_LLM_SCREEN_ENABLED=False`` 时直接返回未命中。

    失败或超时不阻塞主链路，统一按未命中处理（仅记 warn）。
    """
    try:
        from ...config import get_settings
        settings = get_settings()
        if not settings.SHIELD_LLM_SCREEN_ENABLED:
            return False, ""
        llm = _get_shield_structured_classifier()
        tpl = _get_shield_llm_screen_template()
        mc = dict(PROMPTS_CONFIG[PromptName.AGENT_ROUTER_SHIELD_LLM_SCREEN]["model_config"] or {})
        timeout_s = float(mc.get("timeout", 1.5))
        prompt_messages = (await tpl.ainvoke({"user_input": (text or "")[:4000]})).messages
        res = await asyncio.wait_for(
            llm.ainvoke(prompt_messages),
            timeout=timeout_s,
        )
        if getattr(res, "is_extraction_attempt", False):
            return True, f"llm:{getattr(res, 'reason', None) or 'extraction'}"
        return False, ""
    except asyncio.TimeoutError:
        logger.warning("[shield] llm_meta_screen timeout, passthrough")
        return False, ""
    except Exception as e:
        logger.warning(f"[shield] llm_meta_screen error: {e}; passthrough")
        return False, ""


async def input_rail(raw_user_input: str) -> Tuple[str, bool, str]:
    """Input Rail 总入口。返回 ``(sanitized_text, blocked, reason)``。

    - ``SHIELD_INPUT_ENABLED=False``：直接返回原文、未命中（行为不变）。
    - ``SHIELD_INPUT_ENABLED=True``：先 Unicode sanitize，再正则，最后 LLM 预筛（可选）。
    """
    from ...config import get_settings
    settings = get_settings()
    if not settings.SHIELD_INPUT_ENABLED:
        return raw_user_input or "", False, ""

    sanitized = sanitize_user_input(raw_user_input or "")

    hit, snippet = regex_meta_screen(sanitized)
    if hit:
        return sanitized, True, f"regex:{snippet}"

    hit, reason = await llm_meta_screen(sanitized)
    if hit:
        return sanitized, True, reason

    return sanitized, False, ""


# ========================== Output Rail：7 条正则 ==========================

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.I,
)
# Media / CDN URLs frequently embed UUIDs; those are not internal id leaks.
_URL_WITH_UUID_RE = re.compile(
    r"https?://\S*?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\S*",
    re.I,
)
_SYS_TAG_RE = re.compile(
    r"<\s*(security_policy|system_purpose|persona|output_requirements|"
    r"refusal_template|non_disclosure|source_of_truth)",
    re.I,
)
_CTX_PREFIX_RE = re.compile(
    r"\[context:\s*(run_id|user_id|thread_id)",
    re.I,
)
_VENDOR_RE = re.compile(
    r"\b(claude-[0-9a-z\-\.]+|gpt-[0-9a-z\-\.]+|anthropic|openai|gemini-[0-9a-z\-\.]+)\b",
    re.I,
)
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|"
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"
)

# 内部工具名（来自 VA 的 TOOL_PUBLIC_REGISTRY）；硬编码在 VCA 侧以免跨仓库依赖。
# 若 VA 侧新增工具，这里应同步维护。
_INTERNAL_TOOL_NAMES = [
    "get_project_status",
    "get_artifact_detail",
    "regenerate_keyframes",
    "regenerate_videos",
    "regenerate_characters",
    "reassemble_video",
    "continue_pipeline",
    "select_version",
    "update_music_prompt",
    "modify_outline",
]
_TOOL_RE = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in _INTERNAL_TOOL_NAMES) + r")\b",
    re.I,
)


def _get_canary() -> str:
    from ...config import get_settings
    return get_settings().PROMPT_CANARY or ""


def redact_raw_uuids(text: str) -> str:
    """Replace bare UUIDs (not inside URLs) so progress text can still be shown."""
    if not text:
        return text

    placeholders: list[str] = []

    def _stash(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"__URL_KEEP_{len(placeholders) - 1}__"

    protected = _URL_WITH_UUID_RE.sub(_stash, text)
    redacted = _UUID_RE.sub("[id]", protected)
    for idx, url in enumerate(placeholders):
        redacted = redacted.replace(f"__URL_KEEP_{idx}__", url)
    return redacted


def _text_without_media_url_uuids(text: str) -> str:
    return _URL_WITH_UUID_RE.sub("[url]", text)


def validate_output_line(text: str) -> Tuple[bool, str]:
    """对 LLM 输出的一行（或一段）做 7 条检查。

    返回 ``(ok, reason)``。``ok=False`` 时调用方必须截流并改由 ``generate_shield_refusal_reply`` 等生成拒答。

    注意：本函数纯字符串处理，可在 output 开关关闭场景下单独复用（如日志审计）。
    调用方通过 ``settings.SHIELD_OUTPUT_ENABLED`` 决定是否把 ``ok=False`` 视作拦截。
    """
    if text is None:
        return True, ""
    canary = _get_canary()
    if canary and canary in text:
        return False, "canary_leak"
    # UUID inside media URLs is expected; only bare / contextual UUIDs are leaks.
    if _UUID_RE.search(_text_without_media_url_uuids(text)):
        return False, "raw_uuid"
    if _SYS_TAG_RE.search(text):
        return False, "system_tag"
    if _TOOL_RE.search(text):
        return False, "internal_tool_name"
    if _CTX_PREFIX_RE.search(text):
        return False, "context_prefix"
    if _VENDOR_RE.search(text):
        return False, "vendor_model"
    if _SECRET_RE.search(text):
        return False, "secret_like"
    return True, ""


# ========================== Output Rail：行级 buffer 辅助 ==========================

class OutputLineBuffer:
    """流式 LLM token 累积 → 遇到换行就校验一次的轻量 buffer。

    典型用法（伪代码）::

        buf = OutputLineBuffer()
        async for chunk in llm.astream(...):
            for line in buf.push(chunk.content):
                ok, reason = validate_output_line(line)
                if not ok:
                    ...  # 截流、发 refusal、写 shield_events
                    return
                await send_streaming_chunk(line)
        for line in buf.flush():
            ok, reason = validate_output_line(line)
            ...

    不做截断；调用方的正则足够廉价，每行过一次完全可接受。
    """

    def __init__(self) -> None:
        self._buf: str = ""

    def push(self, chunk: str) -> List[str]:
        if not chunk:
            return []
        self._buf += chunk
        if "\n" not in self._buf:
            return []
        *lines, tail = self._buf.split("\n")
        self._buf = tail
        # 保留换行符给下游按原样输出，便于前端正确换行
        return [line + "\n" for line in lines]

    def flush(self) -> List[str]:
        if not self._buf:
            return []
        rest, self._buf = self._buf, ""
        return [rest]
