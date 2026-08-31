"""Bridge OpenMontage tool names to Cuti Media / provider host capabilities."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.chat.v2.host_gateway import HostGateway, HostGatewayError


CALLABLE_TOOLS = frozenset({
    "video_trimmer",
    "video_stitch",
    "frame_sampler",
    "seedance_video",
    "video_selector",
    "list",
})

FILTERED_REASONS: dict[str, str] = {
    "remotion": "Node Remotion runtime not in Cuti",
    "hyperframes_compose": "HyperFrames runtime not in Cuti",
    "whisperx": "Local STT/GPU not shipped",
    "lip_sync": "Avatar GPU / third-party lip-sync not wired",
    "talking_head": "Avatar pipeline not in product",
    "comfyui_video": "ComfyUI worker absent",
    "comfyui_image": "ComfyUI worker absent",
    "pexels_video": "Stock API / policy not enabled",
    "pixabay_video": "Stock API / policy not enabled",
    "tts_selector": "Use Cuti audio/music capabilities instead",
    "music_gen": "Use Cuti music.generate instead",
    "upscale": "Enhancement models not in Media",
    "face_restore": "Enhancement models not in Media",
    "bg_remove": "Enhancement models not in Media",
    "scene_detect": "Analysis host capability missing",
    "sora_video": "Prompting kept; execute via provider.generate",
    "veo_video": "Prompting kept; execute via provider.generate",
    "kling_video": "Prompting kept; execute via provider.generate",
}


def catalog() -> dict[str, Any]:
    return {
        "ok": True,
        "callable": sorted(t for t in CALLABLE_TOOLS if t != "list"),
        "filtered_examples": FILTERED_REASONS,
        "hint": "Pass tool=<name> and inputs={...} to open_montage.tool.invoke",
    }


def _url(inputs: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = inputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def invoke_om_tool(
    tool: str,
    inputs: dict[str, Any] | None = None,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    tool = str(tool or "").strip()
    inputs = dict(inputs or {})
    if tool in {"list", ""}:
        return catalog()
    if tool not in CALLABLE_TOOLS:
        reason = FILTERED_REASONS.get(tool)
        if reason is None:
            for key, val in FILTERED_REASONS.items():
                if tool.startswith(key):
                    reason = val
                    break
        raise HostGatewayError(
            f"OpenMontage tool {tool!r} is not callable on Cuti"
            + (f": {reason}" if reason else " (see open-montage COMPAT.md)")
        )

    gateway = HostGateway()
    rid = run_id or f"om-{uuid4().hex[:12]}"

    if tool == "video_trimmer":
        return await _video_trimmer(gateway, inputs, rid)
    if tool == "video_stitch":
        return await _video_stitch(gateway, inputs, rid)
    if tool == "frame_sampler":
        return await _frame_sampler(gateway, inputs, rid)
    if tool in {"seedance_video", "video_selector"}:
        return await _seedance(gateway, inputs)
    raise HostGatewayError(f"unhandled tool: {tool}")


async def _video_trimmer(gateway: HostGateway, inputs: dict[str, Any], run_id: str) -> dict[str, Any]:
    op = str(inputs.get("operation") or "").strip()
    if op == "concat":
        urls = inputs.get("video_urls") or inputs.get("segments") or []
        if isinstance(urls, list) and urls and isinstance(urls[0], dict):
            urls = [_url(seg, "input_path", "video_url", "uri", "url") for seg in urls]
        urls = [u for u in urls if isinstance(u, str) and u.strip()]
        result = await gateway.media_concat({
            "video_urls": urls,
            "normalize": inputs.get("normalize", True),
            "run_id": run_id,
        })
        result["tool"] = "video_trimmer"
        result["summary"] = f"video_trimmer.concat → {result.get('video_count', 0)} clips"
        return result
    if op == "speed":
        video_url = _url(inputs, "video_url", "input_path", "uri", "url")
        target = inputs.get("target_duration")
        if target is None:
            raise HostGatewayError(
                "video_trimmer speed requires target_duration (Cuti Media); speed_factor-only filtered"
            )
        result = await gateway.media_speed_adjust({
            "video_url": video_url,
            "target_duration": float(target),
            "run_id": run_id,
        })
        result["tool"] = "video_trimmer"
        result["summary"] = "video_trimmer.speed via media.speed_adjust"
        return result
    if op == "cut":
        video_url = _url(inputs, "video_url", "input_path", "uri", "url")
        start = float(inputs.get("start_seconds") or 0)
        if start > 0:
            raise HostGatewayError(
                "video_trimmer cut with start_seconds>0 filtered; Cuti Media trims from t=0 only"
            )
        end = inputs.get("end_seconds")
        target = inputs.get("target_duration")
        if target is None and end is not None:
            target = float(end) - start
        if not video_url or target is None:
            raise HostGatewayError("video_trimmer cut requires video_url and end_seconds/target_duration")
        result = await gateway.media_trim({
            "video_url": video_url,
            "target_duration": float(target),
            "mode": inputs.get("mode") or "trim_only",
            "run_id": run_id,
        })
        result["tool"] = "video_trimmer"
        result["summary"] = "video_trimmer.cut via media.trim"
        return result
    raise HostGatewayError(f"unsupported video_trimmer operation: {op!r}")


async def _video_stitch(gateway: HostGateway, inputs: dict[str, Any], run_id: str) -> dict[str, Any]:
    op = str(inputs.get("operation") or "stitch").strip()
    transition = str(inputs.get("transition") or "cut").strip()
    if transition != "cut":
        raise HostGatewayError(f"video_stitch transition={transition!r} filtered; only cut")
    clips = inputs.get("clips") or inputs.get("video_urls") or []
    urls: list[str] = []
    for item in clips:
        if isinstance(item, str) and item.strip():
            urls.append(item.strip())
        elif isinstance(item, dict):
            u = _url(item, "input_path", "video_url", "uri", "url")
            if u:
                urls.append(u)
    if op == "validate":
        return {"ok": True, "tool": "video_stitch", "video_count": len(urls), "urls": urls,
                "summary": f"validated {len(urls)} clips"}
    if op != "stitch":
        raise HostGatewayError(f"unsupported video_stitch operation: {op!r}")
    result = await gateway.media_concat({
        "video_urls": urls,
        "normalize": inputs.get("normalize", True),
        "run_id": run_id,
    })
    result["tool"] = "video_stitch"
    result["summary"] = f"video_stitch → media.concat ({result.get('video_count', 0)} clips)"
    return result


async def _frame_sampler(gateway: HostGateway, inputs: dict[str, Any], run_id: str) -> dict[str, Any]:
    strategy = str(inputs.get("strategy") or "timestamps").strip()
    if strategy != "timestamps":
        raise HostGatewayError(
            f"frame_sampler strategy={strategy!r} filtered; only timestamps supported"
        )
    video_url = _url(inputs, "video_url", "input_path", "uri", "url")
    timestamps = inputs.get("timestamps")
    if timestamps is None and "timestamp" in inputs:
        timestamps = [inputs["timestamp"]]
    if not video_url or not isinstance(timestamps, list) or not timestamps:
        raise HostGatewayError("frame_sampler requires video_url and timestamps[]")
    frames = []
    for i, ts in enumerate(timestamps):
        frames.append(
            await gateway.media_extract_frame({
                "video_url": video_url,
                "timestamp": float(ts),
                "format": inputs.get("format") or "jpeg",
                "run_id": f"{run_id}-f{i}",
            })
        )
    if len(frames) == 1:
        out = dict(frames[0])
        out["tool"] = "frame_sampler"
        return out
    return {
        "ok": True,
        "tool": "frame_sampler",
        "frames": frames,
        "count": len(frames),
        "uri": frames[0].get("uri"),
        "summary": f"extracted {len(frames)} frames",
    }


async def _seedance(gateway: HostGateway, inputs: dict[str, Any]) -> dict[str, Any]:
    prompt = str(inputs.get("prompt") or "").strip()
    if not prompt:
        raise HostGatewayError("seedance_video requires prompt")
    payload: dict[str, Any] = {
        "prompt": prompt,
        "provider": inputs.get("provider") or "ark",
    }
    for key in ("model", "duration", "resolution", "aspect_ratio", "generate_audio"):
        if key in inputs and inputs[key] is not None:
            payload[key] = inputs[key]
    if inputs.get("ratio") and "aspect_ratio" not in payload:
        payload["aspect_ratio"] = inputs["ratio"]
    for src, dst in (
        ("images", "images"),
        ("reference_images", "images"),
        ("videos", "videos"),
        ("audios", "audios"),
    ):
        val = inputs.get(src)
        if isinstance(val, list) and val:
            payload[dst] = val
    result = await gateway.provider_generate(payload)
    result["tool"] = "seedance_video"
    result["summary"] = (
        f"seedance_video via provider.generate "
        f"({result.get('provider_used') or payload.get('provider')})"
    )
    return result
