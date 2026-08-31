"""Chat 编排：与 VideoAgent delegate 分界（见 docs/EDIT_AGENT_UNIFIED_PLAN.md）。"""
from __future__ import annotations

from typing import Optional


def delegated_va_run_id_reducer(current: Optional[str], update: Optional[str]) -> Optional[str]:
    """checkpoint 合并：显式写入优先，否则保留已有 delegated_va_run_id。"""
    return update if update is not None else current


def should_bind_edit_mcp_tool(delegated_va_run_id: Optional[str]) -> bool:
    """已对 VA 成功 delegate 并持有 pipeline run_id 时，可挂载 Edit（整颗 Companion）单 tool。"""
    return bool(delegated_va_run_id)
