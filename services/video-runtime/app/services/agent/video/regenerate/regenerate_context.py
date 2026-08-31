"""Regenerate 流水线内的临时覆盖（不写 DB 的 selected/current），供 post-regenerate 下游等场景使用。"""
from __future__ import annotations

import contextvars
from typing import Dict, Optional

# character_uuid -> character_version_uuid：与面板 interaction 里 new_version 对齐，仅影响当次取图
forced_character_version_uuids_cv: contextvars.ContextVar[Optional[Dict[str, str]]] = contextvars.ContextVar(
    "forced_character_version_uuids_cv", default=None
)
