"""
音乐 MV 三层字段与 Outline/Chapter/Scene Version 表迁移

1) video_audio_transcription 新增列：
   - song_name, global_bpm, genre, global_emotion, suggested_global_theme, suggested_color_palette

2) video_audio_section 新表（无外键，仅索引）

3) video_story_outline 新增列：
   - audio_transcription_uuid, current_version_index

4) video_chapters 新增列：
   - audio_section_uuid, current_version_index

5) video_scenes 新增列：
   - current_version_index

6) video_audio_segment 新增列：
   - vocal_presence

7) video_story_outline_versions 新表（Outline 版本快照）

8) video_chapter_versions 新表（Chapter 版本快照）

9) video_scene_versions 新表（Scene 版本快照）

duration 分配逻辑暂不改动，仍由代码侧分配。

用法（与 migrate_billing_fields / migrate_tool_consistency_fields 一致）：
  python init/migrate_music_mv_three_layer.py --env dev --dry-run
  python init/migrate_music_mv_three_layer.py --env prod --dry-run
  python init/migrate_music_mv_three_layer.py --env dev
  python init/migrate_music_mv_three_layer.py --env prod
  python init/migrate_music_mv_three_layer.py --env local
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


# ALTER 迁移：表 -> [(列, 类型, 默认)]
ALTER_MIGRATIONS = [
    ("video_audio_transcription", [
        ("song_name", "VARCHAR(512)", "NULL"),
        ("global_bpm", "NUMERIC(10,2)", "NULL"),
        ("genre", "VARCHAR(128)", "NULL"),
        ("global_emotion", "VARCHAR(128)", "NULL"),
        ("suggested_global_theme", "TEXT", "NULL"),
        ("suggested_color_palette", "VARCHAR(256)", "NULL"),
    ]),
    ("video_story_outline", [
        ("audio_transcription_uuid", "UUID", "NULL"),
        ("current_version_index", "INTEGER", "0"),
    ]),
    ("video_chapters", [
        ("audio_section_uuid", "UUID", "NULL"),
        ("current_version_index", "INTEGER", "0"),
    ]),
    ("video_scenes", [
        ("current_version_index", "INTEGER", "0"),
    ]),
    ("video_audio_segment", [
        ("vocal_presence", "BOOLEAN", "NULL"),
    ]),
]

# 新表（无 REFERENCES，仅索引）
DDL_VIDEO_AUDIO_SECTION = """
CREATE TABLE IF NOT EXISTS video_audio_section (
  id BIGSERIAL PRIMARY KEY,
  uuid UUID NOT NULL UNIQUE,
  user_id VARCHAR(64),
  conversation_id VARCHAR(64),
  thread_id VARCHAR(64),
  run_id VARCHAR(64),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  transcription_uuid UUID NOT NULL,
  section_type VARCHAR(64) NOT NULL,
  start_time NUMERIC(12,3) NOT NULL,
  end_time NUMERIC(12,3) NOT NULL,
  musical_features TEXT,
  section_emotion VARCHAR(128),
  suggested_visual_intensity VARCHAR(64),
  suggested_rhythmic_strategy VARCHAR(128),
  suggested_visual_theme VARCHAR(256),
  suggested_context TEXT,
  additional_data JSONB
);
CREATE INDEX IF NOT EXISTS idx_video_audio_section_transcription
  ON video_audio_section(transcription_uuid);
CREATE INDEX IF NOT EXISTS idx_video_audio_section_transcription_start
  ON video_audio_section(transcription_uuid, start_time);
"""

DDL_VIDEO_STORY_OUTLINE_VERSIONS = """
CREATE TABLE IF NOT EXISTS video_story_outline_versions (
  id BIGSERIAL PRIMARY KEY,
  uuid UUID NOT NULL UNIQUE,
  story_outline_id UUID NOT NULL,
  version_number INTEGER NOT NULL,
  user_id VARCHAR(64),
  conversation_id VARCHAR(64),
  thread_id VARCHAR(64),
  run_id VARCHAR(64),
  title TEXT,
  description TEXT,
  theme TEXT,
  key_message TEXT,
  total_duration INTEGER,
  style_guide TEXT,
  analysis_id VARCHAR(64),
  additional_data JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_video_story_outline_versions_outline_id
  ON video_story_outline_versions(story_outline_id);
"""

DDL_VIDEO_CHAPTER_VERSIONS = """
CREATE TABLE IF NOT EXISTS video_chapter_versions (
  id BIGSERIAL PRIMARY KEY,
  uuid UUID NOT NULL UNIQUE,
  chapter_id UUID NOT NULL,
  version_number INTEGER NOT NULL,
  user_id VARCHAR(64),
  conversation_id VARCHAR(64),
  thread_id VARCHAR(64),
  run_id VARCHAR(64),
  story_outline_id UUID,
  title TEXT,
  description TEXT,
  duration NUMERIC(12,3),
  "order" INTEGER,
  audio_segment_ids JSONB,
  additional_data JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_video_chapter_versions_chapter_id
  ON video_chapter_versions(chapter_id);
"""

DDL_VIDEO_SCENE_VERSIONS = """
CREATE TABLE IF NOT EXISTS video_scene_versions (
  id BIGSERIAL PRIMARY KEY,
  uuid UUID NOT NULL UNIQUE,
  scene_id UUID NOT NULL,
  version_number INTEGER NOT NULL,
  user_id VARCHAR(64),
  conversation_id VARCHAR(64),
  thread_id VARCHAR(64),
  run_id VARCHAR(64),
  scene_number INTEGER,
  title TEXT,
  description TEXT,
  duration INTEGER,
  camera_angle TEXT,
  character_action TEXT,
  visual_style TEXT,
  transition_style TEXT,
  is_bridge BOOLEAN,
  character_ids JSONB,
  audio_segment_ids JSONB,
  chapter_id UUID,
  generation_mode VARCHAR(32),
  additional_data JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_video_scene_versions_scene_id
  ON video_scene_versions(scene_id);
"""


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
        # 1. ALTER 新增列
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

        # 2. 新表 video_audio_section（无外键，仅索引）
        if not dry_run:
            for stmt in DDL_VIDEO_AUDIO_SECTION.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    await conn.execute(stmt)
            logger.info("  ➕ video_audio_section (CREATE TABLE IF NOT EXISTS + idx)")
        else:
            logger.info("  [dry-run] CREATE TABLE video_audio_section + indexes")

        # 3. 新表 video_story_outline_versions
        if not dry_run:
            for stmt in DDL_VIDEO_STORY_OUTLINE_VERSIONS.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    await conn.execute(stmt)
            logger.info("  ➕ video_story_outline_versions + idx")
        else:
            logger.info("  [dry-run] CREATE TABLE video_story_outline_versions + index")

        # 4. 新表 video_chapter_versions
        if not dry_run:
            for stmt in DDL_VIDEO_CHAPTER_VERSIONS.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    await conn.execute(stmt)
            logger.info("  ➕ video_chapter_versions + idx")
        else:
            logger.info("  [dry-run] CREATE TABLE video_chapter_versions + index")

        # 5. 新表 video_scene_versions
        if not dry_run:
            for stmt in DDL_VIDEO_SCENE_VERSIONS.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    await conn.execute(stmt)
            logger.info("  ➕ video_scene_versions + idx")
        else:
            logger.info("  [dry-run] CREATE TABLE video_scene_versions + index")

        if dry_run:
            logger.info("✅ dry-run 完成")
        else:
            logger.info("✅ 迁移完成")
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="音乐 MV 三层字段 + Outline/Chapter/Scene Version 表迁移（无外键，仅索引）"
    )
    parser.add_argument("--env", default="dev", help="环境: dev, prod, local")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将执行的操作，不执行")
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
