"""
asyncpg 工具函数
提供常用的数据库操作辅助函数
"""
import logging
from typing import List, Optional, Dict, Any, Union
from datetime import datetime
import json
import uuid as uuid_module

logger = logging.getLogger(__name__)


def generate_uuid() -> str:
    """生成 UUID"""
    return str(uuid_module.uuid4())


def now_utc() -> datetime:
    """获取当前 UTC 时间"""
    return datetime.utcnow()


def utc_isoformat(dt: Optional[datetime]) -> Optional[str]:
    """
    将「约定为 UTC」的 datetime 序列化为带 Z 的 ISO 8601 字符串，供 API 返回。
    后端统一存 UTC，返回给前端时带 Z，前端用 new Date(s) 会按 UTC 解析再按本地时区展示。
    - naive datetime：当作 UTC，末尾加 Z
    - timezone-aware：若为 UTC 则格式化为 ...Z，否则保留 offset
    """
    if dt is None:
        return None
    if not hasattr(dt, "isoformat"):
        return str(dt)
    s = dt.isoformat()
    if getattr(dt, "tzinfo", None) is None:
        return s + "Z"
    if s.endswith("+00:00"):
        return s[:-6] + "Z"
    return s


def to_json(data: Any) -> Optional[str]:
    """转换为 JSON 字符串"""
    if data is None:
        return None
    return json.dumps(data)


def from_json(data: Optional[str]) -> Any:
    """从 JSON 字符串解析"""
    if data is None:
        return None
    return json.loads(data)


def ensure_list(value: Any) -> Optional[List]:
    """将 DB 可能返回的 JSON 字符串或标量规范为 List（供 msgspec Struct List 字段用）。
    
    关键修复：
    1. 处理 JSON 字符串 'null'（json.loads 返回 None）
    2. 过滤列表中的 None 值（防止 [None] 变成 ['null']）
    """
    if value is None:
        return None
    if isinstance(value, list):
        # 过滤掉列表中的 None 值
        return [v for v in value if v is not None]
    if isinstance(value, str):
        # 处理 JSON 字符串 'null'
        if value == 'null':
            return None
        try:
            parsed = json.loads(value)
            # json.loads('null') 返回 None
            if parsed is None:
                return None
            # 如果是列表，过滤掉 None 值
            if isinstance(parsed, list):
                return [v for v in parsed if v is not None]
            # 其他类型包装成列表
            return [parsed]
        except (json.JSONDecodeError, TypeError):
            return [value]
    return list(value) if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)) else [value]


def ensure_dict(value: Any) -> Optional[Dict[str, Any]]:
    """将 DB 可能返回的 JSON 字符串规范为 Dict（供 msgspec Struct Dict 字段用）。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


# ==================== 通用：DB 行与 msgspec Struct 兼容（dev/prod 表结构不一致时） ====================
# 用法：任意 CRUD 在「从 DB 行构造 Struct」时用 row_to_struct(row, XxxDB)；在「INSERT 前」用 build_insert_row(XxxDB, **kwargs)。
# 这样 DB 多列或 schema 少列时不会报 Unexpected keyword argument；schema 有而 DB 暂无的列不会写入。

def struct_allowed_keys(struct_cls: type) -> set:
    """返回 struct_cls（msgspec.Struct）当前定义的字段名集合。"""
    return set(getattr(struct_cls, "__struct_fields__", ()))


def filter_row_to_struct_keys(row: Optional[Dict], struct_cls: type) -> Optional[Dict]:
    """只保留 struct_cls 里有的键。DB 多列而当前 schema 未加时，避免 Unexpected keyword argument。"""
    if row is None:
        return None
    allowed = struct_allowed_keys(struct_cls)
    return {k: v for k, v in row.items() if k in allowed}


def row_to_struct(row: Optional[Dict], struct_cls: type):
    """用 DB 行构造 Struct 实例，只传入 schema 有的键。兼容任意表/Struct。"""
    filtered = filter_row_to_struct_keys(row, struct_cls)
    return struct_cls(**filtered) if filtered else None


def build_insert_row(struct_cls: type, **kwargs) -> Dict:
    """只保留 struct_cls 里有的键，用于 INSERT。当前 schema 没有的列不写入，避免 DB 无该列时报错。"""
    allowed = struct_allowed_keys(struct_cls)
    return {k: v for k, v in kwargs.items() if k in allowed}


async def fetch_one(conn, query: str, *args) -> Optional[Dict]:
    """执行查询并返回单行结果（字典格式）"""
    try:
        row = await conn.fetchrow(query, *args)
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"fetch_one 失败: {e}, query: {query[:100]}")
        raise


async def fetch_all(conn, query: str, *args) -> List[Dict]:
    """执行查询并返回所有行（字典格式列表）"""
    try:
        rows = await conn.fetch(query, *args)
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"fetch_all 失败: {e}, query: {query[:100]}")
        raise


async def fetch_val(conn, query: str, *args) -> Any:
    """执行查询并返回单个值"""
    try:
        return await conn.fetchval(query, *args)
    except Exception as e:
        logger.error(f"fetch_val 失败: {e}, query: {query[:100]}")
        raise


async def execute(conn, query: str, *args) -> str:
    """执行 DML 语句（INSERT/UPDATE/DELETE）"""
    try:
        return await conn.execute(query, *args)
    except Exception as e:
        logger.error(f"execute 失败: {e}, query: {query[:100]}")
        raise


async def insert_and_return(conn, query_or_table: str, *args, **kwargs) -> Optional[Dict]:
    """执行 INSERT 并返回插入的行。
    两种用法：
    1) insert_and_return(conn, "INSERT INTO t (...) VALUES (...) RETURNING *", *values)
    2) insert_and_return(conn, "table_name", col1=val1, col2=val2, ...) 由 build_insert_query 拼 SQL
    """
    try:
        if kwargs:
            query, values = build_insert_query(query_or_table, kwargs)
            row = await conn.fetchrow(query, *values)
        else:
            row = await conn.fetchrow(query_or_table, *args)
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"insert_and_return 失败: {e}, query: {query_or_table[:100]}")
        raise


def build_where_clause(filters: Dict[str, Any], start_idx: int = 1) -> tuple[str, List[Any]]:
    """构建 WHERE 子句
    
    Args:
        filters: 过滤条件字典 {column: value}
        start_idx: 参数起始索引（默认从 $1 开始）
        
    Returns:
        (where_clause, values) 元组
    """
    if not filters:
        return "", []
    
    conditions = []
    values = []
    idx = start_idx
    
    for column, value in filters.items():
        if value is not None:
            conditions.append(f'"{column}" = ${idx}')
            values.append(value)
            idx += 1
    
    where_clause = " AND ".join(conditions) if conditions else ""
    return where_clause, values


def _clean_list_for_json(lst: list) -> list:
    """清理列表中的 None 值，防止 json.dumps 产生 'null' 字符串"""
    return [v for v in lst if v is not None]


def build_insert_query(table: str, data: Dict[str, Any]) -> tuple[str, List[Any]]:
    """构建 INSERT 查询
    
    Args:
        table: 表名
        data: 数据字典 {column: value}
        
    Returns:
        (query, values) 元组
        
    关键修复：
    1. 过滤掉列表中的 None 值，防止 json.dumps([None]) 产生 '[null]'
    2. 空列表不插入（避免 asyncpg 类型推断错误）
    """
    import json
    
    filtered_data = {}
    for k, v in data.items():
        if v is None:
            continue
        
        # 处理列表：清理 None 值
        if isinstance(v, list):
            cleaned = _clean_list_for_json(v)
            # 空列表跳过：asyncpg 无法推断空列表的元素类型
            if len(cleaned) == 0:
                continue
            filtered_data[k] = json.dumps(cleaned)
        # 处理字典
        elif isinstance(v, dict):
            filtered_data[k] = json.dumps(v)
        else:
            filtered_data[k] = v
    
    columns = list(filtered_data.keys())
    values = list(filtered_data.values())
    placeholders = [f"${i+1}" for i in range(len(columns))]
    # 列名用双引号包裹，避免 reserved keyword（如 order）导致 syntax error
    quoted_columns = ', '.join(f'"{c}"' for c in columns)
    query = f"""
        INSERT INTO {table} ({quoted_columns})
        VALUES ({', '.join(placeholders)})
        RETURNING *
    """
    return query, values


def build_update_query(
    table: str, 
    data: Dict[str, Any], 
    where: Dict[str, Any]
) -> tuple[str, List[Any]]:
    """构建 UPDATE 查询
    
    Args:
        table: 表名
        data: 更新数据字典 {column: value}
        where: WHERE 条件字典 {column: value}
        
    Returns:
        (query, values) 元组
        
    关键修复：
    1. 过滤掉列表中的 None 值，防止 json.dumps([None]) 产生 '[null]'
    2. 空列表跳过（避免 asyncpg 类型推断错误）
    """
    import json
    
    filtered_data = {}
    for k, v in data.items():
        if v is None:
            continue
        
        # 处理列表：清理 None 值
        if isinstance(v, list):
            cleaned = _clean_list_for_json(v)
            # 空列表跳过：asyncpg 无法推断空列表的元素类型
            if len(cleaned) == 0:
                continue
            filtered_data[k] = json.dumps(cleaned)
        # 处理字典
        elif isinstance(v, dict):
            filtered_data[k] = json.dumps(v)
        else:
            filtered_data[k] = v
    
    set_parts = []
    values = []
    idx = 1
    
    # 构建 SET 子句（列名加双引号，避免 reserved keyword 如 order）
    for column, value in filtered_data.items():
        set_parts.append(f'"{column}" = ${idx}')
        values.append(value)
        idx += 1
    
    # 构建 WHERE 子句
    where_clause, where_values = build_where_clause(where, start_idx=idx)
    values.extend(where_values)
    
    query = f"""
        UPDATE {table}
        SET {', '.join(set_parts)}
        WHERE {where_clause}
        RETURNING *
    """
    
    return query, values
