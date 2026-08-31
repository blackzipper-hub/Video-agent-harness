"""
精选风格提示词 — 增加 is_available 字段迁移脚本

为 curated_style_prompts 表增加 is_available BOOLEAN DEFAULT true（是否可用，默认可用）。
获取用于匹配时仅返回 is_available=true 的记录。

用法：
  python init/migrate_curated_style_prompts_is_available.py development
  python init/migrate_curated_style_prompts_is_available.py production   # 或 prod
  python init/migrate_curated_style_prompts_is_available.py local
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

    logger.info("开始执行 is_available 字段迁移 (环境: %s)", environment)

    conn = await asyncpg.connect(database_url)
    try:
        if await column_exists(conn, "curated_style_prompts", "is_available"):
            logger.info("curated_style_prompts.is_available 已存在，跳过")
        else:
            await conn.execute(
                "ALTER TABLE curated_style_prompts ADD COLUMN is_available BOOLEAN NOT NULL DEFAULT true"
            )
            logger.info("已为 curated_style_prompts 添加列 is_available (DEFAULT true)")

        logger.info("迁移完成")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate_up())
