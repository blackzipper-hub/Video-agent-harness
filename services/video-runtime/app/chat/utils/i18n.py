"""
语言上下文（与 Cuti-VideoAgent 一致）：按请求设置当前语言，供后续逻辑使用。
中间件从 X-App-Language / X-Language 读取并 set_current_language；业务侧可 get_current_language()。

迁移自 VideoAgent 的 ``get_i18n_message`` / ``get_i18n_message_async``：本仓库暂无完整 locales 目录时，
回退为 ``default`` 或 key，保证 ``models.user_options.get_tool_capabilities`` 等可导入。
"""
import asyncio
from contextvars import ContextVar
from typing import Any, Dict, Optional

language_context: ContextVar[str] = ContextVar("language", default="en")


def get_current_language() -> str:
    """获取当前请求的语言（ISO 639-1）。"""
    return language_context.get()


def set_current_language(language_code: str) -> None:
    """设置当前请求的语言。"""
    language_context.set((language_code or "en").strip().lower() or "en")


def get_i18n_message(
    key: str,
    default: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    lang: Optional[str] = None,
) -> str:
    """与 VideoAgent 签名兼容；无翻译表时返回 default 或 key。"""
    msg = default if default is not None else key
    if params and isinstance(params, dict):
        try:
            return msg.format(**params)
        except (KeyError, ValueError):
            return msg
    return msg


async def get_i18n_message_async(
    key: str,
    default: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    lang: Optional[str] = None,
) -> str:
    """异步版本：与 VideoAgent 一致，供 user_options 等 async 路径使用。"""
    return await asyncio.to_thread(get_i18n_message, key, default=default, params=params, lang=lang)
