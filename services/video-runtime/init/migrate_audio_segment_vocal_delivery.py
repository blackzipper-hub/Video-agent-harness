"""
video_audio_segment 新增列：vocal_gender

- vocal_gender: 该片段人声性别，'f' 女声 / 'm' 男声，可选

用法（与 migrate_music_mv_three_layer 一致）：
  python init/migrate_audio_segment_vocal_delivery.py --env dev --dry-run
  python init/migrate_audio_segment_vocal_delivery.py --env dev
"""
import asyncio
import os
import sys
import argparse
import logging
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_env(env: str) -> None:
    env_lower = env.lower()
    if env_lower in ("local",):
        load_dotenv(".env.local")
        logger.info("使用 .env.local")
    elif env_lower in ("prod", "production"):
        load_dotenv(".env.production")
        logger.info("使用 .env.production")
    else:
        load_dotenv(".env.development")
        logger.info("使用 .env.development")


ALTER_MIGRATIONS = [
    ("video_audio_segment", [
        ("vocal_gender", "VARCHAR(8)", "NULL"),   # 'f' | 'm'
    ]),
]


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


async def run_migrate(database_url: str, dry_run: bool) -> None:
    import asyncpg

    if "postgresql+asyncpg://" in database_url:
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(database_url)
    try:
        for table, columns in ALTER_MIGRATIONS:
            for col, col_type, default in columns:
                exists = await column_exists(conn, table, col)
                if exists:
                    logger.info("  ⏭️  %s.%s 已存在，跳过", table, col)
                    continue
                ddl = f"ALTER TABLE {table} ADD COLUMN {col} {col_type} DEFAULT {default}"
                if dry_run:
                    logger.info("  [dry-run] %s", ddl)
                else:
                    logger.info("  ➕ %s.%s", table, col)
                    await conn.execute(ddl)
        if dry_run:
            logger.info("✅ dry-run 完成")
        else:
            logger.info("✅ 迁移完成")
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="video_audio_segment 新增 vocal_gender 列"
    )
    parser.add_argument("--env", default="dev", help="环境: dev, prod, local")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将执行的操作，不执行")
    parser.add_argument("positional_env", nargs="?", help="兼容位置参数")
    args = parser.parse_args()
    env = args.positional_env or args.env
    load_env(env)

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        logger.error("DATABASE_URL 未配置")
        sys.exit(1)

    asyncio.run(run_migrate(database_url, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
