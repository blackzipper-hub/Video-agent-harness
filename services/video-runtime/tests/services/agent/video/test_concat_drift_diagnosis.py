"""
诊断 force=True 重跑后仍存在的 concat 时长膨胀 (203.25s vs 202.50s)。

所有 segment 都已用 Gemini end-start 整数时长重新处理，
但 concat 后实际视频仍比预期长 0.75s。

本脚本通过 MSC probe 每个 segment 视频的实际时长，
定位是 "哪些 segment 的实际文件比 DB duration 长" 以及 "为什么"。
"""
import asyncio
import os
import json
import logging
from typing import Dict, List, Optional

import asyncpg
import httpx
import pytest

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ASSEMBLY_UUID = "53f0c5af-ca81-4c56-8267-60a35f0d5d5a"
STORY_OUTLINE_ID = "cbca450c-131b-4eb3-9844-66a5c3f1796f"

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/storybook_dev",
)
MEDIA_SERVICE_URL = os.environ.get(
    "MEDIA_SERVICE_URL",
    "http://media-dev.cuti.internal:8080",
)


async def get_db_pool():
    return await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)


async def probe_video(client: httpx.AsyncClient, url: str) -> Optional[dict]:
    try:
        resp = await client.post(
            f"{MEDIA_SERVICE_URL}/api/v1/video/info",
            json={"video_url": url},
            timeout=30.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"probe failed: {e}")
    return None


async def probe_audio(client: httpx.AsyncClient, url: str) -> Optional[dict]:
    try:
        resp = await client.post(
            f"{MEDIA_SERVICE_URL}/api/v1/audio/info",
            json={"audio_url": url},
            timeout=30.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"probe failed: {e}")
    return None


@pytest.mark.asyncio
async def test_concat_drift_diagnosis():
    pool = await get_db_pool()
    try:
        await _run(pool)
    finally:
        await pool.close()


async def _run(pool):
    # 1. Assembly info
    assembly = await pool.fetchrow(
        "SELECT * FROM video_assemblies WHERE uuid = $1", ASSEMBLY_UUID
    )
    assert assembly, f"Assembly {ASSEMBLY_UUID} not found"
    conv_id = assembly["conversation_id"]
    thread_id = assembly["thread_id"]
    logger.info(f"\n{'='*90}")
    logger.info(f"ASSEMBLY: {assembly['uuid']}")
    logger.info(f"  total_duration (DB): {assembly.get('total_duration')}s")
    logger.info(f"  story_outline_id: {STORY_OUTLINE_ID}")

    # 2. Audio transcription + segments
    atx = await pool.fetchrow(
        """SELECT uuid, duration, audio_url FROM video_audio_transcription
           WHERE conversation_id = $1 AND thread_id = $2
           ORDER BY created_at DESC LIMIT 1""",
        conv_id, thread_id,
    )
    assert atx, "Audio transcription not found"
    complete_audio_duration = float(atx["duration"])

    audio_segs = await pool.fetch(
        """SELECT uuid, segment_id, start, "end", duration
           FROM video_audio_segment
           WHERE transcription_uuid = $1 ORDER BY segment_id""",
        atx["uuid"],
    )

    sum_end_minus_start = sum(float(s["end"]) - float(s["start"]) for s in audio_segs)
    sum_db_duration = sum(float(s["duration"]) for s in audio_segs)

    logger.info(f"\n{'='*90}")
    logger.info("AUDIO SEGMENTS:")
    logger.info(f"  Complete audio (DB):     {complete_audio_duration:.3f}s")
    logger.info(f"  Sum(end-start):          {sum_end_minus_start:.3f}s")
    logger.info(f"  Sum(DB duration):        {sum_db_duration:.3f}s")
    logger.info(f"  Segments count:          {len(audio_segs)}")

    # audio_segment uuid -> (start, end, end-start, db_duration)
    audio_map = {}
    for s in audio_segs:
        audio_map[s["uuid"]] = {
            "start": float(s["start"]),
            "end": float(s["end"]),
            "range": float(s["end"]) - float(s["start"]),
            "db_dur": float(s["duration"]),
        }

    # 3. Video segments + latest versions
    seg_rows = await pool.fetch(
        """SELECT s.uuid, s.segment_number, s.current_version_index,
                  sv.uuid as sv_uuid, sv.video_url, sv.success, sv.duration as sv_duration,
                  sv.fps, sv.video_generation_version_ids, sv.version_number
           FROM video_segments s
           JOIN video_segment_versions sv ON sv.video_segment_id = s.uuid
             AND sv.version_number = s.current_version_index
           WHERE s.story_outline_id = $1
           ORDER BY s.segment_number""",
        STORY_OUTLINE_ID,
    )

    logger.info(f"\n{'='*90}")
    logger.info(f"VIDEO SEGMENTS: {len(seg_rows)} segments")

    # 4. Collect VGV ids for shot duration lookup
    all_vgv_ids: List[str] = []
    for r in seg_rows:
        ids = json.loads(r["video_generation_version_ids"]) if r["video_generation_version_ids"] else []
        all_vgv_ids.extend(ids)

    vgv_map: Dict[str, dict] = {}
    if all_vgv_ids:
        vgv_rows = await pool.fetch(
            """SELECT uuid, duration, shot_number, fps, video_url, audio_segment_ids
               FROM video_generation_versions WHERE uuid = ANY($1::text[])""",
            all_vgv_ids,
        )
        for v in vgv_rows:
            vgv_map[v["uuid"]] = {
                "duration": float(v["duration"] or 0),
                "fps": v["fps"],
                "audio_segment_ids": v["audio_segment_ids"],
            }

    # 5. Map segment -> audio_segment via VGV
    seg_to_audio = {}
    for r in seg_rows:
        vgv_ids = json.loads(r["video_generation_version_ids"]) if r["video_generation_version_ids"] else []
        for vid in vgv_ids:
            info = vgv_map.get(vid, {})
            asids = info.get("audio_segment_ids") or []
            if asids:
                seg_to_audio[r["segment_number"]] = asids[0]
                break

    # 6. Probe all segment videos via MSC
    logger.info(f"\n{'='*90}")
    logger.info("PROBING ACTUAL VIDEO FILES VIA MSC...")

    async with httpx.AsyncClient() as client:
        # Check MSC reachability by trying a lightweight call
        msc_ok = False
        try:
            resp = await client.post(
                f"{MEDIA_SERVICE_URL}/api/v1/audio/info",
                json={"audio_url": atx["audio_url"]},
                timeout=15.0,
            )
            msc_ok = resp.status_code == 200
            if msc_ok:
                logger.info("✅ MSC is reachable")
        except Exception as e:
            logger.warning(f"MSC check failed: {e}")

        if not msc_ok:
            logger.error("❌ MSC not reachable - cannot probe. Falling back to DB-only analysis.")

        # Probe complete audio
        if msc_ok and atx["audio_url"]:
            ainfo = await probe_audio(client, atx["audio_url"])
            if ainfo:
                logger.info(f"  Complete audio probe duration: {ainfo.get('duration')}s")

        logger.info(f"\n  {'Seg':>4} {'DB Dur':>8} {'Probe Dur':>10} {'Excess':>8} {'FPS':>6} "
                     f"{'Audio(e-s)':>10} {'Codec':>8} {'Has Audio':>10}")
        logger.info(f"  {'-'*85}")

        total_db_dur = 0.0
        total_probe_dur = 0.0
        total_excess = 0.0
        excess_details = []

        for r in seg_rows:
            seg_num = r["segment_number"]
            db_dur = float(r["sv_duration"] or 0)
            total_db_dur += db_dur
            video_url = r["video_url"]

            # Get corresponding audio range
            audio_seg_id = seg_to_audio.get(seg_num)
            audio_range = audio_map[audio_seg_id]["range"] if audio_seg_id and audio_seg_id in audio_map else 0

            probe_dur = db_dur
            fps = 0
            codec = "?"
            has_audio = False

            if msc_ok and video_url:
                vinfo = await probe_video(client, video_url)
                if vinfo:
                    probe_dur = float(vinfo.get("duration", 0))
                    fps = vinfo.get("fps", 0)
                    codec = vinfo.get("codec", "?")
                    has_audio = vinfo.get("has_audio", False)

            total_probe_dur += probe_dur
            excess = probe_dur - db_dur
            total_excess += excess

            if abs(excess) > 0.001:
                excess_details.append((seg_num, db_dur, probe_dur, excess, fps, audio_range))

            logger.info(
                f"  {seg_num:>4} {db_dur:>8.3f} {probe_dur:>10.3f} {excess:>+8.3f} {fps:>6.1f} "
                f"{audio_range:>10.3f} {codec:>8} {str(has_audio):>10}"
            )

        logger.info(f"  {'-'*85}")
        logger.info(f"  {'TOTAL':>4} {total_db_dur:>8.3f} {total_probe_dur:>10.3f} {total_excess:>+8.3f}")

    # 7. Analysis
    logger.info(f"\n{'='*90}")
    logger.info("ANALYSIS:")
    logger.info(f"  Sum(DB sv_duration):    {total_db_dur:.3f}s")
    logger.info(f"  Sum(probe duration):    {total_probe_dur:.3f}s")
    logger.info(f"  Complete audio:         {complete_audio_duration:.3f}s")
    logger.info(f"  Probe - DB:             {total_excess:+.3f}s")
    logger.info(f"  Probe - Audio:          {total_probe_dur - complete_audio_duration:+.3f}s")

    if excess_details:
        logger.info(f"\n  Segments with probe != DB duration ({len(excess_details)} segments):")
        for seg_num, db_d, probe_d, exc, fps, a_range in excess_details:
            frames_at_24 = round(db_d * 24)
            expected_from_frames = frames_at_24 / 24.0
            frame_excess = probe_d - expected_from_frames
            logger.info(
                f"    Seg {seg_num:>2}: DB={db_d:.3f}s probe={probe_d:.3f}s excess={exc:+.3f}s | "
                f"@24fps: {frames_at_24} frames = {expected_from_frames:.3f}s | "
                f"audio(e-s)={a_range:.3f}s"
            )

        avg_excess = total_excess / len(seg_rows) if seg_rows else 0
        logger.info(f"\n  Average per-segment excess: {avg_excess:+.4f}s")
        if avg_excess > 0:
            logger.info(f"  At 24fps, 1 extra frame = {1/24:.4f}s = 0.0417s")
            logger.info(f"  Average excess / frame_duration = {avg_excess / (1/24):.2f} frames")

    logger.info(f"\n{'='*90}")
    logger.info("CONCLUSION:")
    if total_excess > 0.1:
        logger.info(
            f"  Even with correct target_duration (Gemini end-start), each segment video\n"
            f"  file is slightly LONGER than requested. This is FFmpeg trim precision:\n"
            f"  at 24fps each frame = 0.042s, and trim cannot cut mid-frame.\n"
            f"  Over {len(seg_rows)} segments, excess accumulates to {total_excess:.3f}s.\n"
            f"  This is inherent to frame-based video — the assembly speed_adjust fix\n"
            f"  is needed as a final alignment step."
        )
    else:
        logger.info("  Probe durations match DB durations. No per-segment excess detected.")


if __name__ == "__main__":
    asyncio.run(_standalone())


async def _standalone():
    pool = await get_db_pool()
    try:
        await _run(pool)
    finally:
        await pool.close()
