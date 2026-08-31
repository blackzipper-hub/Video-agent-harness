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

        # Characters with selected_version_id
        chars = await conn.fetch("SELECT uuid, name, selected_version_id, type FROM video_characters WHERE thread_id = $1", tid)
        print("=== CHARACTERS ===")
        for c2 in chars:
            print(f"  {c2['name']} ({c2['type']}) uuid={c2['uuid'][:12]} sel_ver={c2['selected_version_id'][:12] if c2['selected_version_id'] else 'N'}")

        # Music generations + versions
        mgs = await conn.fetch("SELECT uuid FROM video_music_generations WHERE thread_id = $1", tid)
        print(f"\n=== MUSIC GENERATIONS ({len(mgs)}) ===")
        for mg in mgs:
            vers = await conn.fetch("SELECT uuid, version_number, music_prompt FROM video_music_generation_versions WHERE music_generation_id = $1 ORDER BY version_number", mg["uuid"])
            print(f"  mg={mg['uuid'][:12]} versions={len(vers)}")
            for v in vers:
                p = str(v["music_prompt"])[:60] if v["music_prompt"] else "None"
                print(f"    v{v['version_number']} uuid={v['uuid'][:12]} prompt={p}")

        # KF shot_1 versions (for select_version test)
        kf1 = await conn.fetchrow("SELECT id, uuid FROM video_keyframes WHERE thread_id = $1 AND shot_number = 1 LIMIT 1", tid)
        if kf1:
            kv = await conn.fetch("SELECT uuid, version_number, success FROM video_keyframe_versions WHERE keyframe_id = $1 ORDER BY version_number", str(kf1["id"]))
            print(f"\n=== KF shot_1 VERSIONS ({len(kv)}) ===")
            for v in kv:
                print(f"  v{v['version_number']} uuid={v['uuid'][:12]} ok={v['success']}")

        # VID shot_1 generation_mode + audio_url
        vid1_vers = await conn.fetch("""
            SELECT vv.uuid, vv.version_number, vv.generation_mode, vv.audio_url, vv.success
            FROM video_generation_versions vv
            JOIN video_generations vg ON vg.id::text = vv.video_generation_id OR vg.uuid = vv.video_generation_id
            WHERE vg.thread_id = $1 AND vg.shot_number = 1
            ORDER BY vv.version_number
        """, tid)
        print(f"\n=== VID shot_1 VERSIONS ({len(vid1_vers)}) ===")
        for v in vid1_vers:
            au = str(v["audio_url"])[:50] if v["audio_url"] else "None"
            print(f"  v{v['version_number']} uuid={v['uuid'][:12]} mode={v['generation_mode']} audio={au}")

        # Narrations
        nars = await conn.fetch("SELECT shot_number, audio_url FROM video_narrations WHERE thread_id = $1 ORDER BY shot_number LIMIT 3", tid)
        print(f"\n=== NARRATIONS ({len(nars)}) ===")
        for n in nars:
            print(f"  shot_{n['shot_number']} audio={str(n['audio_url'])[:50] if n['audio_url'] else 'None'}")

asyncio.run(c())
