#!/usr/bin/env python3
"""Full real MV e2e (port-forward media + WaveSpeed).

Flow:
  analyze → trim segments + master → Seedance seg0 → extract_frame last
  → Seedance seg1 (first-frame) → concat → mix_audio replace master
  → ffprobe expectations

No local media/http.server. Requires:
  kubectl -n dev port-forward svc/cuti-media-service 18080:8080
  WAVESPEED_API_KEY=...
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("MEDIA_SERVICE_URL", "http://127.0.0.1:18080")
os.environ.setdefault("ENVIRONMENT", "development")


def _short(url: str, n: int = 88) -> str:
    if not isinstance(url, str):
        return str(url)
    return url if len(url) <= n else url[: n - 1] + "…"


def _ffprobe(url: str) -> dict:
    tmp = Path(tempfile.gettempdir()) / f"mv-e2e-{abs(hash(url)) % 10_000_000}{Path(url).suffix or '.bin'}"
    urllib.request.urlretrieve(url, tmp)
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration,size:stream=codec_type,codec_name,duration",
            "-of", "json", str(tmp),
        ],
        text=True,
    )
    data = json.loads(out)
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    types = {s.get("codec_type") for s in streams}
    return {
        "duration": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or 0),
        "has_video": "video" in types,
        "has_audio": "audio" in types,
        "codecs": [(s.get("codec_type"), s.get("codec_name")) for s in streams],
        "local": str(tmp),
    }


async def main() -> int:
    from app.chat.v2.host_gateway import HostGateway
    from app.integrations.providers.provider_bridge import generate_video

    if not os.environ.get("WAVESPEED_API_KEY"):
        raise SystemExit("WAVESPEED_API_KEY required")

    audio_url = os.environ.get(
        "AUDIO_URL",
        "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
    )
    image_url = os.environ.get(
        "PUBLIC_IMAGE_URL",
        "https://d1q70pf5vjeyhc.cloudfront.net/media/90033be53413492fb8f6ae3d3c11b491/"
        "images/1786530275109776263_kKU3aktD.png",
    )
    target = float(os.environ.get("TARGET_DURATION_SEC", "30"))
    # Keep e2e cost/time bounded: 2 segments × ~8s (still exercises multi-seg path)
    max_seg = float(os.environ.get("MAX_SEGMENT_SEC", "8"))
    resolution = os.environ.get("RESOLUTION", "720p")

    gw = HostGateway()
    report: dict = {"steps": [], "checks": [], "ok": True}

    def step(name: str, **payload):
        print(f"\n=== {name} ===")
        for k, v in payload.items():
            if isinstance(v, str) and v.startswith("http"):
                print(f"  {k}: {_short(v)}")
            else:
                print(f"  {k}: {v}")
        report["steps"].append({"name": name, **payload})

    def check(name: str, cond: bool, detail: str = ""):
        status = "PASS" if cond else "FAIL"
        print(f"  CHECK {status}: {name} {detail}")
        report["checks"].append({"name": name, "ok": cond, "detail": detail})
        if not cond:
            report["ok"] = False

    # 1) analyze
    audiomap = await gw.media_audio_analyze({
        "audio_url": audio_url,
        "target_duration_sec": target,
        "max_segment_sec": max_seg,
    })
    segs = audiomap["segments"]
    master = audiomap["master"]
    step(
        "1.analyze",
        method=audiomap["method"],
        master=master,
        segment_count=audiomap["segment_count"],
        segments=segs,
    )
    check("master≈target", abs(master["duration_sec"] - target) < 0.05, f"{master['duration_sec']}")
    check("segments>=2", len(segs) >= 2, str(len(segs)))
    check(
        "each_seg<=max",
        all(s["duration_sec"] <= max_seg + 1e-3 for s in segs),
        str([s["duration_sec"] for s in segs]),
    )
    check(
        "segments_tile_master",
        abs(sum(s["duration_sec"] for s in segs) - master["duration_sec"]) < 0.05,
        f"sum={sum(s['duration_sec'] for s in segs)}",
    )

    # 2) trim segments + master
    seg_audios = []
    for seg in segs:
        clipped = await gw.media_audio_trim({
            "audio_url": audio_url,
            "start": seg["start_sec"],
            "duration": seg["duration_sec"],
        })
        seg_audios.append(clipped["uri"])
        meta = _ffprobe(clipped["uri"])
        step(f"2.trim_seg{seg['index']}", uri=clipped["uri"], probe=meta)
        check(
            f"seg{seg['index']}_dur",
            abs(meta["duration"] - seg["duration_sec"]) < 0.35,
            f"got={meta['duration']:.2f} want={seg['duration_sec']}",
        )
        check(f"seg{seg['index']}_audio", meta["has_audio"], str(meta["codecs"]))

    master_clip = await gw.media_audio_trim({
        "audio_url": audio_url,
        "start": master["start_sec"],
        "duration": master["duration_sec"],
        "fade_in_sec": 0.15,
        "fade_out_sec": 0.3,
    })
    master_meta = _ffprobe(master_clip["uri"])
    step("2.trim_master", uri=master_clip["uri"], probe=master_meta)
    check(
        "master_dur",
        abs(master_meta["duration"] - master["duration_sec"]) < 0.35,
        f"got={master_meta['duration']:.2f}",
    )

    # 3) Seedance multi-seg
    video_urls: list[str] = []
    last_frame = None
    for i, seg in enumerate(segs):
        dur = max(4, min(15, int(round(seg["duration_sec"]))))
        prompt = (
            f"动漫风格角色在霓虹舞台上随音乐律动，第{i + 1}段/"
            f"共{len(segs)}段。镜头慢推，转场与音乐节奏卡点，"
            f"背景BGM与节奏参考 @音频1，画面张力强，禁止写实真人脸。"
        )
        images = [image_url]
        if last_frame:
            # continuation: last frame as first-frame style ref (T2V refs path)
            images = [last_frame, image_url]
        profile = {
            "prompt": prompt,
            "duration": dur,
            "resolution": resolution,
            "aspect_ratio": "9:16",
            "generate_audio": True,
            "provider": "wavespeed",
            "model": "doubao-seedance-2-0",
            "images": images,
            "audios": [seg_audios[i]],
            "mode": "reference",
        }
        step(f"3.seedance_seg{i}", duration=dur, audio=seg_audios[i], images=images)
        print("  submitting…")
        result = await generate_video(profile)
        uri = result.get("uri") or result.get("video_url") or result.get("result_url")
        if not uri:
            step(f"3.seedance_seg{i}_FAILED", result=str(result)[:500])
            check(f"seedance_seg{i}", False, "no uri")
            break
        vmeta = _ffprobe(uri)
        video_urls.append(uri)
        step(f"3.seedance_seg{i}_done", uri=uri, probe=vmeta, task=result.get("raw_task_id"))
        check(f"seedance_seg{i}_video", vmeta["has_video"], str(vmeta["codecs"]))
        check(
            f"seedance_seg{i}_dur",
            abs(vmeta["duration"] - dur) < 1.5,
            f"got={vmeta['duration']:.2f} want={dur}",
        )

        if i < len(segs) - 1:
            try:
                frame = await gw.media_extract_frame({
                    "video_url": uri,
                    "position": "last",
                    "format": "png",
                })
                last_frame = (
                    frame.get("uri")
                    or frame.get("result_url")
                    or frame.get("image_url")
                )
                step(
                    "3.extract_frame_last",
                    uri=last_frame,
                    timestamp=frame.get("timestamp"),
                    via="media.extract_frame",
                )
            except Exception as exc:  # noqa: BLE001
                # Deployed media may not yet expose /video/extract-frame.
                last_frame = await _extract_last_frame_fallback(uri)
                step(
                    "3.extract_frame_last",
                    uri=last_frame,
                    via="ffmpeg+wavespeed_upload_fallback",
                    error=str(exc)[:200],
                )
            check("extract_frame_last", bool(last_frame), _short(str(last_frame)))

    if len(video_urls) != len(segs):
        report["ok"] = False
        _write(report)
        return 1

    # 4) concat
    concat = await gw.media_concat({
        "video_urls": video_urls,
        "normalize": True,
        "transition_duration": 0.125,
    })
    concat_uri = concat["uri"]
    cmeta = _ffprobe(concat_uri)
    expected_concat = sum(max(4, min(15, int(round(s["duration_sec"])))) for s in segs)
    # short crossfade shortens a bit
    step("4.concat", uri=concat_uri, probe=cmeta, expected_about=expected_concat)
    check("concat_video", cmeta["has_video"], str(cmeta["codecs"]))
    check(
        "concat_dur_ballpark",
        abs(cmeta["duration"] - expected_concat) < 3.0,
        f"got={cmeta['duration']:.2f} want~{expected_concat}",
    )

    # 5) mix master replace
    mixed = await gw.media_mix_audio({
        "video_url": concat_uri,
        "audio_url": master_clip["uri"],
        "mode": "replace",
    })
    mixed_uri = mixed["uri"]
    mmeta = _ffprobe(mixed_uri)
    step("5.mix_replace", uri=mixed_uri, probe=mmeta, mode=mixed.get("mode"))
    check("mixed_has_av", mmeta["has_video"] and mmeta["has_audio"], str(mmeta["codecs"]))
    # replace uses -shortest: final ≈ min(video, master)
    expect_final = min(cmeta["duration"], master_meta["duration"])
    check(
        "mixed_dur≈min(video,master)",
        abs(mmeta["duration"] - expect_final) < 1.0,
        f"got={mmeta['duration']:.2f} expect~{expect_final:.2f}",
    )
    # Music-spine: video timeline should match master window (within tolerance).
    check(
        "music_spine_video≈master",
        abs(cmeta["duration"] - master_meta["duration"]) < 2.5,
        (
            f"video={cmeta['duration']:.2f}s master={master_meta['duration']:.2f}s "
            f"final={mmeta['duration']:.2f}s"
        ),
    )
    check(
        "final≈master",
        abs(mmeta["duration"] - master_meta["duration"]) < 2.5,
        f"final={mmeta['duration']:.2f} master={master_meta['duration']:.2f}",
    )

    report["artifacts"] = {
        "master_audio": master_clip["uri"],
        "segment_audios": seg_audios,
        "segment_videos": video_urls,
        "concat_video": concat_uri,
        "final_mixed": mixed_uri,
    }
    _write(report)
    print("\n=== FINAL ===")
    print("OVERALL", "PASS" if report["ok"] else "FAIL")
    failed = [c for c in report["checks"] if not c["ok"]]
    if failed:
        print("Failed checks:")
        for c in failed:
            print(f"  - {c['name']}: {c['detail']}")
    print("final:", _short(mixed_uri, 120))
    return 0 if report["ok"] else 1


def _write(report: dict) -> None:
    path = Path(tempfile.gettempdir()) / "cuti-mv-e2e-full.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nreport -> {path}")


async def _extract_last_frame_fallback(video_url: str) -> str:
    """When media service lacks extract-frame, pull last frame locally and upload to WaveSpeed."""
    import aiohttp

    key = os.environ.get("WAVESPEED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("WAVESPEED_API_KEY required for extract-frame fallback upload")

    tmp_dir = Path(tempfile.mkdtemp(prefix="mv-frame-"))
    video_path = tmp_dir / "clip.mp4"
    frame_path = tmp_dir / "last.png"
    urllib.request.urlretrieve(video_url, video_path)
    # Seek near end; -sseof works for most mp4s from Seedance.
    cmd = [
        "ffmpeg", "-y", "-sseof", "-0.05", "-i", str(video_path),
        "-frames:v", "1", "-q:v", "2", str(frame_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not frame_path.exists():
        # fallback: use duration-0.1 timestamp
        probe = _ffprobe(video_url)
        ts = max(0.0, probe["duration"] - 0.1)
        cmd = [
            "ffmpeg", "-y", "-ss", str(ts), "-i", str(video_path),
            "-frames:v", "1", str(frame_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not frame_path.exists():
            raise RuntimeError(f"ffmpeg last-frame failed: {proc.stderr[-400:]}")

    form = aiohttp.FormData()
    form.add_field(
        "file",
        frame_path.read_bytes(),
        filename="last.png",
        content_type="image/png",
    )
    headers = {"Authorization": f"Bearer {key}"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.wavespeed.ai/api/v3/media/upload/binary",
            headers=headers,
            data=form,
        ) as resp:
            body = await resp.json()
            if resp.status != 200 or body.get("code") not in (None, 200):
                raise RuntimeError(f"wavespeed upload failed: {body}")
            data = body.get("data") or {}
            url = data.get("download_url") or data.get("url")
            if not url:
                raise RuntimeError(f"wavespeed upload missing url: {body}")
            return url


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
