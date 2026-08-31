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
        tid = "efe5af1c-a058-4765-b648-e3e70b4172e5"
        rows = await conn.fetch("SELECT shot_number, generation_mode FROM video_generations WHERE thread_id = $1 ORDER BY shot_number", tid)
        for r in rows:
            print(f"  shot_{r['shot_number']} mode={r['generation_mode']}")

        # Also check narrations for this thread
        nars = await conn.fetch("SELECT shot_number, audio_url FROM video_narrations WHERE thread_id = $1 ORDER BY shot_number", tid)
        print(f"\nNarrations ({len(nars)}):")
        for n in nars:
            au = str(n["audio_url"])[:60] if n["audio_url"] else "None"
            print(f"  shot_{n['shot_number']} audio={au}")
asyncio.run(c())
