"""章节 order 字段约定：DB / 内部逻辑统一 0-based（第一章 order=0）；UI 显示为 order + 1。"""
from __future__ import annotations

from typing import Any, List, Sequence, TypeVar

T = TypeVar("T")


def _chapter_order_value(chapter: Any) -> int:
    if isinstance(chapter, dict):
        return int(chapter.get("order", 0) or 0)
    return int(getattr(chapter, "order", 0) or 0)


def sort_chapters_by_order(chapters: Sequence[T]) -> List[T]:
    """按 order 升序返回新列表（不修改原列表）。"""
    return sorted(chapters, key=_chapter_order_value)


def reindex_chapter_orders_inplace(chapters: List[Any]) -> None:
    """按当前 order 排序后，原地赋值为连续 0-based order（0, 1, 2, …）。"""
    if not chapters:
        return
    chapters.sort(key=_chapter_order_value)
    for i, chapter in enumerate(chapters):
        if isinstance(chapter, dict):
            chapter["order"] = i
        else:
            chapter.order = i


def chapter_display_number(order: int | None, fallback_index: int = 0) -> int:
    """用户可见章节序号（第一章 = 1）。"""
    base = order if order is not None else fallback_index
    return int(base) + 1
