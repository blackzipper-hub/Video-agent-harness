import os
import shutil
import subprocess
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ffmpeg_service import concat_videos, extract_frame, get_video_info


def _ffmpeg_ok() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_clip(path: str, *, color: str, video_seconds: float = 1.0, audio_seconds: float = 1.08) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color={color}:size=320x240:rate=24:duration={video_seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={audio_seconds}",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", path,
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg/ffprobe not in PATH")
async def test_concat_does_not_duplicate_video_tail_for_longer_audio():
    with tempfile.TemporaryDirectory() as tmp:
        first = os.path.join(tmp, "first.mp4")
        second = os.path.join(tmp, "second.mp4")
        output = os.path.join(tmp, "output.mp4")
        _make_clip(first, color="red")
        _make_clip(second, color="blue")

        first_info = await get_video_info(first)
        second_info = await get_video_info(second)
        await concat_videos([first, second], output, normalize=True)
        output_info = await get_video_info(output)

        assert output_info["nb_frames"] == first_info["nb_frames"] + second_info["nb_frames"]


@pytest.mark.asyncio
@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg/ffprobe not in PATH")
async def test_extract_last_frame_uses_final_decoded_video_frame():
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "source.mp4")
        frame = os.path.join(tmp, "last.png")
        _make_clip(source, color="green")

        info = await get_video_info(source)
        result = await extract_frame(source, frame, None, position="last", image_format="png")

        expected_pts = (info["nb_frames"] - 1) / info["fps"]
        assert result["position"] == "last"
        assert abs(result["timestamp"] - expected_pts) < 1e-4
        assert os.path.getsize(frame) > 0


@pytest.mark.asyncio
@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg/ffprobe not in PATH")
async def test_concat_supports_short_continuation_crossfade():
    with tempfile.TemporaryDirectory() as tmp:
        first = os.path.join(tmp, "first.mp4")
        second = os.path.join(tmp, "second.mp4")
        output = os.path.join(tmp, "output.mp4")
        _make_clip(first, color="red")
        _make_clip(second, color="blue")

        await concat_videos(
            [first, second],
            output,
            normalize=True,
            transition_duration=0.125,
        )
        output_info = await get_video_info(output)

        assert 44 <= output_info["nb_frames"] <= 46
        assert abs(output_info["video_duration"] - 1.875) < 0.1


@pytest.mark.asyncio
async def test_concat_normalize_uses_copy_for_identical_hevc_main10_streams():
    """Seedance segments must not spend 20 minutes transcoding compatible HEVC."""
    info = {
        "duration": 15.072,
        "video_duration": 15.0,
        "width": 1920,
        "height": 1080,
        "fps": 24.0,
        "nb_frames": 360,
        "codec": "hevc",
        "pix_fmt": "yuv420p10le",
        "has_audio": True,
        "time_base": "1/12288",
        "audio_codec": "aac",
        "audio_sample_rate": 32000,
        "audio_channels": 2,
        "audio_channel_layout": "stereo",
        "audio_time_base": "1/32000",
    }
    with tempfile.TemporaryDirectory() as tmp:
        output = os.path.join(tmp, "output.mp4")
        run = AsyncMock(return_value=(0, "", ""))
        inspect = AsyncMock(return_value=info)
        with (
            patch("app.services.ffmpeg_service.run_ffmpeg", run),
            patch("app.services.ffmpeg_service.get_video_info", inspect),
        ):
            await concat_videos(
                [os.path.join(tmp, "one.mp4"), os.path.join(tmp, "two.mp4")],
                output,
                normalize=True,
            )

        command = run.await_args.args[0]
        assert command[command.index("-c") + 1] == "copy"
        assert "libx264" not in command


@pytest.mark.asyncio
async def test_concat_timestamp_corruption_forces_transcode_without_reentering_copy():
    source_info = {
        "duration": 10.0,
        "video_duration": 10.0,
        "width": 1920,
        "height": 1080,
        "fps": 24.0,
        "nb_frames": 240,
        "codec": "hevc",
        "pix_fmt": "yuv420p10le",
        "has_audio": True,
        "time_base": "1/12288",
        "audio_codec": "aac",
        "audio_sample_rate": 32000,
        "audio_channels": 2,
        "audio_channel_layout": "stereo",
        "audio_time_base": "1/32000",
    }
    corrupt_output_info = {**source_info, "duration": 30.0, "video_duration": 30.0}
    with tempfile.TemporaryDirectory() as tmp:
        output = os.path.join(tmp, "output.mp4")
        run = AsyncMock(return_value=(0, "", ""))
        inspect = AsyncMock(side_effect=[
            source_info,
            source_info,
            corrupt_output_info,
            source_info,
            source_info,
        ])
        with (
            patch("app.services.ffmpeg_service.run_ffmpeg", run),
            patch("app.services.ffmpeg_service.get_video_info", inspect),
            patch("app.services.ffmpeg_service.os.unlink"),
        ):
            await concat_videos(
                [os.path.join(tmp, "one.mp4"), os.path.join(tmp, "two.mp4")],
                output,
                normalize=True,
            )

        assert run.await_count == 2
        copy_command = run.await_args_list[0].args[0]
        transcode_command = run.await_args_list[1].args[0]
        assert copy_command[copy_command.index("-c") + 1] == "copy"
        assert "libx264" in transcode_command
