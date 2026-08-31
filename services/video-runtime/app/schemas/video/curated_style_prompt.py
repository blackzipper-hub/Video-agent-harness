"""
精选风格提示词 Schema - 使用 msgspec.Struct

对应表 curated_style_prompts（资产表）。
读路径使用 row_to_struct_safe，DB 多列不影响。
"""

import msgspec
from datetime import datetime
from typing import Optional


class CuratedStyleCategoryRow(msgspec.Struct, kw_only=True):
    """仅用于 DISTINCT category 查询的行，配合 row_to_struct_safe 防御 DB 多列"""

    category: Optional[str] = None


class CuratedStylePromptDB(msgspec.Struct, kw_only=True):
    """精选风格提示词 - 对应 curated_style_prompts 表"""

    # === 统一字段 ===
    id: int
    uuid: str
    created_at: datetime
    updated_at: datetime

    # === 业务字段 ===
    category: str  # 大类，如 kpop, cinematic_realistic
    name: str  # 风格名称
    name_zh: Optional[str] = None
    thumbnail_url: Optional[str] = None
    description_en: str  # 风格描述-英文（用于生成）
    description_zh: Optional[str] = None
    sort_order: Optional[int] = 0
    is_available: bool = True  # 是否可用，默认可用；获取用于匹配时仅返回可用的
