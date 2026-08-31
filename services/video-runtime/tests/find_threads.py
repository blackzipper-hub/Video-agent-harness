import asyncio, os, sys
os.environ["ENVIRONMENT"] = "local"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"), override=False)

async def c():
    from app.models.database import init_asyncpg_pool, get_asyncpg_pool
    await init_asyncpg_pool()
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT k.thread_id,
                count(DISTINCT k.uuid) as kfs,
                (SELECT count(*) FROM video_characters WHERE thread_id = k.thread_id) as chars,
                (SELECT count(*) FROM video_generations WHERE thread_id = k.thread_id) as vids,
                (SELECT count(*) FROM video_scenes WHERE thread_id = k.thread_id) as scenes,
                (SELECT title FROM video_story_outline WHERE thread_id = k.thread_id LIMIT 1) as title
            FROM video_keyframes k
            WHERE k.thread_id IS NOT NULL
            GROUP BY k.thread_id
            HAVING count(DISTINCT k.uuid) >= 3
            ORDER BY max(k.created_at) DESC LIMIT 10
        """)
        for r in rows:
            tid = r["thread_id"]
            run_id = await conn.fetchval("SELECT run_id FROM conversation_runs WHERE thread_id = $1 ORDER BY created_at LIMIT 1", tid)
            print(f"tid={tid}  run={run_id}  ch={r['chars']} kf={r['kfs']} vid={r['vids']} sc={r['scenes']}  title={str(r['title'])[:40]}")
asyncio.run(c())
