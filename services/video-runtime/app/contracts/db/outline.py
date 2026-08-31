"""
Outline DB row contracts (msgspec ↔ Postgres).

Canonical names: ``OutlineDB`` / ``ChapterDB``.
Bodies remain in ``app.schemas.video.video_story`` until a broader migrate.
No kit/schemas/db JSON export — Program uses these types directly.
"""
from app.schemas.video.video_story import (
    VideoChapterDB as ChapterDB,
    VideoStoryOutlineDB as OutlineDB,
    VideoStoryOutlineVersionDB as OutlineVersionDB,
)

__all__ = [
    "ChapterDB",
    "OutlineDB",
    "OutlineVersionDB",
]
