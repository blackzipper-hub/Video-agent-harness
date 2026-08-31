"""
Lipsync 重构 — 数据库字段回滚脚本（DROP 新增字段）

⚠️  注意：此脚本 **仅删除** 本次迁移新增的字段，不会影响已有字段。

回滚字段：
  - video_scenes.generation_mode
  - video_detailed_shots.generation_mode
  - video_generation_versions.generation_mode
  - video_generation_versions.audio_url

用法：
  python init/rollback_lipsync_fields.py development   # dev 环境
  python init/rollback_lipsync_fields.py production     # prod 环境
  python init/rollback_lipsync_fields.py local          # local 环境
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
# 本次迁移新增的字段（仅这些会被 DROP，绝不碰已有字段）
# ============================================================
ROLLBACK_COLUMNS = [
    {"table": "video_scenes", "column": "generation_mode"},
    {"table": "video_detailed_shots", "column": "generation_mode"},
    {"table": "video_generation_versions", "column": "generation_mode"},
    {"table": "video_generation_versions", "column": "audio_url"},
]


async def column_exists(conn, table_name: str, column_name: str) -> bool:
    """检查列是否存在"""
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


async def rollback():
    """执行回滚：删除本次迁移新增的字段"""
    import asyncpg

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        logger.error("❌ DATABASE_URL 未配置")
        sys.exit(1)

    # asyncpg 不识别 postgresql+asyncpg:// 前缀
    if "postgresql+asyncpg://" in database_url:
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    logger.info(f"🚀 开始回滚 lipsync 字段 (环境: {environment})")
    logger.info(f"⚠️  将删除 {len(ROLLBACK_COLUMNS)} 个字段")

    # 生产环境二次确认
    if environment == "production":
        confirm = input("⚠️  你正在操作 PRODUCTION 环境！确认回滚请输入 'YES': ")
        if confirm != "YES":
            logger.info("❌ 用户取消操作")
            sys.exit(0)

    conn = await asyncpg.connect(database_url)
    try:
        dropped = 0
        skipped = 0

        for item in ROLLBACK_COLUMNS:
            table = item["table"]
            column = item["column"]

            if not await column_exists(conn, table, column):
                logger.info(f"  ⏭️  {table}.{column} 不存在，跳过")
                skipped += 1
                continue

            ddl = f'ALTER TABLE {table} DROP COLUMN {column}'
            logger.info(f"  🗑️  DROP {table}.{column}")
            await conn.execute(ddl)
            dropped += 1

        logger.info(f"✅ 回滚完成：删除 {dropped} 个字段，跳过 {skipped} 个不存在字段")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(rollback())
