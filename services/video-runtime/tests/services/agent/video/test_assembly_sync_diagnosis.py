"""
诊断 assembly c743421e 的音视频同步漂移和黑帧问题。

核心问题假设：
1. 黑帧: shot 时长 (整数秒, 如 8.0s) < 音频 segment 时长 (如 8.04s)，
   pad_or_trim 补了黑色尾帧来对齐。
2. 同步漂移: 各 segment 时长之和 (203.45s) ≠ 完整音频时长 (202.50s)，
   累积漂移导致后半段越来越明显。
"""
import asyncio
import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import asyncpg
import httpx
import pytest

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ASSEMBLY_UUID = "c743421e-f285-4e26-a5a7-02ad2f65a0e5"
STORY_OUTLINE_ID = "0d581c3d-fee9-40d2-9f59-2c5be58e2567"

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


async def probe_video_info(client: httpx.AsyncClient, video_url: str) -> Optional[dict]:
    try:
        resp = await client.post(
            f"{MEDIA_SERVICE_URL}/api/v1/video/info",
            json={"video_url": video_url},
            timeout=30.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"video/info failed for {video_url[:60]}...: {e}")
    return None


async def probe_audio_info(client: httpx.AsyncClient, audio_url: str) -> Optional[dict]:
    try:
        resp = await client.post(
            f"{MEDIA_SERVICE_URL}/api/v1/audio/info",
            json={"audio_url": audio_url},
            timeout=30.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"audio/info failed: {e}")
    return None


@pytest.mark.asyncio
async def test_assembly_sync_diagnosis():
    pool = await get_db_pool()
    try:
        await _run_diagnosis(pool)
    finally:
        await pool.close()


async def _run_diagnosis(pool):
    # ── 1. Assembly info ──
    assembly = await pool.fetchrow(
        "SELECT * FROM video_assemblies WHERE uuid = $1", ASSEMBLY_UUID
    )
    assert assembly, f"Assembly {ASSEMBLY_UUID} not found"
    conv_id = assembly["conversation_id"]
    thread_id = assembly["thread_id"]

    logger.info(f"\n{'='*90}")
    logger.info(f"ASSEMBLY: {assembly['uuid']}")
    logger.info(f"  final_video_url: {str(assembly.get('final_video_url', ''))[:80]}...")
    logger.info(f"  total_duration (DB): {assembly.get('total_duration')}s")
    logger.info(f"  assembly_mode: {assembly.get('assembly_mode')}")
    logger.info(f"  conversation_id: {conv_id}, thread_id: {thread_id}")

    # ── 2. Audio transcription + segments ──
    atx = await pool.fetchrow(
        """SELECT uuid, duration, audio_url FROM video_audio_transcription
           WHERE conversation_id = $1 AND thread_id = $2
           ORDER BY created_at DESC LIMIT 1""",
        conv_id, thread_id,
    )
    assert atx, "Audio transcription not found"
    complete_audio_url = atx["audio_url"]
    complete_audio_duration = float(atx["duration"])

    audio_segs = await pool.fetch(
        """SELECT uuid, segment_id, start, "end", duration
           FROM video_audio_segment
           WHERE transcription_uuid = $1 ORDER BY segment_id""",
        atx["uuid"],
    )

    sum_audio_seg_dur = sum(float(s["duration"]) for s in audio_segs)
    sum_audio_seg_range = sum(float(s["end"]) - float(s["start"]) for s in audio_segs)

    logger.info(f"\n{'='*90}")
    logger.info("AUDIO ANALYSIS:")
    logger.info(f"  Complete audio duration (DB):    {complete_audio_duration:.4f}s")
    logger.info(f"  Sum(audio_segment.duration):     {sum_audio_seg_dur:.4f}s")
    logger.info(f"  Sum(audio_segment.end - start):  {sum_audio_seg_range:.4f}s")
    logger.info(f"  GAP (sum_dur - complete_dur):    {sum_audio_seg_dur - complete_audio_duration:.4f}s")
    logger.info(f"  Per-segment average excess:      {(sum_audio_seg_dur - complete_audio_duration) / len(audio_segs):.4f}s")

    logger.info(f"\n  {'Seg':>4} {'Start':>8} {'End':>8} {'End-Start':>10} {'Duration':>10} {'Excess':>8}")
    logger.info(f"  {'-'*60}")
    for s in audio_segs:
        rng = float(s["end"]) - float(s["start"])
        dur = float(s["duration"])
        excess = dur - rng
        logger.info(
            f"  {s['segment_id']:>4} {float(s['start']):>8.3f} {float(s['end']):>8.3f} "
            f"{rng:>10.3f} {dur:>10.3f} {excess:>+8.3f}"
        )

    # ── 3. Video segments + versions ──
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

    # ── 4. Collect all VGV ids for shot duration lookup ──
    all_vgv_ids: List[str] = []
    for r in seg_rows:
        ids = json.loads(r["video_generation_version_ids"]) if r["video_generation_version_ids"] else []
        all_vgv_ids.extend(ids)

    vgv_dur_map: Dict[str, float] = {}
    if all_vgv_ids:
        vgv_rows = await pool.fetch(
            """SELECT uuid, duration, shot_number, fps, video_url
               FROM video_generation_versions WHERE uuid = ANY($1::text[])""",
            all_vgv_ids,
        )
        for v in vgv_rows:
            vgv_dur_map[v["uuid"]] = float(v["duration"] or 0)

    # ── 5. Per-segment comparison ──
    logger.info(f"\n{'='*90}")
    logger.info("SEGMENT-BY-SEGMENT COMPARISON:")
    logger.info(
        f"  {'Seg':>4} {'Audio Dur':>10} {'Shot Sum':>10} {'Target(DB)':>11} "
        f"{'Pad/Trim':>9} {'Cumul Drift':>12} {'URL Type':>10}"
    )
    logger.info(f"  {'-'*80}")

    total_target_dur = 0.0
    total_shot_dur = 0.0
    total_padding = 0.0
    cumulative_audio_pos = 0.0
    cumulative_video_pos = 0.0
    padding_segments = 0
    black_frame_details = []

    for i, r in enumerate(seg_rows):
        seg_num = r["segment_number"]
        target_dur = float(r["sv_duration"] or 0)
        total_target_dur += target_dur

        vgv_ids = json.loads(r["video_generation_version_ids"]) if r["video_generation_version_ids"] else []
        shot_durs = [vgv_dur_map.get(uid, 0) for uid in vgv_ids]
        shot_sum = sum(shot_durs)
        total_shot_dur += shot_sum

        audio_dur = float(audio_segs[i]["duration"]) if i < len(audio_segs) else 0
        audio_range = float(audio_segs[i]["end"]) - float(audio_segs[i]["start"]) if i < len(audio_segs) else 0

        pad_or_trim = target_dur - shot_sum if shot_sum > 0 else 0
        if pad_or_trim > 0.01:
            total_padding += pad_or_trim
            padding_segments += 1
            black_frame_details.append((seg_num, pad_or_trim, shot_sum, target_dur))

        cumulative_audio_pos += audio_range
        cumulative_video_pos += target_dur
        drift = cumulative_video_pos - cumulative_audio_pos

        url = str(r["video_url"] or "")
        url_type = "lipsync" if "lipsync" in url else "seg"

        pad_str = f"{pad_or_trim:>+9.3f}" if shot_sum > 0 else f"{'N/A':>9}"
        logger.info(
            f"  {seg_num:>4} {audio_dur:>10.3f} {shot_sum:>10.3f} {target_dur:>11.3f} "
            f"{pad_str} {drift:>+12.3f} {url_type:>10}"
        )

    logger.info(f"  {'-'*80}")
    logger.info(f"  {'TOTAL':>4} {sum_audio_seg_dur:>10.3f} {total_shot_dur:>10.3f} {total_target_dur:>11.3f}")

    # ── 6. Probe actual video metadata ──
    logger.info(f"\n{'='*90}")
    logger.info("PROBING ACTUAL VIDEO METADATA (via media service)...")

    media_service_ok = False
    async with httpx.AsyncClient() as client:
        # Check media service availability
        try:
            test_resp = await client.get(f"{MEDIA_SERVICE_URL}/health", timeout=5.0)
            media_service_ok = test_resp.status_code == 200
        except Exception:
            pass

        if not media_service_ok:
            logger.warning("⚠️ Media service not reachable, skipping video probes")
        else:
            # Probe audio
            if complete_audio_url:
                ainfo = await probe_audio_info(client, complete_audio_url)
                if ainfo:
                    logger.info(f"  Complete audio actual duration: {ainfo.get('duration', 'N/A')}s")

            logger.info(
                f"\n  {'Seg':>4} {'DB Dur':>9} {'Actual Dur':>11} {'Diff':>8} "
                f"{'FPS':>6} {'Res':>12} {'Codec':>8} {'Audio':>6} {'PxFmt':>10}"
            )
            logger.info(f"  {'-'*90}")

            fps_set = set()
            res_set = set()
            codec_set = set()
            pix_fmt_set = set()
            total_actual_dur = 0.0

            for r in seg_rows:
                seg_num = r["segment_number"]
                db_dur = float(r["sv_duration"] or 0)
                video_url = r["video_url"]

                if not video_url:
                    continue

                vinfo = await probe_video_info(client, video_url)
                if vinfo:
                    actual_dur = float(vinfo.get("duration", 0))
                    fps = vinfo.get("fps", 0)
                    width = vinfo.get("width", 0)
                    height = vinfo.get("height", 0)
                    codec = vinfo.get("codec", "?")
                    has_audio = vinfo.get("has_audio", False)
                    pix_fmt = vinfo.get("pix_fmt", "?")

                    diff = actual_dur - db_dur
                    total_actual_dur += actual_dur
                    fps_set.add(fps)
                    res_set.add(f"{width}x{height}")
                    codec_set.add(codec)
                    pix_fmt_set.add(pix_fmt)

                    warn = ""
                    if abs(diff) > 0.05:
                        warn += " DUR_MISMATCH!"
                    if has_audio:
                        warn += " HAS_AUDIO!"

                    logger.info(
                        f"  {seg_num:>4} {db_dur:>9.3f} {actual_dur:>11.3f} {diff:>+8.3f} "
                        f"{fps:>6.1f} {width:>5}x{height:<5} {codec:>8} {str(has_audio):>6} {pix_fmt:>10}"
                        f"{warn}"
                    )
                else:
                    total_actual_dur += db_dur
                    logger.warning(f"  {seg_num:>4} PROBE FAILED")

            logger.info(f"\n  Uniformity:")
            logger.info(f"    FPS:     {fps_set}")
            logger.info(f"    Res:     {res_set}")
            logger.info(f"    Codecs:  {codec_set}")
            logger.info(f"    PxFmt:   {pix_fmt_set}")

            if len(fps_set) > 1:
                logger.error("    ❌ FPS 不一致! -c copy concat 会导致时间戳漂移")
            if len(res_set) > 1:
                logger.error("    ❌ 分辨率不一致!")
            if len(codec_set) > 1:
                logger.error("    ❌ 编码器不一致!")

    # ── 7. Summary ──
    logger.info(f"\n{'='*90}")
    logger.info("DIAGNOSIS SUMMARY")
    logger.info("=" * 90)

    logger.info(f"""
┌─────────────────────────────────────────────────────────────────────────┐
│ 问题 1: 黑帧 (Black frames at segment boundaries)                      │
├─────────────────────────────────────────────────────────────────────────┤
│ {padding_segments}/{len(seg_rows)} 个 segment 有黑色尾帧填充                                     │
│ 总填充时长: {total_padding:.3f}s                                               │
│                                                                         │
│ 原因: 视频生成时长是整数秒 (5s, 8s)，音频 segment 有小数部分 (5.04s, 8.04s)│
│ pad_or_trim 用黑帧填满差值，产生每段末尾 ~0.024-0.044s 黑帧            │
│                                                                         │
│ 黑帧分布:                                                               │""")
    for seg_num, pad, shot_sum, target in black_frame_details[:10]:
        logger.info(f"│   Segment {seg_num:>2}: shot={shot_sum:.1f}s → target={target:.3f}s → pad {pad:.3f}s 黑帧{'':>10}│")
    if len(black_frame_details) > 10:
        logger.info(f"│   ... and {len(black_frame_details) - 10} more segments{'':>40}│")

    logger.info(f"""├─────────────────────────────────────────────────────────────────────────┤
│ 修复建议:                                                               │
│ 1. 用最后一帧冻结 (freeze-frame) 代替黑帧填充                          │
│ 2. 或直接用 end-start 作为 target_duration，而非 duration 字段          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ 问题 2: 累积漂移 (Cumulative audio-video sync drift)                    │
├─────────────────────────────────────────────────────────────────────────┤
│ 完整音频时长:         {complete_audio_duration:>10.3f}s                               │
│ sum(audio_seg.end-start): {sum_audio_seg_range:>10.3f}s (= 音频时间线)                │
│ sum(audio_seg.duration):  {sum_audio_seg_dur:>10.3f}s (= 视频 target_duration 来源)  │
│ sum(video target_dur):    {total_target_dur:>10.3f}s (= 实际视频时长)                 │
│                                                                         │
│ 每个 audio_segment.duration 比 (end-start) 多 ~0.024-0.044s            │
│ 28 个 segment 累积: {sum_audio_seg_dur - sum_audio_seg_range:.3f}s 的额外时长                           │
│                                                                         │
│ 这意味着视频在第 N 个 segment 处比音频偏后 ~N*0.034s                    │
│ 到第 14 个 segment (中间): ~0.48s 漂移                                  │
│ 到第 28 个 segment (结尾): ~0.95s 漂移                                  │
├─────────────────────────────────────────────────────────────────────────┤
│ 修复建议:                                                               │
│ 1. ★ 根本修复: 使用 (end - start) 作为视频 segment 的 target_duration   │
│    而不是 audio_segment.duration 字段                                   │
│ 2. 或在音频分段后确保 sum(duration) == complete_audio.duration          │
│ 3. 或在最终 concat 后对齐到完整音频时长 (speed_adjust)                  │
└─────────────────────────────────────────────────────────────────────────┘""")


if __name__ == "__main__":
    asyncio.run(_run_standalone())


async def _run_standalone():
    pool = await get_db_pool()
    try:
        await _run_diagnosis(pool)
    finally:
        await pool.close()
