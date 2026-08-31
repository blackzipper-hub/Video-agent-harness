from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from app.chat.config import Settings

_LOGGER_NAME = "cuti.deep_agent_v2.trace"
_LOCK = Lock()
_configured_path: str | None = None
_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
}


def configure_agent_trace(settings: Settings) -> None:
    global _configured_path
    if not settings.DEEP_AGENT_V2_TRACE_ENABLED:
        return
    path = Path(settings.DEEP_AGENT_V2_TRACE_LOG_PATH).expanduser().resolve()
    with _LOCK:
        if _configured_path == str(path):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(_LOGGER_NAME)
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = logging.handlers.TimedRotatingFileHandler(
            filename=path,
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        _configured_path = str(path)


def trace_agent_event(
    settings: Settings,
    event: str,
    *,
    run_id: str | None = None,
    **payload: Any,
) -> None:
    if not settings.DEEP_AGENT_V2_TRACE_ENABLED:
        return
    configure_agent_trace(settings)
    document = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event,
        "run_id": run_id,
        **_safe(payload),
    }
    logging.getLogger(_LOGGER_NAME).info(
        json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    )


def _safe(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if key.lower() in _SENSITIVE_KEYS or any(
        marker in key.lower() for marker in ("password", "secret", "token", "api_key")
    ):
        return "[REDACTED]"
    if depth >= 8:
        return "[MAX_DEPTH]"
    if isinstance(value, dict):
        return {
            str(item_key): _safe(item, key=str(item_key), depth=depth + 1)
            for item_key, item in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [_safe(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return value if len(value) <= 12000 else f"{value[:12000]}…[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe(str(value), key=key, depth=depth + 1)
