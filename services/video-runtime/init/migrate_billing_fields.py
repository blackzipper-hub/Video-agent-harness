"""
计费相关 DB 字段迁移（conversation_runs + video_task_records）

当前计费逻辑：成本记在 conversation_run 上，worker 扫描 conversation_runs.billing_status=pending，
核实 LangSmith 成本后更新 billing_status、cost_credits（dev 原有）与 6 个计费字段。因此 conversation_runs 的字段是必须的。

本脚本会迁移（conversation_runs 含 dev 原有 cost_credits，两表统一 6 个计费字段）：

1) conversation_runs（全类型 run 的计费）
   - billing_status     VARCHAR(32) DEFAULT NULL    pending | completed | failed
   - cost_credits       DOUBLE PRECISION DEFAULT NULL  dev 原有，扣减积分（兼容）
   - langsmith_cost     NUMERIC(19,6) DEFAULT NULL  LangSmith 成本（美元）
   - cost               NUMERIC(19,6) DEFAULT NULL  我方统计成本（美元）
   - cost_calculated    BOOLEAN DEFAULT NULL        是否已计算成本
   - credits_deducted   BOOLEAN DEFAULT NULL        是否已扣积分
   - credits_amount     INTEGER DEFAULT NULL        扣减积分数

2) video_task_records（仅 video 的计费，与 conversation_runs 对齐）
   - 同上 6 个字段

用法：
  # 先 dry-run 看哪些库缺字段（可对比 dev/prod）
  python init/migrate_billing_fields.py --env dev --dry-run
  python init/migrate_billing_fields.py --env prod --dry-run

  # 执行迁移（dev 先跑，再跑 prod）
  python init/migrate_billing_fields.py --env dev
  python init/migrate_billing_fields.py --env prod
  python init/migrate_billing_fields.py --env local
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


# 迁移定义：conversation_runs 与 video_task_records 统一 6 个计费字段
MIGRATIONS = [
    # conversation_runs（全类型 run；含 dev 原有 cost_credits）
    {"table": "conversation_runs", "column": "billing_status", "type": "VARCHAR(32)", "default": "NULL", "description": "计费状态: pending | completed | failed"},
    {"table": "conversation_runs", "column": "cost_credits", "type": "DOUBLE PRECISION", "default": "NULL", "description": "扣减积分（dev 原有，兼容）"},
    {"table": "conversation_runs", "column": "langsmith_cost", "type": "NUMERIC(19,6)", "default": "NULL", "description": "LangSmith 成本（美元）"},
    {"table": "conversation_runs", "column": "cost", "type": "NUMERIC(19,6)", "default": "NULL", "description": "我方统计成本（美元）"},
    {"table": "conversation_runs", "column": "cost_calculated", "type": "BOOLEAN", "default": "NULL", "description": "是否已计算成本"},
    {"table": "conversation_runs", "column": "credits_deducted", "type": "BOOLEAN", "default": "NULL", "description": "是否已扣积分"},
    {"table": "conversation_runs", "column": "credits_amount", "type": "INTEGER", "default": "NULL", "description": "扣减积分数"},
    {"table": "conversation_runs", "column": "run_type", "type": "VARCHAR(32)", "default": "NULL", "description": "二级动作: main, resume, regenerate_keyframes, regenerate_videos, regenerate_characters"},
    # credit_history：计费幂等用，不展示给用户
    {"table": "credit_history", "column": "reference_id", "type": "VARCHAR(64)", "default": "NULL", "description": "关联ID如 run_id，仅内部幂等用不展示"},
    # video_task_records（仅 video，字段对齐）
    {"table": "video_task_records", "column": "billing_status", "type": "VARCHAR(32)", "default": "NULL", "description": "计费状态: pending | pending_langsmith | completed | failed"},
    {"table": "video_task_records", "column": "langsmith_cost", "type": "NUMERIC(19,6)", "default": "NULL", "description": "LangSmith 成本（美元）"},
    {"table": "video_task_records", "column": "cost", "type": "NUMERIC(19,6)", "default": "NULL", "description": "我方统计成本（美元）"},
    {"table": "video_task_records", "column": "cost_calculated", "type": "BOOLEAN", "default": "NULL", "description": "是否已计算成本"},
    {"table": "video_task_records", "column": "credits_deducted", "type": "BOOLEAN", "default": "NULL", "description": "是否已扣积分"},
    {"table": "video_task_records", "column": "credits_amount", "type": "INTEGER", "default": "NULL", "description": "扣减积分数"},
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
        description="计费字段迁移：conversation_runs（必须）+ video_task_records（可选）"
    )
    parser.add_argument("--env", default="dev", help="环境: dev, prod, local")
    parser.add_argument("--dry-run", action="store_true", help="仅检查并打印将添加的字段，不执行 ALTER（用于对比 dev/prod）")
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
