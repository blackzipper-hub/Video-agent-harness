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

        # Characters
        chars = await conn.fetch("SELECT uuid, name, selected_version_id, type FROM video_characters WHERE thread_id = $1", tid)
        print(f"=== CHARACTERS ({len(chars)}) ===")
        for c2 in chars:
            sv = c2['selected_version_id'][:12] if c2['selected_version_id'] else 'N'
            print(f"  {c2['name']} ({c2['type']}) uuid={c2['uuid']} sel_ver={sv}")

        # Music (music_prompt is in versions)
        mgs = await conn.fetch("SELECT uuid FROM video_music_generations WHERE thread_id = $1", tid)
        print(f"\n=== MUSIC ({len(mgs)}) ===")
        for mg in mgs[:2]:
            vers = await conn.fetch("SELECT uuid, version_number, music_prompt FROM video_music_generation_versions WHERE music_generation_id = $1 ORDER BY version_number", mg["uuid"])
            for v in vers:
                p = str(v["music_prompt"])[:60] if v["music_prompt"] else "None"
                print(f"  mg={mg['uuid']} v{v['version_number']} ver_uuid={v['uuid']} prompt={p}")

        # KF shot_1 versions
        kfv = await conn.fetch("SELECT uuid, version_number, keyframe_id, success, keyframe_url FROM video_keyframe_versions WHERE thread_id = $1 AND shot_number = 1 ORDER BY version_number", tid)
        print(f"\n=== KF shot_1 VERSIONS ({len(kfv)}) ===")
        for v in kfv:
            u = str(v['keyframe_url'])[:60] if v['keyframe_url'] else 'None'
            print(f"  v{v['version_number']} uuid={v['uuid']} kf_id={v['keyframe_id']} ok={v['success']} url={u}")

        # VID versions
        vids = await conn.fetch("""
            SELECT vg.shot_number, vv.uuid, vv.version_number, vv.generation_mode, vv.audio_url, vv.success, vv.video_generation_id
            FROM video_generation_versions vv
            JOIN video_generations vg ON vg.uuid = vv.video_generation_id
            WHERE vg.thread_id = $1
            ORDER BY vg.shot_number, vv.version_number
        """, tid)
        print(f"\n=== VID VERSIONS ({len(vids)}) ===")
        for v in vids:
            au = str(v['audio_url'])[:50] if v['audio_url'] else "None"
            print(f"  shot_{v['shot_number']} v{v['version_number']} uuid={v['uuid'][:12]} mode={v['generation_mode']} audio={au}")

        # Outline
        ol = await conn.fetchrow("SELECT uuid, title FROM video_story_outlines WHERE thread_id = $1 ORDER BY created_at DESC LIMIT 1", tid)
        if ol:
            print(f"\n=== OUTLINE ===")
            print(f"  uuid={ol['uuid']} title={ol['title']}")

        # Segments
        segs = await conn.fetch("SELECT uuid, shot_number FROM video_segments WHERE thread_id = $1 ORDER BY shot_number LIMIT 3", tid)
        print(f"\n=== SEGMENTS ({len(segs)}) ===")
        for s in segs:
            print(f"  shot_{s['shot_number']} uuid={s['uuid'][:12]}")

asyncio.run(c())
