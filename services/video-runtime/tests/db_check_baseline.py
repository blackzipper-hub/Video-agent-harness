import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ENVIRONMENT"] = "local"
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"), override=False)

async def check():
    from app.models.database import init_asyncpg_pool, get_asyncpg_pool
    await init_asyncpg_pool()
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        tid = "efe5af1c-a058-4765-b648-e3e70b4172e5"

        # KF shot 2 versions - join via uuid match
        kf_vers = await conn.fetch("""
            SELECT kv.uuid, kv.version_number, kv.success, kv.t2i_prompt, kv.run_id, kv.created_at
            FROM video_keyframe_versions kv
            JOIN video_keyframes k ON k.id::text = kv.keyframe_id OR k.uuid = kv.keyframe_id
            WHERE k.thread_id = $1 AND k.shot_number = 2
            ORDER BY kv.version_number DESC LIMIT 5
        """, tid)
        print(f"KF shot_2 versions={len(kf_vers)}")
        for v in kf_vers:
            p = str(v["t2i_prompt"])[:80] if v["t2i_prompt"] else "None"
            print(f"  v{v['version_number']} uuid={v['uuid'][:12]} ok={v['success']} run={v['run_id'][:12] if v['run_id'] else 'N'} prompt={p}")

        # VID shot 3 columns + versions
        vid_vers = await conn.fetch("""
            SELECT vv.uuid, vv.version_number, vv.success, vv.run_id, vv.motion_prompt, vv.created_at
            FROM video_generation_versions vv
            JOIN video_generations vg ON vg.id::text = vv.video_generation_id OR vg.uuid = vv.video_generation_id
            WHERE vg.thread_id = $1 AND vg.shot_number = 3
            ORDER BY vv.version_number DESC LIMIT 5
        """, tid)
        print(f"\nVID shot_3 versions={len(vid_vers)}")
        for v in vid_vers:
            p = str(v["motion_prompt"])[:80] if v["motion_prompt"] else "None"
            print(f"  v{v['version_number']} uuid={v['uuid'][:12]} ok={v['success']} run={v['run_id'][:12] if v['run_id'] else 'N'} prompt={p}")

        # Outline
        outl = await conn.fetchrow("SELECT uuid, title FROM video_story_outline WHERE thread_id = $1 LIMIT 1", tid)
        print(f"\nOUTLINE uuid={outl['uuid']} title={outl['title']}")

        # Characters
        chars = await conn.fetch("SELECT uuid, name, selected_version_id, current_version_index FROM video_characters WHERE thread_id = $1 AND type = 'character'", tid)
        print(f"\nCHARS ({len(chars)}):")
        for c in chars:
            print(f"  {c['name']} uuid={c['uuid'][:12]} ver_idx={c['current_version_index']} sel_ver={c['selected_version_id'][:12] if c['selected_version_id'] else 'N'}")

asyncio.run(check())
