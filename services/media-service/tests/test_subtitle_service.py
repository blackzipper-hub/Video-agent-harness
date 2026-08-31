import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.models.subtitle import SubtitleCue
from app.services import ffmpeg_service, hyperframes_service, subtitle_service


def test_hyperframes_words_fall_back_to_cue_timing_and_preserve_cjk():
    words = hyperframes_service._normalize_words([], [
        {"start": 1.0, "end": 2.0, "text": "你好 world"},
    ])

    assert [item["text"] for item in words] == ["你", "好", "world"]
    assert words[0]["start"] == pytest.approx(1.0)
    assert words[-1]["end"] == pytest.approx(2.0)


def test_hyperframes_html_uses_direct_video_audio_and_local_gsap():
    groups = [{"text": "Hello world", "start": 0.1, "end": 0.8}]
    rendered = hyperframes_service._build_html(
        width=1280, height=720, duration=1.0,
        groups=groups,
        style="caption-neon-glow", accent="#00ffcc", position="bottom-safe",
    )

    assert 'src="assets/gsap.min.js"' in rendered
    assert '<video id="source-video"' in rendered
    assert '<audio id="source-audio"' in rendered
    assert "assets/cuti-cjk.ttf" in rendered
    assert "caption-neon-glow" not in rendered  # style is compiled to deterministic CSS
    assert "#00ffcc" in rendered
    assert "Hello world" in rendered
    assert "word active" not in rendered
    assert "scale:1.10" not in rendered


def test_hyperframes_prefers_complete_sentence_cues():
    groups = hyperframes_service._sentence_groups(
        [{"word": "你", "start": 0.1, "end": 0.2}],
        [{"text": "你好，欢迎回来。", "start": 0.1, "end": 1.4}],
    )

    assert groups == [{"text": "你好，欢迎回来。", "start": 0.1, "end": 1.4}]


def test_normalize_cues_wraps_cjk_and_reports_overlap():
    cues, validation = subtitle_service.normalize_cues(
        [
            SubtitleCue(start=0, end=2, text="这是一个用于验证自动换行的字幕句子"),
            SubtitleCue(start=1.8, end=3, text="第二句"),
        ],
        max_lines=2,
        max_chars_per_line=8,
        max_cps=15,
    )

    assert "\n" in cues[0].text
    assert validation["overlaps"] == 1
    assert validation["invalid_cues"] == 0


def test_write_srt_uses_utf8_bom_and_millisecond_timestamps(tmp_path: Path):
    output = tmp_path / "captions.srt"
    subtitle_service.write_subtitle(
        output,
        [SubtitleCue(start=1.234, end=2.5, text="Hello\nworld")],
        "srt",
    )

    text = output.read_text(encoding="utf-8-sig")
    assert "00:00:01,234 --> 00:00:02,500" in text
    assert "Hello\nworld" in text


@pytest.mark.asyncio
async def test_burn_subtitles_builds_safe_ffmpeg_filter(monkeypatch, tmp_path: Path):
    captured = {}

    async def fake_info(_path):
        return {"width": 1920, "height": 1080}

    async def fake_run(command, timeout=None):
        captured["command"] = command
        return 0, "", ""

    monkeypatch.setattr(ffmpeg_service, "get_video_info", fake_info)
    monkeypatch.setattr(ffmpeg_service, "run_ffmpeg", fake_run)
    subtitle = tmp_path / "captions.srt"
    subtitle.write_text("", encoding="utf-8")

    style = await ffmpeg_service.burn_subtitles(
        str(tmp_path / "video.mp4"),
        str(subtitle),
        str(tmp_path / "output.mp4"),
        style_preset="clean",
        position="bottom-safe",
        font_name="Noto Sans CJK SC;unsafe",
    )

    command = captured["command"]
    video_filter = command[command.index("-vf") + 1]
    assert "subtitles=filename=" in video_filter
    assert "original_size=1920x1080" in video_filter
    assert "FontName=Noto Sans CJK SCunsafe" in video_filter
    assert "FontSize=16" in video_filter
    assert "Alignment=2" in video_filter
    assert 40 <= style["font_size"] <= 70
    assert 40 <= style["margin_v"] <= 80
    assert command[-1].endswith("output.mp4")


def test_resolve_subtitle_font_detects_windows_noto_family(monkeypatch):
    original_is_file = Path.is_file

    def fake_is_file(path):
        if str(path).replace("/", "\\").lower().endswith(
            r"windows\fonts\notosanssc-vf.ttf"
        ):
            return True
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", fake_is_file)
    monkeypatch.setattr(ffmpeg_service.settings, "subtitle_font_name", "")
    monkeypatch.setattr(ffmpeg_service.settings, "subtitle_fonts_dir", "")

    family, fonts_dir, source = ffmpeg_service._resolve_subtitle_font()

    assert family == "Noto Sans SC"
    assert fonts_dir.replace("/", "\\").lower().endswith(r"windows\fonts")
    assert source.replace("/", "\\").lower().endswith(r"notosanssc-vf.ttf")


@pytest.mark.asyncio
async def test_burn_subtitles_passes_discovered_fontsdir(monkeypatch, tmp_path: Path):
    captured = {}

    async def fake_info(_path):
        return {"width": 1280, "height": 720}

    async def fake_run(command, timeout=None):
        captured["command"] = command
        return 0, "", ""

    monkeypatch.setattr(ffmpeg_service, "get_video_info", fake_info)
    monkeypatch.setattr(ffmpeg_service, "run_ffmpeg", fake_run)
    monkeypatch.setattr(
        ffmpeg_service,
        "_resolve_subtitle_font",
        lambda _requested=None: ("Microsoft YaHei", r"C:\Windows\Fonts", r"C:\Windows\Fonts\msyh.ttc"),
    )
    subtitle = tmp_path / "captions.srt"
    subtitle.write_text("字幕测试", encoding="utf-8-sig")

    style = await ffmpeg_service.burn_subtitles(
        str(tmp_path / "video.mp4"), str(subtitle), str(tmp_path / "output.mp4")
    )

    video_filter = captured["command"][captured["command"].index("-vf") + 1]
    assert "FontName=Microsoft YaHei" in video_filter
    assert "fontsdir='C\\:/Windows/Fonts'" in video_filter
    assert style["font_name"] == "Microsoft YaHei"
    assert style["fonts_dir"] == r"C:\Windows\Fonts"


@pytest.mark.asyncio
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg is not installed",
)
async def test_real_ffmpeg_burn_produces_playable_mp4(tmp_path: Path):
    source = tmp_path / "source.mp4"
    subtitle = tmp_path / "captions.srt"
    output = tmp_path / "captioned.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-shortest", "-c:v", "libx264", "-c:a", "aac", str(source),
    ], check=True, capture_output=True)
    subtitle_service.write_subtitle(
        subtitle,
        [SubtitleCue(start=0.1, end=1.8, text="字幕端到端测试")],
        "srt",
    )

    await ffmpeg_service.burn_subtitles(str(source), str(subtitle), str(output))

    probe = subprocess.run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(output),
    ], check=True, capture_output=True, text=True)
    metadata = json.loads(probe.stdout)
    assert output.stat().st_size > 0
    assert float(metadata["format"]["duration"]) >= 1.9
    assert any(stream.get("codec_type") == "video" for stream in metadata["streams"])
