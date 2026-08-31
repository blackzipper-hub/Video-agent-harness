#!/usr/bin/env python3
"""Live smoke for seedance-mv media spine + WaveSpeed Seedance 2.0.

Usage (do NOT commit keys):
  WAVESPEED_API_KEY=... MEDIA_SERVICE_URL=http://127.0.0.1:18080 \\
    python scripts/live_mv_smoke.py

Env:
  AUDIO_URL   default http://127.0.0.1:8765/song.mp3
  SKIP_WAVESPEED=1 to only exercise media analyze/trim/mix
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# agent package root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("MEDIA_SERVICE_URL", "http://127.0.0.1:18080")
os.environ.setdefault("ENVIRONMENT", "development")


def _redact(url: str) -> str:
    if not url:
        return url
    if len(url) > 96:
        return url[:72] + "…" + url[-16:]
    return url


async def run_media_spine(audio_url: str) -> dict:
    from app.chat.v2.host_gateway import HostGateway

    gw = HostGateway()
    print("\n=== 1) media.audio_analyze ===")
    audiomap = await gw.media_audio_analyze({
        "audio_url": audio_url,
        "target_duration_sec": 30.0,
    })
    print(json.dumps({
        "method": audiomap["method"],
        "master": audiomap["master"],
        "segment_count": audiomap["segment_count"],
        "segments": audiomap["segments"],
    }, ensure_ascii=False, indent=2))

    print("\n=== 2) media.audio_trim (each segment + master) ===")
    seg_urls = []
    for seg in audiomap["segments"]:
        clipped = await gw.media_audio_trim({
            "audio_url": audio_url,
            "start": seg["start_sec"],
            "duration": seg["duration_sec"],
        })
        seg_urls.append(clipped["uri"])
        print(f"  seg{seg['index']}: {seg['duration_sec']}s -> {_redact(clipped['uri'])}")

    master = await gw.media_audio_trim({
        "audio_url": audio_url,
        "start": audiomap["master"]["start_sec"],
        "duration": audiomap["master"]["duration_sec"],
        "fade_in_sec": 0.2,
        "fade_out_sec": 0.4,
    })
    print(f"  master: {_redact(master['uri'])}")

    print("\n=== 3) media create-placeholder + concat + mix_audio replace ===")
    import httpx

    media_base = os.environ["MEDIA_SERVICE_URL"].rstrip("/")
    async with httpx.AsyncClient(timeout=120.0) as client:
        clips = []
        for i, dur in enumerate((5, 5), start=1):
            resp = await client.post(
                f"{media_base}/api/v1/video/create-placeholder",
                json={
                    "duration": dur,
                    "width": 768,
                    "height": 1280,
                    "fps": 24,
                    "run_id": f"mv-live-ph-{i}",
                },
            )
            resp.raise_for_status()
            clips.append(resp.json()["result_url"])
            print(f"  placeholder{i}: {_redact(clips[-1])}")
    concat = await gw.media_concat({
        "video_urls": clips,
        "normalize": True,
        "transition_duration": 0.0,
    })
    print(f"  concat: {_redact(concat['uri'])} count={concat.get('video_count')}")

    mixed = await gw.media_mix_audio({
        "video_url": concat["uri"],
        "audio_url": master["uri"],
        "mode": "replace",
    })
    print(f"  mixed: {_redact(mixed['uri'])} mode={mixed['mode']}")

    return {
        "audiomap": audiomap,
        "seg_urls": seg_urls,
        "master_url": master["uri"],
        "mixed_url": mixed["uri"],
    }


async def run_wavespeed(seg_audio_url: str | None) -> dict:
    key = os.environ.get("WAVESPEED_API_KEY", "").strip()
    if not key:
        raise SystemExit("WAVESPEED_API_KEY missing")
    if key.startswith("wsk_"):
        print(f"\n=== 4) WaveSpeed Seedance 2.0 (key=wsk_…{key[-6:]}) ===")
    else:
        print("\n=== 4) WaveSpeed Seedance 2.0 ===")

    # Public refs WaveSpeed can fetch (not localhost).
    # Short Wikimedia commons samples.
    public_image = os.environ.get(
        "PUBLIC_IMAGE_URL",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/"
        "Camponotus_flavomarginatus_ant.jpg/320px-Camponotus_flavomarginatus_ant.jpg",
    )
    public_audio = os.environ.get(
        "PUBLIC_AUDIO_URL",
        "https://upload.wikimedia.org/wikipedia/commons/transcoded/8/8b/"
        "En-us-word-audio.ogg/En-us-word-audio.ogg.mp3",
    )

    from app.integrations.providers.provider_bridge import generate_video

    prompt = (
        "动漫风格角色在霓虹舞台上随音乐律动，镜头慢推，"
        "转场与音乐节奏卡点，背景BGM参考 @音频1，"
        "画面张力强，禁止写实真人脸。"
    )
    profile = {
        "prompt": prompt,
        "duration": 5,
        "resolution": "720p",
        "aspect_ratio": "9:16",
        "generate_audio": True,
        "provider": "wavespeed",
        "model": "doubao-seedance-2-0",
        "images": [public_image],
        "audios": [public_audio],
        # Force T2V+refs path (not i2v-only).
        "mode": "reference",
    }
    print(f"  image: {_redact(public_image)}")
    print(f"  audio: {_redact(public_audio)}")
    print("  submitting…")
    result = await generate_video(profile)
    out = {
        "provider_used": result.get("provider_used"),
        "uri": result.get("uri") or result.get("video_url") or result.get("result_url"),
        "duration": result.get("duration"),
        "remote_task_id": result.get("remote_task_id") or result.get("raw_task_id"),
        "summary": result.get("summary"),
        "error": result.get("error_message") or result.get("error"),
    }
    print(json.dumps({k: (_redact(v) if k == "uri" and isinstance(v, str) else v)
                      for k, v in out.items()}, ensure_ascii=False, indent=2))
    return out


async def main() -> int:
    # Prefer a public/CDN audio URL — no local http.server required.
    # Default: SoundHelix long track (media service downloads it via port-forward).
    audio_url = os.environ.get(
        "AUDIO_URL",
        "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
    )
    media_url = os.environ.get("MEDIA_SERVICE_URL", "http://127.0.0.1:18080")
    os.environ["MEDIA_SERVICE_URL"] = media_url
    print(f"MEDIA_SERVICE_URL={media_url}")
    print(f"AUDIO_URL={audio_url}")
    print("NOTE: expect media via kubectl port-forward; do not start local uvicorn.")

    media = await run_media_spine(audio_url)
    wavespeed = None
    ws_error = None
    if os.environ.get("SKIP_WAVESPEED") != "1":
        try:
            wavespeed = await run_wavespeed(
                media["seg_urls"][0] if media["seg_urls"] else None
            )
        except Exception as exc:  # noqa: BLE001 — report live failures cleanly
            ws_error = f"{type(exc).__name__}: {exc}"
            print(f"\nWaveSpeed FAILED: {ws_error[:500]}")
    else:
        print("\n=== 4) WaveSpeed skipped (SKIP_WAVESPEED=1) ===")

    ok_media = bool(media.get("mixed_url"))
    ok_ws = True
    if os.environ.get("SKIP_WAVESPEED") != "1":
        ok_ws = bool(wavespeed and (wavespeed.get("uri")) and not wavespeed.get("error")) and not ws_error

    print("\n=== RESULT ===")
    print(f"media_spine={'PASS' if ok_media else 'FAIL'}  wavespeed={'PASS' if ok_ws else 'FAIL/SKIP'}")
    summary_path = Path(tempfile.gettempdir()) / "cuti-mv-live-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "media_ok": ok_media,
                "wavespeed_ok": ok_ws if os.environ.get("SKIP_WAVESPEED") != "1" else None,
                "master_url": media.get("master_url"),
                "mixed_url": media.get("mixed_url"),
                "segment_count": media["audiomap"]["segment_count"],
                "wavespeed_uri": (wavespeed or {}).get("uri"),
                "wavespeed_error": ws_error,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"summary -> {summary_path}")
    return 0 if ok_media and ok_ws else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
