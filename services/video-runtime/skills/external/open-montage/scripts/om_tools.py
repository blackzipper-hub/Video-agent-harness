#!/usr/bin/env python3
"""OpenMontage tool list/invoke CLI for Cuti (bridged, not raw OM runtime).

Usage:
  python om_tools.py list
  python om_tools.py invoke <tool_name>

Reads tool inputs from CUTI_INPUT_JSON / input.json:
  {"tool": "video_trimmer", "inputs": {...}}
or when using argv invoke, input.json is the tool inputs object
(with optional "tool" key).

Host dispatch uses CUTI_HOST_* env injected by the sandbox runner.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path


CATALOG_PATH = Path(__file__).with_name("tools_catalog.json")


def _load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _load_input() -> dict:
    path = os.environ.get("CUTI_INPUT_JSON") or "/workspace/input.json"
    p = Path(path)
    if not p.is_file():
        # local fallback next to cwd
        p = Path("input.json")
    if not p.is_file():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def host_dispatch(capability: str, payload: dict | None = None, timeout: float = 300.0) -> dict:
    url = os.environ.get("CUTI_HOST_GATEWAY_URL")
    if not url:
        raise RuntimeError("CUTI_HOST_GATEWAY_URL is not set (host bridge unavailable)")
    token = os.environ.get("CUTI_HOST_TOKEN", "")
    allowed_capabilities = json.loads(os.environ.get("CUTI_HOST_ALLOWED_CAPABILITIES", "[]"))
    allowed_domains = json.loads(os.environ.get("CUTI_HOST_ALLOWED_DOMAINS", "[]"))
    body = {
        "capability": capability,
        "payload": payload or {},
        "allowed_capabilities": allowed_capabilities,
        "allowed_domains": allowed_domains,
        "skill_name": os.environ.get("CUTI_SKILL_NAME", "open-montage"),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


def cmd_list() -> dict:
    catalog = _load_catalog()
    return {
        "ok": True,
        "callable": catalog.get("callable", {}),
        "filtered": catalog.get("filtered", {}),
        "hint": "Invoke via: python om_tools.py invoke <tool> with inputs in input.json",
    }


def _url(inputs: dict, *keys: str) -> str:
    for key in keys:
        value = inputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def invoke_video_trimmer(inputs: dict) -> dict:
    op = str(inputs.get("operation") or "").strip()
    if op == "concat":
        urls = inputs.get("video_urls") or inputs.get("segments") or []
        if isinstance(urls, list) and urls and isinstance(urls[0], dict):
            urls = [_url(seg, "input_path", "video_url", "uri", "url") for seg in urls]
        urls = [u for u in urls if isinstance(u, str) and u.strip()]
        if len(urls) < 1:
            raise ValueError("video_trimmer concat requires video_urls or segments with URLs")
        return host_dispatch("media.concat", {"video_urls": urls, "normalize": inputs.get("normalize", True)})
    if op == "speed":
        video_url = _url(inputs, "video_url", "input_path", "uri", "url")
        target = inputs.get("target_duration")
        if target is None and inputs.get("speed_factor") is not None:
            raise ValueError(
                "Cuti media.speed_adjust needs target_duration (seconds); "
                "speed_factor-only OM API is not supported"
            )
        if not video_url or target is None:
            raise ValueError("video_trimmer speed requires video_url and target_duration")
        return host_dispatch(
            "media.speed_adjust",
            {"video_url": video_url, "target_duration": float(target)},
        )
    if op == "cut":
        video_url = _url(inputs, "video_url", "input_path", "uri", "url")
        start = float(inputs.get("start_seconds") or 0)
        if start > 0:
            raise ValueError(
                "Cuti Media trim only supports cut from t=0 to target_duration; "
                f"start_seconds={start} is filtered"
            )
        end = inputs.get("end_seconds")
        target = inputs.get("target_duration")
        if target is None and end is not None:
            target = float(end) - start
        if not video_url or target is None:
            raise ValueError("video_trimmer cut requires video_url and end_seconds or target_duration")
        return host_dispatch(
            "media.trim",
            {
                "video_url": video_url,
                "target_duration": float(target),
                "mode": inputs.get("mode") or "trim_only",
            },
        )
    raise ValueError(f"unsupported video_trimmer operation: {op!r}")


def invoke_video_stitch(inputs: dict) -> dict:
    op = str(inputs.get("operation") or "stitch").strip()
    if op not in {"stitch", "validate"}:
        raise ValueError("only video_stitch operation=stitch|validate is bridged")
    transition = str(inputs.get("transition") or "cut").strip()
    if transition != "cut":
        raise ValueError(f"transition={transition!r} filtered; only cut → media.concat")
    clips = inputs.get("clips") or inputs.get("video_urls") or []
    urls = []
    for item in clips:
        if isinstance(item, str):
            urls.append(item.strip())
        elif isinstance(item, dict):
            urls.append(_url(item, "input_path", "video_url", "uri", "url"))
    urls = [u for u in urls if u]
    if op == "validate":
        return {"ok": True, "video_count": len(urls), "urls": urls}
    if len(urls) < 1:
        raise ValueError("video_stitch requires clips/video_urls")
    return host_dispatch("media.concat", {"video_urls": urls, "normalize": inputs.get("normalize", True)})


def invoke_frame_sampler(inputs: dict) -> dict:
    strategy = str(inputs.get("strategy") or "timestamps").strip()
    if strategy != "timestamps":
        raise ValueError(
            f"frame_sampler strategy={strategy!r} filtered; only timestamps → media.extract_frame"
        )
    video_url = _url(inputs, "video_url", "input_path", "uri", "url")
    timestamps = inputs.get("timestamps")
    if timestamps is None and "timestamp" in inputs:
        timestamps = [inputs["timestamp"]]
    if not video_url or not isinstance(timestamps, list) or not timestamps:
        raise ValueError("frame_sampler requires video_url and timestamps[]")
    frames = []
    for ts in timestamps:
        frames.append(
            host_dispatch(
                "media.extract_frame",
                {
                    "video_url": video_url,
                    "timestamp": float(ts),
                    "format": inputs.get("format") or "jpeg",
                },
            )
        )
    if len(frames) == 1:
        return frames[0]
    return {"ok": True, "frames": frames, "count": len(frames)}


def invoke_seedance(inputs: dict) -> dict:
    prompt = str(inputs.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("seedance_video / video_selector requires prompt")
    payload = {
        "prompt": prompt,
        "provider": inputs.get("provider") or "ark",
        "model": inputs.get("model"),
        "duration": inputs.get("duration"),
        "resolution": inputs.get("resolution"),
        "aspect_ratio": inputs.get("aspect_ratio") or inputs.get("ratio"),
        "generate_audio": inputs.get("generate_audio", True),
        "images": inputs.get("images") or inputs.get("reference_images") or [],
        "videos": inputs.get("videos") or [],
        "audios": inputs.get("audios") or [],
    }
    # Drop empty optionals
    payload = {k: v for k, v in payload.items() if v is not None and v != [] and v != ""}
    return host_dispatch("provider.generate", payload)


INVOKERS = {
    "video_trimmer": invoke_video_trimmer,
    "video_stitch": invoke_video_stitch,
    "frame_sampler": invoke_frame_sampler,
    "seedance_video": invoke_seedance,
    "video_selector": invoke_seedance,
}


def cmd_invoke(tool: str, inputs: dict) -> dict:
    catalog = _load_catalog()
    callable_tools = catalog.get("callable") or {}
    filtered = catalog.get("filtered") or {}
    if tool not in callable_tools:
        reason = filtered.get(tool) or filtered.get(f"{tool}_*")
        # fuzzy prefix match
        if reason is None:
            for key, val in filtered.items():
                if key.endswith("*") and tool.startswith(key[:-1]):
                    reason = val
                    break
        raise ValueError(
            f"tool {tool!r} is not callable on Cuti"
            + (f": {reason}" if reason else ". See COMPAT.md / tools_catalog.json filtered")
        )
    fn = INVOKERS[tool]
    result = fn(inputs)
    if not isinstance(result, dict):
        result = {"result": result}
    result.setdefault("ok", True)
    result["tool"] = tool
    result["bridge"] = callable_tools[tool].get("bridge")
    return result


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0
    cmd = argv[0]
    try:
        if cmd == "list":
            out = cmd_list()
        elif cmd == "invoke":
            if len(argv) < 2:
                raise ValueError("usage: om_tools.py invoke <tool_name>")
            tool = argv[1]
            data = _load_input()
            inputs = data.get("inputs") if isinstance(data.get("inputs"), dict) else data
            if isinstance(inputs, dict) and inputs.get("tool") and not argv[1]:
                tool = str(inputs["tool"])
            # Prefer nested inputs when present
            if isinstance(data.get("inputs"), dict):
                inputs = data["inputs"]
            elif "tool" in data:
                inputs = {k: v for k, v in data.items() if k != "tool"}
            out = cmd_invoke(tool, inputs if isinstance(inputs, dict) else {})
        else:
            raise ValueError(f"unknown command: {cmd}")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
