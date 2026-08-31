"""
video_detailed_shots 表新增 generation_routing（JSONB），存 per-shot 路由主决策。

读路径经 _row_to_shot + row_to_struct_safe：未迁移时 SELECT 无此列则 generation_routing 为 None，旧代码不崩。

用法（与 migrate_tool_consistency_fields 一致）：
  python init/migrate_video_detailed_shots_generation_routing.py --env dev --dry-run
  python init/migrate_video_detailed_shots_generation_routing.py --env dev
  python init/migrate_video_detailed_shots_generation_routing.py --env prod
  python init/migrate_video_detailed_shots_generation_routing.py --env local
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
        table, col = "video_detailed_shots", "generation_routing"
        exists = await column_exists(conn, table, col)
        if exists:
            logger.info("⏭️  %s.%s 已存在，跳过", table, col)
            return

        if dry_run:
            logger.info("[dry-run] 将执行: ALTER TABLE %s ADD COLUMN %s JSONB DEFAULT NULL", table, col)
            return

        await conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {col} JSONB DEFAULT NULL"
        )
        await conn.execute(
            f"COMMENT ON COLUMN {table}.{col} IS "
            f"'Per-shot routing JSON: recommended_generation_mode, tool hints, rationale'"
        )
        logger.info("✅ 已添加 %s.%s", table, col)
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="video_detailed_shots.generation_routing JSONB 列迁移"
    )
    parser.add_argument("--env", default="dev", help="环境: dev, prod, local")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将执行的操作")
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
