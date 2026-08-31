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
        tid = "87516541-4690-49b4-ad80-ac03623ce224"
        o = await conn.fetchrow("SELECT uuid, title FROM video_story_outline WHERE thread_id = $1 LIMIT 1", tid)
        print(f"outline: {o['uuid']}  title={o['title']}")
        chars = await conn.fetch("SELECT uuid, name FROM video_characters WHERE thread_id = $1 AND type = 'character'", tid)
        print(f"chars ({len(chars)}):")
        for c2 in chars:
            print(f"  {c2['name']}  {c2['uuid']}")
        kfs = await conn.fetch("SELECT uuid, shot_number FROM video_keyframes WHERE thread_id = $1 ORDER BY shot_number LIMIT 3", tid)
        print(f"keyframes (first 3):")
        for k in kfs:
            print(f"  shot_{k['shot_number']}  {k['uuid']}")
        vids = await conn.fetch("SELECT uuid, shot_number FROM video_generations WHERE thread_id = $1 ORDER BY shot_number LIMIT 3", tid)
        print(f"videos (first 3):")
        for v in vids:
            print(f"  shot_{v['shot_number']}  {v['uuid']}")
asyncio.run(c())
