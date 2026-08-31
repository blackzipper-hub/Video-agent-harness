"""Prompt Shield 审计表 ``shield_events``（只读查询，供 Admin）。

表由 Cuti-VideoChatAgent 侧 ``init/init_shield_events.py`` 创建；与 VCA 模型列一致。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlmodel import SQLModel, Field, Column, JSON, Text


class ShieldEventDB(SQLModel, table=True):
    __tablename__ = "shield_events"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    thread_id: Optional[str] = Field(default=None, index=True)
    run_id: Optional[str] = Field(default=None, index=True)
    layer: str = Field(index=True, max_length=16)
    reason: str = Field(index=True, max_length=64)
    matched_snippet: Optional[str] = Field(default=None, sa_column=Column(Text))
    extra: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
