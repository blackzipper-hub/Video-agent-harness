"""
新增 langsmith_status 字段迁移（conversation_runs）

背景：
- billing_status 仅跟踪扣款流程（pending → completed / failed）
- langsmith_status 独立跟踪 LangSmith 成本写入状态（pending → completed / failed）
- image agent 固定积分扣款后直接标 billing_status=completed，但 langsmith_cost 需异步补写
  billing_worker 扫描 langsmith_status=pending 的 run 补写 langsmith_cost，完成后标 completed

迁移字段：
1) conversation_runs
   - langsmith_status  VARCHAR(32) DEFAULT NULL   pending | completed | failed

用法：
  # dry-run（对比 dev/prod 环境）
  python init/migrate_langsmith_status.py --env dev --dry-run
  python init/migrate_langsmith_status.py --env prod --dry-run

  # 执行迁移
  python init/migrate_langsmith_status.py --env dev
  python init/migrate_langsmith_status.py --env prod
  python init/migrate_langsmith_status.py --env local
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


MIGRATIONS = [
    {
        "table": "conversation_runs",
        "column": "langsmith_status",
        "type": "VARCHAR(32)",
        "default": "NULL",
        "description": "LangSmith 成本写入状态: pending | completed | failed（独立于扣款 billing_status）",
    },
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
        added = 0
        skipped = 0
        for m in MIGRATIONS:
            table = m["table"]
            column = m["column"]
            col_type = m["type"]
            default = m["default"]
            desc = m["description"]

            exists = await column_exists(conn, table, column)
            if exists:
                logger.info("  ⏭️  %s.%s 已存在，跳过", table, column)
                skipped += 1
                continue

            if dry_run:
                logger.info("  [dry-run] 将添加 %s.%s (%s DEFAULT %s) — %s", table, column, col_type, default, desc)
                added += 1
                continue

            ddl = f"ALTER TABLE {table} ADD COLUMN {column} {col_type} DEFAULT {default}"
            logger.info("  ➕ %s.%s (%s) — %s", table, column, col_type, desc)
            await conn.execute(ddl)
            added += 1

        if dry_run:
            logger.info("✅ dry-run 完成：将新增 %s 个字段，已存在 %s 个", added, skipped)
        else:
            logger.info("✅ 迁移完成：新增 %s 个字段，跳过 %s 个已存在字段", added, skipped)
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="新增 langsmith_status 字段：conversation_runs"
    )
    parser.add_argument("--env", default="dev", help="环境: dev, prod, local")
    parser.add_argument("--dry-run", action="store_true", help="仅检查并打印将添加的字段，不执行 ALTER")
    parser.add_argument("positional_env", nargs="?", help="兼容位置参数: development, production, local")
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
