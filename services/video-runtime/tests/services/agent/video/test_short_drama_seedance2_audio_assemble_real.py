"""真实验证：Short Drama + Seedance2 I2V 出声 + assemble 保片内声。

会扣 WaveSpeed 费用（约 480p/4s Fast I2V 一镜），并打 media-dev。

运行：
  cd services/agent
  set -a && source .env.internal && set +a
  RUN_SHORT_DRAMA_AUDIO_REAL=1 pytest \\
    tests/services/agent/video/test_short_drama_seedance2_audio_assemble_real.py -v -s
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import load_dotenv

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[4]
load_dotenv(ROOT / ".env.internal")
load_dotenv(ROOT / ".env.local")

EXAMPLE_START = (
    "https://static.wavespeed.ai/examples/"
    "3f7e3b510d1e4a60aea09ec4573e1751/1775358390074410023_q9OY8irB.jpeg"
)

_REAL = os.getenv("RUN_SHORT_DRAMA_AUDIO_REAL", "").strip() in ("1", "true", "yes")


def test_short_drama_routing_blocks_lipsync_despite_coverage():
    from app.models.tool_enums import ContentCategory, GenerationMode
    from app.models.user_options import UserOption, should_enable_lipsync_for_run
    from app.services.agent.video.per_shot_generation_routing_service import (
        assign_generation_mode_to_shots,
    )

    opt = UserOption(
        lipsync_coverage=50,
        content_category=ContentCategory.SHORT_DRAMA,
    )
    assert should_enable_lipsync_for_run(opt) is False

    shots = [
        SimpleNamespace(
            generation_mode=GenerationMode.LIPSYNC.value,
            audio_segment_ids=[],
            character_ids=["c1"],
            narration="",
            dialogue="你好",
        )
    ]
    assign_generation_mode_to_shots(
        shots,
        None,
        character_type_map={"c1": "character"},
        allow_lipsync=False,
    )
    assert shots[0].generation_mode == GenerationMode.NORMAL.value


@pytest.mark.asyncio
@pytest.mark.skipif(not _REAL, reason="set RUN_SHORT_DRAMA_AUDIO_REAL=1 to run (costs credits)")
async def test_seedance2_i2v_generate_audio_then_assemble_preserves_audio():
    """1) Seedance2 Fast I2V generate_audio=True → has_audio
    2) assemble preserve_video_audio=True → 成片仍有音轨
    """
    from app.llm.wavespeed_service import WaveSpeedService
    from app.utils import media_service_client as msc
    from app.utils.video_utils import concatenate_videos_silent_with_narration_at_end

    if not os.getenv("WAVESPEED_API_KEY"):
        pytest.skip("WAVESPEED_API_KEY not set")

    svc = WaveSpeedService()
    if not svc.api_key:
        pytest.skip("WaveSpeed API key not configured")

    prompt = (
        'Single continuous cinematic shot. A young woman looks at camera and says: '
        '"I finally made it home." Clear dialogue audio, warm daylight, subtle motion.'
    )
    print("\n=== 1) Seedance2 Fast I2V generate_audio=True (480p/4s) ===")
    result = await svc.generate_seedance_2_fast_i2v(
        image=EXAMPLE_START,
        prompt=prompt,
        duration=4,
        resolution="480p",
        aspect_ratio="16:9",
        generate_audio=True,
    )
    assert result.success, f"I2V failed: {result.message or result.error_msg}"
    assert result.video_url
    print(f"video_url={result.video_url}")

    info = await msc.video_info(result.video_url)
    print(f"video_info={info}")
    assert info.get("has_audio") is True, (
        f"期望 generate_audio=True 上传后仍有音轨，实际 has_audio={info.get('has_audio')} info={info}"
    )

    print("\n=== 2) assemble preserve_video_audio=True（两镜同一片内声） ===")
    vg1 = SimpleNamespace(shot_number=1, success=True, video_url=result.video_url)
    vg2 = SimpleNamespace(shot_number=2, success=True, video_url=result.video_url)
    # 稀疏旁白：仅 shot2 有 TTS 占位为空 → 仍应走 preserve 抽片内声
    narrations_data = {
        1: {"version": None},
        2: {"version": None},
    }
    final_url, timeline = await concatenate_videos_silent_with_narration_at_end(
        [result.video_url, result.video_url],
        [vg1, vg2],
        narrations_data,
        title="short_drama_audio_preserve_test",
        preserve_video_audio=True,
    )
    print(f"final_url={final_url}")
    print(f"timeline={timeline}")
    assert final_url
    final_info = await msc.video_info(final_url)
    print(f"final_video_info={final_info}")
    assert final_info.get("has_audio") is True, (
        f"assemble preserve 后成片应有音轨，实际 has_audio={final_info.get('has_audio')} info={final_info}"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not _REAL, reason="set RUN_SHORT_DRAMA_AUDIO_REAL=1 to run (costs credits)")
async def test_seedance2_programmatic_character_says_produces_spoken_audio():
    """Director-shaped prompt already includes says/beats (no Python inject) → spoken audio."""
    import tempfile
    import subprocess
    from pathlib import Path

    from app.llm.wavespeed_service import WaveSpeedService
    from app.utils import media_service_client as msc

    if not os.getenv("WAVESPEED_API_KEY"):
        pytest.skip("WAVESPEED_API_KEY not set")

    svc = WaveSpeedService()
    if not svc.api_key:
        pytest.skip("WaveSpeed API key not configured")

    prompt = (
        "单一连续电影级镜头，逼真摄影，16:9。中近景，年轻女性看向镜头。"
        "0-2秒：抬手制止。"
        "2-4秒：女主（厉声）说：「闭嘴！」"
        "音效：叹气、清晰对白；无背景音乐。禁止字幕。"
    )
    assert "说：「闭嘴！」" in prompt
    print("\n=== skill-shaped Character says prompt (tail) ===")
    print(prompt[-400:])

    print("\n=== Seedance2 Fast I2V 480p/4s generate_audio=True ===")
    result = await svc.generate_seedance_2_fast_i2v(
        image=EXAMPLE_START,
        prompt=prompt,
        duration=4,
        resolution="480p",
        aspect_ratio="16:9",
        generate_audio=True,
    )
    assert result.success, f"I2V failed: {result.message or result.error_msg}"
    assert result.video_url
    print(f"video_url={result.video_url}")

    info = await msc.video_info(result.video_url)
    print(f"video_info={info}")
    assert info.get("has_audio") is True

    # 抽音轨做 volumedetect：对白通常 mean_volume 明显高于纯静音/极轻环境音
    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "clip.mp4"
        wav = Path(td) / "clip.wav"
        import urllib.request

        urllib.request.urlretrieve(result.video_url, mp4)
        probe = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(mp4),
                "-vn", "-ac", "1", "-ar", "16000", str(wav),
            ],
            capture_output=True,
            text=True,
        )
        assert probe.returncode == 0, probe.stderr[-500:]
        vol = subprocess.run(
            [
                "ffmpeg", "-i", str(wav),
                "-af", "volumedetect", "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
        )
        log = (vol.stderr or "") + (vol.stdout or "")
        print(f"volumedetect:\n{log[-800:]}")
        mean_line = next(
            (ln for ln in log.splitlines() if "mean_volume:" in ln),
            None,
        )
        assert mean_line, f"no mean_volume in ffmpeg log: {log[-400:]}"
        # e.g. mean_volume: -22.3 dB  （静音常接近 -91 dB）
        mean_db = float(mean_line.split("mean_volume:")[1].split("dB")[0].strip())
        print(f"mean_volume_db={mean_db}")
        assert mean_db > -55.0, (
            f"音量过低，疑似无对白/仅极轻环境音 mean_volume={mean_db}dB url={result.video_url}"
        )

    print("\n=== PASS: skill-shaped Character says → spoken audio ===")
    print(f"video_url={result.video_url}")
