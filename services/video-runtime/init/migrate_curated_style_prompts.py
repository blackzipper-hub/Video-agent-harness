"""
精选风格提示词 — 数据库迁移脚本

1. 新建表 curated_style_prompts（资产表：id, uuid, created_at, updated_at, category, name, name_zh, thumbnail_url, description_en, description_zh, sort_order）
2. 为 video_analysis 表增加 hidden_style_description、curated_style_prompt_id 字段

用法：
  python init/migrate_curated_style_prompts.py development
  python init/migrate_curated_style_prompts.py production   # 或 prod
  python init/migrate_curated_style_prompts.py local
"""
import asyncio
import os
import sys
import logging
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

environment = sys.argv[1] if len(sys.argv) > 1 else "development"
# 支持 prod 作为 production 的别名，避免误用 development
if environment == "prod":
    environment = "production"
if environment == "local":
    load_dotenv(".env.local")
elif environment == "development":
    load_dotenv(".env.development")
elif environment == "production":
    load_dotenv(".env.production")
else:
    load_dotenv(".env.development")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 新表 DDL（仅本次迁移涉及）
CREATE_CURATED_STYLE_PROMPTS_TABLE = """
CREATE TABLE IF NOT EXISTS curated_style_prompts (
    id SERIAL PRIMARY KEY,
    uuid VARCHAR(36) NOT NULL UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    category VARCHAR(64) NOT NULL,
    name VARCHAR(256) NOT NULL,
    name_zh VARCHAR(256),
    thumbnail_url TEXT,
    description_en TEXT NOT NULL,
    description_zh TEXT,
    sort_order INTEGER DEFAULT 0
)
"""
CREATE_INDEX_CATEGORY = "CREATE INDEX IF NOT EXISTS idx_curated_style_prompts_category ON curated_style_prompts(category)"
CREATE_INDEX_UUID = "CREATE INDEX IF NOT EXISTS idx_curated_style_prompts_uuid ON curated_style_prompts(uuid)"

# video_analysis 新增列（与 migrate_lipsync_fields 一致：先检查再 ADD）
VIDEO_ANALYSIS_MIGRATIONS = [
    {
        "column": "content_category",
        "type": "VARCHAR(64)",
        "default": "NULL",
        "description": "内容类别：Default / Lip-Sync MV 等（与 user_option 一致）",
    },
    {
        "column": "hidden_style_description",
        "type": "TEXT",
        "default": "NULL",
        "description": "精选风格提示词描述（电影写实时从库中随机取一条，供下游生成用，不展示给前端）",
    },
    {
        "column": "curated_style_prompt_id",
        "type": "VARCHAR(36)",
        "default": "NULL",
        "description": "关联的精选风格提示词 uuid（追溯用）",
    },
]


async def table_exists(conn, table_name: str) -> bool:
    result = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = $1
        )
        """,
        table_name,
    )
    return result


async def column_exists(conn, table_name: str, column_name: str) -> bool:
    result = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2
        )
        """,
        table_name,
        column_name,
    )
    return result


async def migrate_up():
    import asyncpg

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        logger.error("DATABASE_URL 未配置")
        sys.exit(1)
    if "postgresql+asyncpg://" in database_url:
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    logger.info("开始执行精选风格提示词迁移 (环境: %s)", environment)

    conn = await asyncpg.connect(database_url)
    try:
        # 1. 创建 curated_style_prompts 表
        if await table_exists(conn, "curated_style_prompts"):
            logger.info("表 curated_style_prompts 已存在，跳过创建")
        else:
            await conn.execute(CREATE_CURATED_STYLE_PROMPTS_TABLE)
            await conn.execute(CREATE_INDEX_CATEGORY)
            await conn.execute(CREATE_INDEX_UUID)
            logger.info("已创建表 curated_style_prompts 及索引")

        # 2. video_analysis 新增列
        for m in VIDEO_ANALYSIS_MIGRATIONS:
            col, typ, default, desc = m["column"], m["type"], m["default"], m["description"]
            if await column_exists(conn, "video_analysis", col):
                logger.info("video_analysis.%s 已存在，跳过", col)
                continue
            ddl = f'ALTER TABLE video_analysis ADD COLUMN {col} {typ} DEFAULT {default}'
            await conn.execute(ddl)
            logger.info("video_analysis.%s (%s) — %s", col, typ, desc)

        logger.info("迁移完成")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate_up())
