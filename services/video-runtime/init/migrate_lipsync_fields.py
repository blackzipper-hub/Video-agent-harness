"""
Lipsync 重构 — 数据库字段迁移脚本

新增字段：
  - video_scenes.generation_mode VARCHAR(20) DEFAULT NULL
  - video_detailed_shots.generation_mode VARCHAR(20) DEFAULT NULL
  - video_generation_versions.generation_mode VARCHAR(20) DEFAULT NULL
  - video_generation_versions.audio_url TEXT DEFAULT NULL

用法：
  python init/migrate_lipsync_fields.py development   # dev 环境
  python init/migrate_lipsync_fields.py production     # prod 环境
  python init/migrate_lipsync_fields.py local          # local 环境
"""
import asyncio
import os
import sys
import logging
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

# 根据命令行参数加载对应的环境配置
environment = sys.argv[1] if len(sys.argv) > 1 else "development"
if environment == "local":
    load_dotenv(".env.local")
elif environment == "development":
    load_dotenv(".env.development")
elif environment == "production":
    load_dotenv(".env.production")
else:
    load_dotenv(".env.development")

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# 新增字段定义（仅本次迁移涉及的字段）
# ============================================================
MIGRATIONS = [
    {
        "table": "video_scenes",
        "column": "generation_mode",
        "type": "VARCHAR(20)",
        "default": "NULL",
        "description": "生成模式：normal | lipsync",
    },
    {
        "table": "video_detailed_shots",
        "column": "generation_mode",
        "type": "VARCHAR(20)",
        "default": "NULL",
        "description": "生成模式：normal | lipsync",
    },
    {
        "table": "video_generation_versions",
        "column": "generation_mode",
        "type": "VARCHAR(20)",
        "default": "NULL",
        "description": "生成模式：normal | lipsync",
    },
    {
        "table": "video_generation_versions",
        "column": "audio_url",
        "type": "TEXT",
        "default": "NULL",
        "description": "lipsync 模式下使用的音频 URL（审计/regenerate 用）",
    },
]


async def column_exists(conn, table_name: str, column_name: str) -> bool:
    """检查列是否已存在"""
    result = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = $1 AND column_name = $2
        )
        """,
        table_name,
        column_name,
    )
    return result


async def migrate_up():
    """执行迁移：添加新字段"""
    import asyncpg

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        logger.error("❌ DATABASE_URL 未配置")
        sys.exit(1)

    # asyncpg 不识别 postgresql+asyncpg:// 前缀，需要转为 postgresql://
    if "postgresql+asyncpg://" in database_url:
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    logger.info(f"🚀 开始执行 lipsync 字段迁移 (环境: {environment})")
    logger.info(f"📦 共 {len(MIGRATIONS)} 个字段待迁移")

    conn = await asyncpg.connect(database_url)
    try:
        added = 0
        skipped = 0

        for m in MIGRATIONS:
            table = m["table"]
            column = m["column"]
            col_type = m["type"]
            default = m["default"]
            desc = m["description"]

            if await column_exists(conn, table, column):
                logger.info(f"  ⏭️  {table}.{column} 已存在，跳过")
                skipped += 1
                continue

            ddl = f'ALTER TABLE {table} ADD COLUMN {column} {col_type} DEFAULT {default}'
            logger.info(f"  ➕ {table}.{column} ({col_type}) — {desc}")
            await conn.execute(ddl)
            added += 1

        logger.info(f"✅ 迁移完成：新增 {added} 个字段，跳过 {skipped} 个已存在字段")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate_up())
