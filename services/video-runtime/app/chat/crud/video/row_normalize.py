"""
CRUD 行规范化：将 DB 返回的 JSON/JSONB 列（可能为字符串）统一转为 list/dict，
供 msgspec.Struct 构造时使用。所有 JSON 处理集中在此，API/Service 层不再单独处理。

同时提供 row_to_struct_safe：先 normalize 再只保留 Struct 声明的字段再构造，
避免 DB 新增列后 **row 导致 Unexpected keyword argument。

Duration 迁移：set_effective_duration_* 用于读路径，将 COALESCE(xxx_sec, 旧列::float) 写入旧列键，
便于 schema 仍用 duration/total_duration 单字段。
"""
from typing import Optional, Dict, Any, Tuple, Type, TypeVar

from ...utils.asyncpg_utils import ensure_list, ensure_dict, filter_row_to_struct_keys

T = TypeVar("T")


def set_effective_duration(row: Optional[Dict], sec_key: str, old_key: str) -> None:
    """将 row 中的时长统一为「新列优先、旧列兜底」的 float，写回 row[old_key]（读路径用）。"""
    if not row:
        return
    sec_val = row.get(sec_key)
    old_val = row.get(old_key)
    if sec_val is not None:
        try:
            row[old_key] = float(sec_val)
        except (TypeError, ValueError):
            row[old_key] = float(old_val) if old_val is not None else None
    elif old_val is not None:
        try:
            row[old_key] = float(old_val)
        except (TypeError, ValueError):
            row[old_key] = None
    else:
        row[old_key] = None


def normalize_row(
    row: Optional[Dict],
    list_fields: Tuple[str, ...] = (),
    dict_fields: Tuple[str, ...] = (),
    list_default_empty: bool = False,
) -> Optional[Dict]:
    """复制 row 并规范化指定字段：list_fields 转为 list，dict_fields 转为 dict。
    list_default_empty=True 时，list 字段为 None 或缺失时设为 []。
    """
    if not row:
        return None
    out = dict(row)
    for f in list_fields:
        if f in out:
            out[f] = ensure_list(out[f])
            if list_default_empty and out[f] is None:
                out[f] = []
        elif list_default_empty:
            out[f] = []
    for f in dict_fields:
        if f in out:
            out[f] = ensure_dict(out[f])
    return out


def row_to_struct_safe(
    row: Optional[Dict],
    struct_cls: Type[T],
    list_fields: Tuple[str, ...] = (),
    dict_fields: Tuple[str, ...] = (),
    list_default_empty: bool = False,
) -> Optional[T]:
    """Normalize row（list/dict 字段），只保留 struct_cls 声明的键，再构造 Struct。
    DB 多列而当前 schema 未加时不会报错（忽略未知列）。"""
    normalized = normalize_row(
        row,
        list_fields=list_fields,
        dict_fields=dict_fields,
        list_default_empty=list_default_empty,
    )
    if not normalized:
        return None
    filtered = filter_row_to_struct_keys(normalized, struct_cls)
    return struct_cls(**filtered) if filtered else None
