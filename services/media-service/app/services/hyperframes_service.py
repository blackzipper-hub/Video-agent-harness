from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.services.ffmpeg_service import get_video_info


DEFAULT_CLI = "~/.local/hyperframes-runtime/node_modules/.bin/hyperframes"
DEFAULT_NODE_BIN = "~/.local/node-v24.5.0/bin"
STYLE_NAMES = {
    "caption-highlight", "caption-pill-karaoke", "caption-editorial-emphasis",
    "caption-glitch-rgb", "caption-kinetic-slam", "caption-neon-glow",
    "caption-neon-accent", "caption-clip-wipe", "caption-gradient-fill",
    "caption-matrix-decode", "caption-emoji-pop", "caption-parallax-layers",
    "caption-particle-burst", "caption-texture", "caption-weight-shift",
}


def _normalize_words(words: list[dict], cues: list[dict]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in words:
        text = str(item.get("word") or item.get("text") or "").strip()
        try:
            start, end = float(item.get("start")), float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if text and start >= 0 and end > start:
            normalized.append({"text": text, "start": start, "end": end})
    if normalized:
        return sorted(normalized, key=lambda item: (item["start"], item["end"]))
    for cue in cues:
        text = str(cue.get("text") or "").strip()
        try:
            start, end = float(cue.get("start")), float(cue.get("end"))
        except (TypeError, ValueError):
            continue
        tokens = re.findall(r"[\u3400-\u9fff]|[^\s\u3400-\u9fff]+", text)
        if not tokens or end <= start:
            continue
        span = (end - start) / len(tokens)
        normalized.extend(
            {"text": token, "start": start + index * span, "end": start + (index + 1) * span}
            for index, token in enumerate(tokens)
        )
    return normalized


def _groups(words: list[dict[str, Any]], max_words: int = 5) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        if current and (len(current) >= max_words or word["start"] - current[-1]["end"] >= 0.35):
            groups.append({"words": current, "start": current[0]["start"], "end": current[-1]["end"] + 0.12})
            current = []
        current.append(word)
    if current:
        groups.append({"words": current, "start": current[0]["start"], "end": current[-1]["end"] + 0.12})
    for index in range(len(groups) - 1):
        groups[index]["end"] = min(groups[index]["end"], groups[index + 1]["start"] - 0.03)
    return groups


def _sentence_groups(words: list[dict], cues: list[dict]) -> list[dict[str, Any]]:
    """Prefer transcript cues so captions behave like conventional sentence subtitles."""
    groups: list[dict[str, Any]] = []
    for cue in cues:
        text = str(cue.get("text") or "").strip()
        try:
            start, end = float(cue.get("start")), float(cue.get("end"))
        except (TypeError, ValueError):
            continue
        if text and start >= 0 and end > start:
            groups.append({"text": text, "start": start, "end": end})
    if groups:
        return sorted(groups, key=lambda item: (item["start"], item["end"]))

    # Word timestamps remain useful when a provider does not return sentence cues, but
    # group them into readable phrases rather than animating every token independently.
    normalized = _normalize_words(words, [])
    return [
        {
            "text": "".join(word["text"] for word in group["words"])
            if any(re.search(r"[\u3400-\u9fff]", word["text"]) for word in group["words"])
            else " ".join(word["text"] for word in group["words"]),
            "start": group["start"],
            "end": group["end"],
        }
        for group in _groups(normalized)
    ]


def _style_css(style: str, accent: str) -> str:
    extras = {
        "caption-pill-karaoke": "background:rgba(8,8,12,.82);border-radius:999px;padding:18px 28px;",
        "caption-editorial-emphasis": "font-family:'Cuti CJK',serif;font-weight:600;letter-spacing:.01em;",
        "caption-glitch-rgb": "text-shadow:-4px 0 #00e5ff,4px 0 #ff1745,0 5px 18px #000;",
        "caption-kinetic-slam": "font-size:clamp(56px,7vw,118px);text-transform:uppercase;",
        "caption-neon-glow": f"color:#fff;text-shadow:0 0 8px {accent},0 0 24px {accent},0 4px 18px #000;",
        "caption-neon-accent": "background:linear-gradient(90deg,#00e5ff,#ff2bd6,#ffe600);-webkit-background-clip:text;color:transparent;",
        "caption-clip-wipe": "border-left:10px solid var(--accent);padding-left:24px;",
        "caption-gradient-fill": "background:linear-gradient(90deg,#fff,var(--accent));-webkit-background-clip:text;color:transparent;",
        "caption-matrix-decode": "font-family:'Cuti CJK',monospace;color:#75ff84;text-shadow:0 0 14px #00ff44;",
        "caption-emoji-pop": "font-family:'Cuti CJK',sans-serif;",
        "caption-parallax-layers": "text-shadow:5px 5px 0 var(--accent),10px 10px 24px rgba(0,0,0,.7);",
        "caption-particle-burst": f"text-shadow:0 0 5px #fff,0 0 26px {accent};",
        "caption-texture": "background:linear-gradient(180deg,#fff3b0,#ff7a18,#b31217);-webkit-background-clip:text;color:transparent;",
        "caption-weight-shift": "font-variation-settings:'wght' 850;letter-spacing:.04em;",
    }
    return extras.get(style, "")


def _build_html(*, width: int, height: int, duration: float, groups: list[dict], style: str,
                accent: str, position: str) -> str:
    bottom = {"bottom-safe": "7%", "lower-middle": "24%", "center": "44%"}[position]
    groups_json = json.dumps(groups, ensure_ascii=False).replace("</", "<\\/")
    css = _style_css(style, accent)
    return f'''<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width={width},height={height}">
<script src="assets/gsap.min.js"></script><style>
@font-face{{font-family:'Cuti CJK';src:url('assets/cuti-cjk.ttf') format('truetype');font-weight:100 900;font-style:normal;font-display:block}}
*{{box-sizing:border-box}}html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;background:#000}}
#root{{position:relative;width:{width}px;height:{height}px;overflow:hidden;--accent:{accent}}}
#source-video{{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#000}}
#captions{{position:absolute;inset:0;pointer-events:none}}.group{{position:absolute;left:5%;right:5%;bottom:{bottom};display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:.22em;opacity:0;visibility:hidden;text-align:center;font-family:'Cuti CJK',sans-serif;font-size:clamp(34px,4.2vw,78px);font-weight:800;line-height:1.18;color:#fff;text-shadow:0 4px 14px rgba(0,0,0,.95);{css}}}
.sentence{{display:inline-block;position:relative;padding:.05em .10em;border-radius:.14em}}
</style></head><body><div id="root" data-composition-id="main" data-start="0" data-duration="{duration:.3f}" data-fps="30" data-width="{width}" data-height="{height}">
<video id="source-video" class="clip" src="assets/source.mp4" data-start="0" data-duration="{duration:.3f}" data-track-index="0" muted playsinline></video>
<audio id="source-audio" src="assets/source.mp4" data-start="0" data-duration="{duration:.3f}" data-track-index="10" data-volume="1"></audio>
<div id="captions" class="clip" data-start="0" data-duration="{duration:.3f}" data-track-index="20"></div></div>
<script>(function(){{window.__timelines=window.__timelines||{{}};var GROUPS={groups_json};var host=document.getElementById('captions');var tl=gsap.timeline({{paused:true}});
GROUPS.forEach(function(g,gi){{var el=document.createElement('div');el.className='group';el.id='cg-'+gi;var s=document.createElement('span');s.className='sentence';s.textContent=g.text;el.appendChild(s);host.appendChild(el);
tl.set(el,{{visibility:'visible'}},g.start);tl.fromTo(el,{{opacity:0,y:10}},{{opacity:1,y:0,duration:.14,ease:'power1.out'}},g.start);
tl.to(el,{{opacity:0,y:-12,duration:.12,ease:'power2.in'}},Math.max(g.start,g.end-.12));tl.set(el,{{opacity:0,visibility:'hidden'}},g.end)}});tl.seek(0);window.__timelines.main=tl}})();</script></body></html>'''


async def render_captions(video_path: str, output_path: str, *, words: list[dict], cues: list[dict],
                          style: str, accent_color: str, position: str) -> dict[str, Any]:
    if style not in STYLE_NAMES:
        raise ValueError(f"Unsupported HyperFrames caption style: {style}")
    groups = _sentence_groups(words, cues)
    if not groups:
        raise ValueError("HyperFrames captions require timestamped words or cues")
    info = await get_video_info(video_path)
    width, height = int(info.get("width") or 1920), int(info.get("height") or 1080)
    duration = float(info.get("duration") or groups[-1]["end"])
    cli = Path(os.path.expanduser(os.getenv("HYPERFRAMES_CLI", DEFAULT_CLI)))
    node_bin = Path(os.path.expanduser(os.getenv("HYPERFRAMES_NODE_BIN", DEFAULT_NODE_BIN)))
    if not cli.is_file():
        raise RuntimeError("HyperFrames CLI is not installed; set HYPERFRAMES_CLI")
    gsap = cli.parents[1] / "gsap" / "dist" / "gsap.min.js"
    if not gsap.is_file():
        raise RuntimeError("HyperFrames runtime is missing gsap; install gsap beside hyperframes")
    project = Path(tempfile.mkdtemp(prefix="cuti-hyperframes-"))
    try:
        assets = project / "assets"
        assets.mkdir()
        shutil.copy2(video_path, assets / "source.mp4")
        shutil.copy2(gsap, assets / "gsap.min.js")
        font_path = Path(os.path.expanduser(os.getenv(
            "HYPERFRAMES_CJK_FONT",
            "/mnt/c/Windows/Fonts/NotoSansSC-VF.ttf",
        )))
        if not font_path.is_file():
            raise RuntimeError(
                "HyperFrames CJK font is missing; set HYPERFRAMES_CJK_FONT "
                "to a Chinese-capable TTF font"
            )
        shutil.copy2(font_path, assets / "cuti-cjk.ttf")
        (project / "index.html").write_text(_build_html(
            width=width, height=height, duration=duration, groups=groups,
            style=style, accent=accent_color, position=position,
        ), encoding="utf-8")
        (project / "hyperframes.json").write_text(json.dumps({"media": {"autoProxy": True}}), encoding="utf-8")
        env = os.environ.copy()
        env["PATH"] = f"{node_bin}:{env.get('PATH', '')}"
        render_args = [
            str(cli), "render", str(project), "--output", output_path,
            "--workers", "1", "--strict", "--no-browser-gpu",
        ]
        if os.getenv("HYPERFRAMES_DOCKER", "").lower() in {"1", "true", "yes"}:
            render_args.append("--docker")
        process = await asyncio.create_subprocess_exec(
            *render_args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=1800)
        except asyncio.TimeoutError as exc:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            raise RuntimeError("HyperFrames render timed out after 1800 seconds") from exc
        if process.returncode != 0 or not Path(output_path).is_file():
            detail = (stderr or stdout).decode("utf-8", errors="replace")[-3000:]
            raise RuntimeError(f"HyperFrames render failed: {detail}")
        return {
            "renderer": "hyperframes", "runtime_version": "0.7.106", "style": style,
            "docker": os.getenv("HYPERFRAMES_DOCKER", "").lower() in {"1", "true", "yes"},
            "accent_color": accent_color, "position": position,
            "word_count": len(_normalize_words(words, [])), "group_count": len(groups),
        }
    finally:
        shutil.rmtree(project, ignore_errors=True)
