"""统一生成失败原因分类器。

把底层（厂商 API / LLM / 工具）的原始报错归类到有限的「官方类别」，
对外只暴露用户能看懂、但不泄露内部信息（厂商名、模型名、堆栈、状态码细节）的原因文案。

用法：
    cat = classify_failure(raw_error_msg)
    reason = await user_facing_reason_async(raw_error_msg, lang="zh")

raw_error_msg 仍只用于内部日志/调试落库，绝不直接返回前端。
"""
from enum import Enum
from typing import Optional

from app.utils.i18n import get_i18n_message, get_i18n_message_async


class FailureCategory(str, Enum):
    """对外暴露的官方失败类别（粒度刻意保持很粗，避免泄露内部细节）。"""

    RATE_LIMITED = "rate_limited"            # 当前请求量大 / 触发限流
    CONTENT_MODERATION = "content_moderation"  # 内容审核未通过 / 触发安全策略
    QUOTA_EXCEEDED = "quota_exceeded"        # 额度 / 配额不足
    SERVICE_UNAVAILABLE = "service_unavailable"  # 服务暂时不可用 / 超时
    INVALID_INPUT = "invalid_input"          # 输入 / 提示词不被支持
    UNKNOWN = "unknown"                      # 其它未知原因


# 关键词表（全部小写匹配）。顺序在 classify_failure 内决定优先级。
_MODERATION_KEYWORDS = [
    "moderation", "content policy", "content_policy", "safety", "safety system",
    "sensitive", "violat", "policy violation", "nsfw", "prohibited", "not allowed",
    "blocked by", "flagged", "审核", "敏感", "违规", "违禁", "不合规", "拦截",
]
# 厂商/平台侧账户余额（非用户积分）→ 对外统一为服务繁忙，避免用户误以为自己的积分耗尽
_PROVIDER_BALANCE_KEYWORDS = [
    "账户余额", "请充值", "account balance", "insufficient balance",
    "insufficient credit", "out of credit", "no credit", "billing account", "欠费",
    "top up your account", "please top up",
]
_QUOTA_KEYWORDS = [
    "insufficient quota", "quota exceeded", "exceeded your quota",
    "402", "额度", "配额", "积分不足",
]
_RATE_LIMIT_KEYWORDS = [
    "rate limit", "rate_limit", "too many requests", "429", "throttl",
    "限流", "请求过于频繁", "频繁", "使用量",
]
_SERVICE_KEYWORDS = [
    "500", "502", "503", "504", "deadline_exceeded", "timeout", "timed out",
    "capacity", "temporarily unavailable", "service unavailable", "overloaded",
    "connection", "unavailable", "gateway", "暂时不可用", "超时", "繁忙",
]
_INVALID_INPUT_KEYWORDS = [
    "invalid", "bad request", "400", "422", "unsupported", "not supported",
    "unprocessable", "validation", "参数", "不支持", "格式错误",
]
# 媒体入参远端拉不到（本地/私网 URL、下载失败）→ 属于输入/配置问题，绝不能被 "not allowed" 误判成内容审核。
# 典型：WaveSpeed "Invalid image URL: private or local network URLs are not allowed"（本地存储未做媒体出口）。
_MEDIA_URL_KEYWORDS = [
    "private or local network", "local network url", "invalid image url",
    "invalid video url", "invalid audio url", "failed to download",
    "could not download", "unable to fetch", "url is not accessible",
    "无法访问", "无法下载",
]


def classify_failure(raw_error: Optional[str]) -> FailureCategory:
    """根据原始错误信息归类。

    优先级：媒体URL不可达 > 内容审核 > 额度 > 限流 > 服务不可用 > 输入无效 > 未知。
    （媒体URL不可达最优先，避免其中的「not allowed」被内容审核误吞；审核/额度次之，避免被「unavailable」等泛词误判为限流类。）
    """
    if not raw_error:
        return FailureCategory.UNKNOWN
    text = raw_error.lower()

    def _hit(keywords: list) -> bool:
        return any(kw in text for kw in keywords)

    if _hit(_MEDIA_URL_KEYWORDS):
        return FailureCategory.INVALID_INPUT
    if _hit(_MODERATION_KEYWORDS):
        return FailureCategory.CONTENT_MODERATION
    if _hit(_PROVIDER_BALANCE_KEYWORDS):
        return FailureCategory.SERVICE_UNAVAILABLE
    if _hit(_QUOTA_KEYWORDS):
        return FailureCategory.QUOTA_EXCEEDED
    if _hit(_RATE_LIMIT_KEYWORDS):
        return FailureCategory.RATE_LIMITED
    if _hit(_SERVICE_KEYWORDS):
        return FailureCategory.SERVICE_UNAVAILABLE
    if _hit(_INVALID_INPUT_KEYWORDS):
        return FailureCategory.INVALID_INPUT
    return FailureCategory.UNKNOWN


def category_i18n_key(category: FailureCategory) -> str:
    """该类别对应的用户文案 i18n key。"""
    return f"generation_failed.reason.{category.value}"


def user_facing_reason(
    raw_error: Optional[str] = None,
    *,
    category: Optional[FailureCategory] = None,
    lang: Optional[str] = None,
) -> str:
    """同步返回用户可见的官方原因文案（不含任何内部信息）。

    传 category 则直接用之；否则用 raw_error 分类。
    """
    cat = category or classify_failure(raw_error)
    return get_i18n_message(category_i18n_key(cat), default=_default_reason(cat), lang=lang)


async def user_facing_reason_async(
    raw_error: Optional[str] = None,
    *,
    category: Optional[FailureCategory] = None,
    lang: Optional[str] = None,
) -> str:
    """异步版本，见 user_facing_reason。"""
    cat = category or classify_failure(raw_error)
    return await get_i18n_message_async(
        category_i18n_key(cat), default=_default_reason(cat), lang=lang
    )


# 命中即判定「可能泄露内部信息」的黑名单（全部小写匹配）。
# 用于给 tool LLM 生成的 error_msg 兜底：命中任意一项就回退到官方类别文案，绝不把厂商/模型/技术细节透传给用户。
_LEAK_KEYWORDS = [
    # 厂商 / 平台 / 模型名
    "pollo", "wavespeed", "seedance", "seedream", "nano banana", "nano_banana",
    "kling", "sora", "openai", "gpt", "gemini", "flux", "wan2", "ltx", "suno",
    "minimax", "midjourney", "runway", "luma", "jimeng", "即梦", "可灵",
    # 技术细节 / 协议 / 代码
    "http", "api", "sdk", "traceback", "exception", "status code", "json",
    "endpoint", "request id", "url", "uuid", "504", "503", "502", "500",
    "429", "401", "403", "404", "422", "400",
    # 中文技术词
    "异常", "堆栈", "接口", "状态码", "错误码", "调用失败", "报错",
    # 厂商账户余额（非用户积分）
    "余额", "充值", "账户", "欠费", "额度不足",
    "insufficient credit", "top up your account", "please top up",
]


def sanitize_user_facing_reason(
    text: Optional[str],
    *,
    fallback: str,
    max_len: int = 80,
) -> str:
    """对 tool LLM 生成的 error_msg 做后置防泄密过滤（llm + guard 策略）。

    - text 为空 / 命中泄露黑名单 / 过长（大概率夹带原始报错）→ 返回 fallback（官方类别文案）；
    - 否则返回去掉首部状态符号后的文本。
    fallback 一般传 user_facing_reason(category=...) 的类别兜底文案。
    """
    if not text or not text.strip():
        return fallback
    cleaned = text.strip().lstrip("❌✅⚠️ ").strip()
    if not cleaned:
        return fallback
    low = cleaned.lower()
    if any(kw in low for kw in _LEAK_KEYWORDS):
        return fallback
    if len(cleaned) > max_len:
        return fallback
    return cleaned


def _default_reason(category: FailureCategory) -> str:
    """i18n 兜底文案（中文），仅当 locale 缺失时使用。"""
    return {
        FailureCategory.RATE_LIMITED: "当前生成需求较多，请稍后重试。",
        FailureCategory.CONTENT_MODERATION: "内容未通过安全审核，请调整描述后重试。",
        FailureCategory.QUOTA_EXCEEDED: "当前生成资源紧张，请稍后重试。",
        FailureCategory.SERVICE_UNAVAILABLE: "生成服务暂时繁忙，请稍后重试。",
        FailureCategory.INVALID_INPUT: "当前内容暂不被支持，请调整描述后重试。",
        FailureCategory.UNKNOWN: "生成失败，请稍后重试。",
    }.get(category, "生成失败，请稍后重试。")
