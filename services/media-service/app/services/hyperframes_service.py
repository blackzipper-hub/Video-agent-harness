from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.hyperframes import compose as hf_compose
from app.services.ffmpeg_service import get_video_info


DEFAULT_CLI = "~/.local/hyperframes-runtime/node_modules/.bin/hyperframes"
DEFAULT_NODE_BIN = "~/.local/node-v24.5.0/bin"
CLI_CANDIDATES = (
    str(Path(__file__).resolve().parents[4] / ".runtime-deps/hyperframes/node_modules/hyperframes/bin/hyperframes.mjs"),
    "/opt/hyperframes/node_modules/.bin/hyperframes",
    DEFAULT_CLI,
)
NODE_BIN_CANDIDATES = (
    "/usr/local/bin",
    DEFAULT_NODE_BIN,
)
CJK_FONT_CANDIDATES = (
    str(Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"),
    "/usr/share/fonts/truetype/cuti/NotoSansSC-VF.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSC-VF.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf",
    "/mnt/c/Windows/Fonts/NotoSansSC-VF.ttf",
)
BROWSER_CANDIDATES = (
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/opt/hyperframes/browser.path",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
)
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
        if text and start >= 0:
            if end <= start:
                end = start + 0.05
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


def _groups(
    words: list[dict[str, Any]],
    max_words: int = 5,
    cues: list[dict] | None = None,
) -> list[dict[str, Any]]:
    buckets: list[list[dict[str, Any]]] = []
    if cues:
        assigned = [False] * len(words)
        ranges: list[tuple[float, float]] = []
        for cue in cues:
            try:
                start, end = float(cue.get("start")), float(cue.get("end"))
            except (TypeError, ValueError):
                continue
            if end > start:
                ranges.append((start, end))
        for start, end in sorted(ranges):
            bucket = []
            for index, word in enumerate(words):
                if assigned[index]:
                    continue
                if word["end"] > start and word["start"] < end:
                    bucket.append(word)
                    assigned[index] = True
            if bucket:
                buckets.append(bucket)
        leftover = [word for index, word in enumerate(words) if not assigned[index]]
        if leftover:
            buckets.append(leftover)
    else:
        buckets = [words]

    groups: list[dict[str, Any]] = []
    for bucket in buckets:
        current: list[dict[str, Any]] = []
        for word in bucket:
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


def _index_groups(
    words: list[dict[str, Any]],
    max_words: int = 4,
    cues: list[dict] | None = None,
) -> list[dict[str, Any]]:
    indexed: list[dict[str, Any]] = []
    cursor = 0
    for group in _groups(words, max_words, cues=cues):
        count = len(group["words"])
        indexed.append({
            "wordStart": cursor,
            "wordEnd": cursor + count - 1,
            "start": group["start"],
            "end": group["end"],
        })
        cursor += count
    return indexed


def _has_cjk(words: list[dict[str, Any]], layers: list[dict[str, Any]]) -> bool:
    texts = [str(item.get("text") or "") for item in words]
    texts.extend(str(item.get("text") or "") for item in layers)
    return any(re.search(r"[\u3400-\u9fff]", text) for text in texts)


def _first_file(candidates: tuple[str, ...]) -> Path | None:
    for raw in candidates:
        if not raw:
            continue
        path = Path(os.path.expanduser(raw))
        if path.is_file():
            if path.name == "browser.path":
                pointed = Path(path.read_text(encoding="utf-8").strip())
                if pointed.is_file():
                    return pointed
                continue
            return path
    return None


def _resolve_cli() -> Path:
    cli = _first_file((os.getenv("HYPERFRAMES_CLI") or "",) + CLI_CANDIDATES)
    if cli is None:
        raise RuntimeError("HyperFrames CLI is not installed; set HYPERFRAMES_CLI")
    return cli


def _resolve_node_bin(cli: Path) -> Path:
    extra = os.getenv("HYPERFRAMES_NODE_BIN") or ""
    for raw in (extra,) + NODE_BIN_CANDIDATES:
        if not raw:
            continue
        path = Path(os.path.expanduser(raw))
        if path.is_dir():
            return path
    node = shutil.which("node")
    if node:
        return Path(node).parent
    return cli.parent


def _resolve_gsap(cli: Path) -> Path:
    for candidate in (
        *(parent / "gsap/dist/gsap.min.js" for parent in cli.parents),
        cli.parents[1] / "gsap" / "dist" / "gsap.min.js",
        Path("/opt/hyperframes/node_modules/gsap/dist/gsap.min.js"),
    ):
        if candidate.is_file():
            return candidate
    raise RuntimeError("HyperFrames runtime is missing gsap; install gsap beside hyperframes")


def _resolve_cjk_font() -> Path:
    extra = os.getenv("HYPERFRAMES_CJK_FONT") or ""
    font = _first_file((extra,) + CJK_FONT_CANDIDATES)
    if font is None:
        raise RuntimeError(
            "HyperFrames CJK font is missing; set HYPERFRAMES_CJK_FONT "
            "to a Chinese-capable TTF font"
        )
    return font


def _resolve_browser() -> Path | None:
    extra = os.getenv("HYPERFRAMES_BROWSER_PATH") or ""
    return _first_file((extra,) + BROWSER_CANDIDATES)


def _prepare_authored_html(
    html: str,
    *,
    duration: float,
    width: int,
    height: int,
    accent: str,
    has_cjk: bool,
) -> str:
    """Point overlay assets at this workspace. Timing and frame size stay authored."""
    del duration, width, height
    html = re.sub(
        r'<script src="https://cdn\.jsdelivr\.net/npm/gsap[^"]+"></script>',
        '<script src="assets/gsap.min.js"></script>',
        html,
    )
    html = re.sub(
        r'src="https://cdn\.jsdelivr\.net/npm/gsap[^"]+"',
        'src="assets/gsap.min.js"',
        html,
    )
    html = re.sub(
        r'(<video\b[^>]*\bsrc=")[^"]+"',
        r'\1assets/source.mp4"',
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    cjk_face = (
        "@font-face{font-family:'Cuti CJK';src:url('assets/cuti-cjk.ttf') "
        "format('truetype');font-weight:100 900;font-style:normal;font-display:block}"
    )
    extra = f"{cjk_face}:root{{--color-accent:{accent};}}"
    if has_cjk:
        extra += "html,body{font-family:'Cuti CJK',sans-serif}"
        html = re.sub(r'FONT_FAMILY = "[^"]+"', 'FONT_FAMILY = "Cuti CJK"', html)
    if "<style>" in html:
        html = html.replace("<style>", f"<style>\n{extra}\n", 1)
    else:
        html = html.replace("</head>", f"<style>{extra}</style></head>", 1)
    return html


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _caption_style_css(style: str, accent: str) -> str:
    """Translate the intent-level Cuti caption style into deterministic CSS."""
    extras = {
        "caption-pill-karaoke": "background:rgba(8,8,12,.82);border-radius:999px;padding:.20em .38em;",
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


def _build_generated_caption_html(
    *,
    width: int,
    height: int,
    duration: float,
    groups: list[dict[str, Any]],
    style: str,
    accent: str,
    position: str,
) -> str:
    """Build caption composition HTML from the original Cuti style contract.

    The agent chooses semantic parameters (style, position and accent); this
    adapter performs the mechanical transcript-to-HyperFrames conversion.
    Authored caption_html remains an optional advanced override.
    """
    bottom = {"bottom-safe": "7%", "lower-middle": "24%", "center": "44%"}[position]
    groups_json = json.dumps(groups, ensure_ascii=False).replace("</", "<\\/")
    style_css = _caption_style_css(style, accent)
    return f'''<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width={width},height={height}"></head><body><template>
<script src="assets/gsap.min.js"></script><style>
@font-face{{font-family:'Cuti CJK';src:url('assets/cuti-cjk.ttf') format('truetype');font-weight:100 900;font-style:normal;font-display:block}}
*{{box-sizing:border-box}}
#root{{position:absolute;inset:0;width:{width}px;height:{height}px;overflow:hidden;--accent:{accent};pointer-events:none}}
.group{{position:absolute;left:5%;right:5%;bottom:{bottom};display:flex;justify-content:center;align-items:center;opacity:0;text-align:center;font-family:'Cuti CJK',sans-serif;font-size:clamp(34px,4.2vw,78px);font-weight:800;line-height:1.18;color:#fff;text-shadow:0 4px 14px rgba(0,0,0,.95)}}
.sentence{{display:inline-block;position:relative;{style_css}}}
</style><div id="root" data-composition-id="overlay" data-duration="{duration:.3f}" data-fps="30" data-width="{width}" data-height="{height}"></div>
<script>(function(){{window.__timelines=window.__timelines||{{}};var GROUPS={groups_json};var host=document.getElementById('root');var tl=gsap.timeline({{paused:true}});
GROUPS.forEach(function(g,gi){{var el=document.createElement('div');el.className='group';el.id='cg-'+gi;var s=document.createElement('span');s.className='sentence';s.textContent=g.text;el.appendChild(s);host.appendChild(el);
tl.fromTo(el,{{opacity:0,y:10}},{{opacity:1,y:0,duration:.14,ease:'power1.out'}},g.start);
tl.to(el,{{opacity:0,y:-12,duration:.12,ease:'power2.in'}},Math.max(g.start,g.end-.12));tl.set(el,{{opacity:0}},g.end)}});tl.seek(0);window.__timelines.overlay=tl}})();</script>
</template></body></html>'''


def _layer_html(layers: list[dict[str, Any]], duration: float) -> tuple[str, str]:
    mounts: list[str] = []
    tweens: list[str] = []
    for index, layer in enumerate(layers):
        text = str(layer.get("text") or "").strip()
        if not text:
            continue
        try:
            start = max(0.0, float(layer.get("start") or 0))
            end = float(layer.get("end") or start + 3)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        end = min(end, duration)
        layer_duration = max(0.2, end - start)
        layer_id = f"overlay-layer-{index}"
        mounts.append(
            f'<div id="{layer_id}" class="clip title-layer" data-start="{start:.3f}" '
            f'data-duration="{layer_duration:.3f}" data-track-index="{30 + index}">'
            f'<div class="title-text">{_escape_html(text)}</div></div>'
        )
        fade = min(0.35, layer_duration / 3)
        tweens.append(
            f'tl.from("#{layer_id}",{{opacity:0,y:18,duration:{fade:.3f},ease:"power2.out"}},{start:.3f});'
            f'tl.to("#{layer_id}",{{opacity:0,duration:{fade:.3f},ease:"power2.in"}},'
            f"{max(start, end - fade):.3f});"
        )
    return "".join(mounts), "".join(tweens)


def _build_html(
    *,
    width: int,
    height: int,
    duration: float,
    composition_id: str = "overlay",
    css_vars: dict[str, str],
    layers: list[dict[str, Any]] | None = None,
) -> str:
    layer_markup, tweens = _layer_html(layers or [], duration)
    vars_css = hf_compose.css_vars_block(css_vars)
    return f'''<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width={width},height={height}">
<script src="assets/gsap.min.js"></script><style>
@font-face{{font-family:'Cuti CJK';src:url('assets/cuti-cjk.ttf') format('truetype');font-weight:100 900;font-style:normal;font-display:block}}
{vars_css}
*{{box-sizing:border-box}}html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;background:#000}}
#root{{position:relative;width:{width}px;height:{height}px;overflow:hidden}}
#source-video{{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#000}}
.caption-mount{{position:absolute;inset:0;pointer-events:none}}
.title-layer{{position:absolute;inset:0;pointer-events:none;display:flex;align-items:flex-start;justify-content:center;padding-top:7%}}
.title-text{{font-family:var(--font-heading),'Cuti CJK',sans-serif;font-size:clamp(42px,5vw,96px);font-weight:700;color:var(--color-fg);text-shadow:0 4px 18px rgba(0,0,0,.85);letter-spacing:.04em}}
</style></head><body><div id="root" data-composition-id="main" data-start="0" data-duration="{duration:.3f}" data-fps="30" data-width="{width}" data-height="{height}">
<video id="source-video" class="clip" src="assets/source.mp4" data-start="0" data-duration="{duration:.3f}" data-track-index="0" muted playsinline></video>
<audio id="source-audio" src="assets/source.mp4" data-start="0" data-duration="{duration:.3f}" data-track-index="10" data-volume="1"></audio>
<div id="caption-mount" class="caption-mount clip" data-composition-id="{composition_id}" data-composition-src="compositions/{composition_id}.html" data-start="0" data-duration="{duration:.3f}" data-width="{width}" data-height="{height}" data-track-index="20"></div>
{layer_markup}
</div>
<script>(function(){{window.__timelines=window.__timelines||{{}};var tl=gsap.timeline({{paused:true}});{tweens}tl.seek(0);window.__timelines.main=tl}})();</script></body></html>'''


async def render_captions(
    video_path: str,
    output_path: str,
    *,
    words: list[dict],
    cues: list[dict],
    style: str = "caption-highlight",
    accent_color: str = "#ff1745",
    position: str = "bottom-safe",
    playbook: str | None = None,
    layers: list[dict] | None = None,
    caption_html: str | None = None,
    composition_html: str | None = None,
) -> dict[str, Any]:
    authored_caption = (caption_html or "").strip()
    authored_host = (composition_html or "").strip()
    normalized = _normalize_words(words, cues)
    generated_groups: list[dict[str, Any]] = []
    if not authored_caption and not authored_host:
        if style not in STYLE_NAMES:
            raise ValueError(f"Unsupported HyperFrames caption style: {style}")
        generated_groups = _sentence_groups(words, cues)
        if not generated_groups:
            raise ValueError("HyperFrames captions require timestamped words or cues")
    overlay_layers = [item for item in (layers or []) if isinstance(item, dict)]
    info = await get_video_info(video_path)
    width, height = int(info.get("width") or 1920), int(info.get("height") or 1080)
    duration = float(info.get("duration") or (normalized[-1]["end"] if normalized else 0) or 1)
    cli = _resolve_cli()
    node_bin = _resolve_node_bin(cli)
    gsap = _resolve_gsap(cli)
    font_path = _resolve_cjk_font()
    browser = _resolve_browser()
    playbook_data = hf_compose.load_playbook(playbook)
    css_vars, design_md = hf_compose.css_from_playbook(playbook_data, accent_color=accent_color)
    html_has_cjk = bool(re.search(r"[\u3400-\u9fff]", authored_caption + authored_host))
    has_cjk = _has_cjk(normalized, overlay_layers) or html_has_cjk
    if has_cjk:
        css_vars["--font-heading"] = "Cuti CJK"
        css_vars["--font-body"] = "Cuti CJK"
    project = Path(tempfile.mkdtemp(prefix="cuti-hyperframes-"))
    try:
        hf_compose.write_workspace_config(project)
        assets = project / "assets"
        shutil.copy2(video_path, assets / "source.mp4")
        shutil.copy2(gsap, assets / "gsap.min.js")
        shutil.copy2(font_path, assets / "cuti-cjk.ttf")
        if authored_caption:
            staged = _prepare_authored_html(
                authored_caption,
                duration=duration,
                width=width,
                height=height,
                accent=accent_color,
                has_cjk=has_cjk,
            )
            hf_compose.stage_caption_html(project, "overlay", staged)
        elif not authored_host:
            hf_compose.stage_caption_html(
                project,
                "overlay",
                _build_generated_caption_html(
                    width=width,
                    height=height,
                    duration=duration,
                    groups=generated_groups,
                    style=style,
                    accent=accent_color,
                    position=position,
                ),
            )
        if authored_host:
            (project / "index.html").write_text(
                _prepare_authored_html(
                    authored_host,
                    duration=duration,
                    width=width,
                    height=height,
                    accent=accent_color,
                    has_cjk=has_cjk,
                ),
                encoding="utf-8",
            )
        else:
            (project / "index.html").write_text(_build_html(
                width=width, height=height, duration=duration,
                css_vars=css_vars, layers=overlay_layers,
            ), encoding="utf-8")
        if design_md:
            (project / "DESIGN.md").write_text(design_md, encoding="utf-8")
        env = os.environ.copy()
        env["PATH"] = f"{node_bin}{os.pathsep}{env.get('PATH', '')}"
        if browser is not None:
            env.setdefault("HYPERFRAMES_BROWSER_PATH", str(browser))
            env.setdefault("PUPPETEER_EXECUTABLE_PATH", str(browser))
        command = [str(cli)]
        if cli.suffix in {".js", ".mjs", ".cjs"}:
            node = shutil.which("node", path=env["PATH"])
            if not node:
                raise RuntimeError("HyperFrames requires Node.js on PATH")
            command = [node, str(cli)]
        render_args = [
            *command, "render", str(project), "--output", output_path,
            "--workers", "1", "--no-browser-gpu",
            "--browser-timeout", "120",
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
            "renderer": "hyperframes",
            "runtime_version": "0.7.106",
            "style": "authored" if (authored_caption or authored_host) else style,
            "playbook": playbook_data.get("id") or playbook,
            "docker": os.getenv("HYPERFRAMES_DOCKER", "").lower() in {"1", "true", "yes"},
            "accent_color": accent_color, "position": position,
            "word_count": len(normalized),
            "group_count": len(generated_groups) if generated_groups else len(_index_groups(normalized, cues=cues)),
            "layer_count": len(overlay_layers),
            "workspace": str(project) if os.getenv("HYPERFRAMES_KEEP", "").lower() in {"1", "true", "yes"} else None,
        }
    finally:
        if os.getenv("HYPERFRAMES_KEEP", "").lower() not in {"1", "true", "yes"}:
            shutil.rmtree(project, ignore_errors=True)
