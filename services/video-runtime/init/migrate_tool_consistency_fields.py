"""
Tool 一致性 / metrics 相关 DB 字段迁移

新增字段（与 consistency-enhancement-plan 一致）：
1) video_task_records
   - tool_consistency_summary  JSONB DEFAULT NULL  角色/关键帧/视频一致性汇总（列表用）

2) video_character_generation_versions
   - image_tool_metrics       JSONB DEFAULT NULL  当次 image wrapper metrics
   - tool_duration_sec        DOUBLE PRECISION DEFAULT NULL  当次调用耗时（秒）
   - tool_cost                 DOUBLE PRECISION DEFAULT NULL  当次调用成本（美元）

3) video_keyframe_versions
   - image_tool_metrics       JSONB DEFAULT NULL
   - tool_duration_sec        DOUBLE PRECISION DEFAULT NULL
   - tool_cost                 DOUBLE PRECISION DEFAULT NULL

4) video_generation_versions
   - video_tool_metrics       JSONB DEFAULT NULL  当次 video 工具 metrics（预留）
   - tool_duration_sec        DOUBLE PRECISION DEFAULT NULL  当次调用耗时（秒）
   - tool_cost                 DOUBLE PRECISION DEFAULT NULL  当次调用成本（美元）

用法（与 migrate_billing_fields 一致，dev/prod/local 同脚本）：
  python init/migrate_tool_consistency_fields.py --env dev --dry-run
  python init/migrate_tool_consistency_fields.py --env prod --dry-run
  python init/migrate_tool_consistency_fields.py --env dev
  python init/migrate_tool_consistency_fields.py --env prod
  python init/migrate_tool_consistency_fields.py --env local
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
    {"table": "video_task_records", "column": "tool_consistency_summary", "type": "JSONB", "default": "NULL", "description": "角色/关键帧/视频一致性汇总"},
    {"table": "video_character_generation_versions", "column": "image_tool_metrics", "type": "JSONB", "default": "NULL", "description": "当次 image wrapper metrics"},
    {"table": "video_character_generation_versions", "column": "tool_duration_sec", "type": "DOUBLE PRECISION", "default": "NULL", "description": "当次调用耗时(秒)"},
    {"table": "video_character_generation_versions", "column": "tool_cost", "type": "DOUBLE PRECISION", "default": "NULL", "description": "当次调用成本(美元)"},
    {"table": "video_keyframe_versions", "column": "image_tool_metrics", "type": "JSONB", "default": "NULL", "description": "当次 image wrapper metrics"},
    {"table": "video_keyframe_versions", "column": "tool_duration_sec", "type": "DOUBLE PRECISION", "default": "NULL", "description": "当次调用耗时(秒)"},
    {"table": "video_keyframe_versions", "column": "tool_cost", "type": "DOUBLE PRECISION", "default": "NULL", "description": "当次调用成本(美元)"},
    {"table": "video_generation_versions", "column": "video_tool_metrics", "type": "JSONB", "default": "NULL", "description": "当次 video 工具 metrics"},
    {"table": "video_generation_versions", "column": "tool_duration_sec", "type": "DOUBLE PRECISION", "default": "NULL", "description": "当次调用耗时(秒)"},
    {"table": "video_generation_versions", "column": "tool_cost", "type": "DOUBLE PRECISION", "default": "NULL", "description": "当次调用成本(美元)"},
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
        description="Tool 一致性 / metrics 字段迁移（video_task_records + character/keyframe/video_generation versions）"
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
