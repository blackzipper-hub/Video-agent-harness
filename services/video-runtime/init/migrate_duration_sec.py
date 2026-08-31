"""
Duration 相关字段：新增 double 列（xxx_sec）并回填，与 duration_integer_to_double_migration_plan 一致。

新增列（全部 nullable，不加 DEFAULT）：
  - video_analysis.duration_sec
  - video_story_outline.total_duration_sec
  - video_storyboard_details.total_duration_sec
  - video_scenes.duration_sec
  - video_detailed_shots.duration_sec
  - video_task_records.actual_target_duration_sec

阶段 1：ADD COLUMN
阶段 2：回填 UPDATE SET xxx_sec = 旧列::double precision WHERE xxx_sec IS NULL

用法（与 migrate_billing_fields / migrate_tool_consistency_fields 一致）：
  python init/migrate_duration_sec.py --env dev --dry-run
  python init/migrate_duration_sec.py --env prod --dry-run
  python init/migrate_duration_sec.py --env dev
  python init/migrate_duration_sec.py --env prod
  python init/migrate_duration_sec.py --env local

  # 仅加列、不回填（可选）
  python init/migrate_duration_sec.py --env dev --add-only
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


# 新增列定义：(表, 新列名, 旧列名，用于回填)
ADD_COLUMNS = [
    ("video_analysis", "duration_sec", "duration"),
    ("video_story_outline", "total_duration_sec", "total_duration"),
    ("video_storyboard_details", "total_duration_sec", "total_duration"),
    ("video_scenes", "duration_sec", "duration"),
    ("video_detailed_shots", "duration_sec", "duration"),
    ("video_task_records", "actual_target_duration_sec", "actual_target_duration"),
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


async def run_migrate(database_url: str, dry_run: bool, add_only: bool) -> None:
    import asyncpg

    if "postgresql+asyncpg://" in database_url:
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(database_url)
    try:
        # ---------- 阶段 1：ADD COLUMN ----------
        for table, new_col, old_col in ADD_COLUMNS:
            exists = await column_exists(conn, table, new_col)
            if exists:
                logger.info("  ⏭️  %s.%s 已存在，跳过", table, new_col)
                continue

            if dry_run:
                logger.info("  [dry-run] 将添加 %s.%s (double precision) — 回填自 %s", table, new_col, old_col)
                continue

            ddl = f"ALTER TABLE {table} ADD COLUMN {new_col} double precision"
            logger.info("  ➕ %s.%s (double precision) — 回填自 %s", table, new_col, old_col)
            await conn.execute(ddl)

        if add_only or dry_run:
            if dry_run:
                logger.info("✅ dry-run 完成（未执行回填）")
            return

        # ---------- 阶段 2：回填 ----------
        for table, new_col, old_col in ADD_COLUMNS:
            exists = await column_exists(conn, table, new_col)
            if not exists:
                continue

            if dry_run:
                continue

            # 只更新 xxx_sec IS NULL 的行，用旧列转 double
            backfill_sql = (
                f"UPDATE {table} SET {new_col} = {old_col}::double precision WHERE {new_col} IS NULL AND {old_col} IS NOT NULL"
            )
            try:
                result = await conn.execute(backfill_sql)
                logger.info("  📥 %s: 回填 %s ← %s (%s)", table, new_col, old_col, result)
            except Exception as e:
                logger.warning("  ⚠️ %s 回填跳过: %s", table, e)

        # ---------- 阶段 3：旧列 DROP NOT NULL（5 个表；video_task_records.actual_target_duration 已可空）----------
        DROP_NOT_NULL = [
            ("video_analysis", "duration"),
            ("video_story_outline", "total_duration"),
            ("video_storyboard_details", "total_duration"),
            ("video_scenes", "duration"),
            ("video_detailed_shots", "duration"),
        ]
        for table, old_col in DROP_NOT_NULL:
            try:
                ddl = f"ALTER TABLE {table} ALTER COLUMN {old_col} DROP NOT NULL"
                await conn.execute(ddl)
                logger.info("  🔓 %s.%s DROP NOT NULL", table, old_col)
            except Exception as e:
                logger.warning("  ⚠️ %s.%s DROP NOT NULL 跳过: %s", table, old_col, e)

        logger.info("✅ 迁移完成：新列已添加、已回填、旧列已可空")
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Duration 字段迁移：新增 xxx_sec (double)，并回填自旧列"
    )
    parser.add_argument("--env", default="dev", help="环境: dev, prod, local")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将执行的操作，不执行")
    parser.add_argument("--add-only", action="store_true", help="仅添加新列，不回填")
    parser.add_argument("positional_env", nargs="?", help="兼容位置参数: development, production, local")
    args = parser.parse_args()
    env = args.positional_env or args.env
    load_env(env)

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        logger.error("DATABASE_URL 未配置")
        sys.exit(1)

    asyncio.run(run_migrate(database_url, dry_run=args.dry_run, add_only=args.add_only))


if __name__ == "__main__":
    main()
