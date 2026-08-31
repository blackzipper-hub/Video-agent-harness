"""Unit tests for MV audio planning + host media.audio_* capabilities."""
from __future__ import annotations

import pytest

from app.chat.v2.host_gateway import HostGateway, HostGatewayError
from app.chat.v2.mv_audio import (
    SEEDANCE_MAX_AUDIO_SEC,
    plan_seedance_segments,
    resolve_master_window,
)
from app.chat.v2.capability_loader import build_registry


def test_resolve_master_window_full_track():
    assert resolve_master_window(90.0) == (0.0, 90.0)


def test_resolve_master_window_center_crop():
    start, end = resolve_master_window(100.0, target_duration_sec=40.0)
    assert start == pytest.approx(30.0)
    assert end == pytest.approx(70.0)


def test_resolve_master_window_explicit_range():
    start, end = resolve_master_window(120.0, start_sec=45.0, end_sec=75.0)
    assert (start, end) == (45.0, 75.0)


def test_resolve_master_window_rejects_bad_range():
    with pytest.raises(ValueError):
        resolve_master_window(30.0, start_sec=20.0, end_sec=10.0)


def test_plan_seedance_segments_single_short_clip():
    segs = plan_seedance_segments(12.0)
    assert len(segs) == 1
    assert segs[0]["duration_sec"] == pytest.approx(12.0)
    assert segs[0]["start_sec"] == 0.0


def test_plan_seedance_segments_exact_multiples():
    segs = plan_seedance_segments(60.0)
    assert len(segs) == 4
    assert all(s["duration_sec"] == pytest.approx(15.0) for s in segs)
    assert segs[0]["start_sec"] == pytest.approx(0.0)
    assert segs[-1]["end_sec"] == pytest.approx(60.0)
    assert all(s["duration_sec"] <= SEEDANCE_MAX_AUDIO_SEC + 1e-6 for s in segs)


def test_plan_seedance_segments_respects_window_offset():
    segs = plan_seedance_segments(30.0, window_start_sec=45.0)
    assert segs[0]["start_sec"] == pytest.approx(45.0)
    assert segs[-1]["end_sec"] == pytest.approx(75.0)
    assert all(4.0 <= s["duration_sec"] <= 15.0 + 1e-6 for s in segs)


def test_plan_seedance_segments_avoids_tiny_tail_when_possible():
    segs = plan_seedance_segments(47.0)
    assert all(s["duration_sec"] <= 15.0 + 1e-6 for s in segs)
    assert sum(s["duration_sec"] for s in segs) == pytest.approx(47.0)
    assert segs[-1]["duration_sec"] >= 4.0 - 1e-6


def test_platform_registers_mv_media_capabilities():
    registry = build_registry(include_platform=True)
    assert registry.get("media.audio_trim").service_target == "media_audio_trim"
    assert registry.get("media.audio_analyze").service_target == "media_audio_analyze"
    assert registry.get("media.mix_audio").service_target == "media_mix_audio"


@pytest.mark.asyncio
async def test_media_audio_trim_forwards_to_client(monkeypatch):
    calls = {}

    async def _fake_trim(audio_url, start, duration, run_id):
        calls.update({
            "audio_url": audio_url,
            "start": start,
            "duration": duration,
            "run_id": run_id,
        })
        return {"result_url": "http://localhost/files/clip.mp3"}

    monkeypatch.setattr("app.utils.media_service_client.audio_trim", _fake_trim)
    result = await HostGateway().media_audio_trim({
        "audio_url": "http://localhost/files/song.mp3",
        "start": 10.0,
        "duration": 15.0,
    })
    assert calls["start"] == 10.0
    assert calls["duration"] == 15.0
    assert result["uri"].endswith("clip.mp3")


@pytest.mark.asyncio
async def test_media_audio_trim_with_fade(monkeypatch):
    called = {}

    async def _fake_fade(audio_url, start, duration, fade_in_sec, fade_out_sec, run_id):
        called.update({
            "fade_in_sec": fade_in_sec,
            "fade_out_sec": fade_out_sec,
        })
        return {"result_url": "http://localhost/files/fade.mp3"}

    monkeypatch.setattr(
        "app.utils.media_service_client.audio_trim_with_fade", _fake_fade
    )
    result = await HostGateway().media_audio_trim({
        "audio_url": "http://localhost/files/song.mp3",
        "start_sec": 0,
        "duration_sec": 30,
        "fade_in_sec": 0.5,
        "fade_out_sec": 1.0,
    })
    assert called == {"fade_in_sec": 0.5, "fade_out_sec": 1.0}
    assert result["audio_url"].endswith("fade.mp3")


@pytest.mark.asyncio
async def test_media_audio_analyze_center_and_segments(monkeypatch):
    async def _fake_info(audio_url):
        return {"duration": 90.0}

    monkeypatch.setattr("app.utils.media_service_client.audio_info", _fake_info)
    result = await HostGateway().media_audio_analyze({
        "audio_url": "http://localhost/files/song.mp3",
        "target_duration_sec": 60.0,
    })
    assert result["method"] == "center_window"
    assert result["master"]["duration_sec"] == pytest.approx(60.0)
    assert result["segment_count"] == 4
    assert result["alignment"]["spine"] == "music"
    assert all(s["duration_sec"] <= 15.0 + 1e-6 for s in result["segments"])


@pytest.mark.asyncio
async def test_media_audio_analyze_requires_url():
    with pytest.raises(HostGatewayError, match="audio_url"):
        await HostGateway().media_audio_analyze({})


@pytest.mark.asyncio
async def test_media_mix_audio_replace_uses_add_audio(monkeypatch):
    calls = {}

    async def _fake_add(video_url, audio_segments, run_id):
        calls["video_url"] = video_url
        calls["audio_segments"] = audio_segments
        return {"result_url": "http://localhost/files/final.mp4"}

    monkeypatch.setattr("app.utils.media_service_client.video_add_audio", _fake_add)
    result = await HostGateway().media_mix_audio({
        "video_url": "http://localhost/files/silent.mp4",
        "audio_url": "http://localhost/files/master.mp3",
        "mode": "replace",
    })
    assert calls["audio_segments"][0]["audio_url"].endswith("master.mp3")
    assert result["mode"] == "replace"
    assert result["assembly_mode"] == "host_media_mix_replace"


@pytest.mark.asyncio
async def test_media_mix_audio_overlay(monkeypatch):
    calls = {}

    async def _fake_mix(video_url, audio_url, run_id, video_volume=0.3, audio_volume=1.0, loop_audio=False):
        calls.update({
            "audio_volume": audio_volume,
            "loop_audio": loop_audio,
        })
        return {"result_url": "http://localhost/files/mixed.mp4"}

    monkeypatch.setattr("app.utils.media_service_client.video_mix_audio", _fake_mix)
    result = await HostGateway().media_mix_audio({
        "video_url": "http://localhost/files/v.mp4",
        "audio_url": "http://localhost/files/a.mp3",
        "mode": "overlay",
        "audio_volume": 0.4,
        "loop_audio": True,
    })
    assert calls["audio_volume"] == 0.4
    assert calls["loop_audio"] is True
    assert result["mode"] == "overlay"


@pytest.mark.asyncio
async def test_media_audio_analyze_smart_clip_path(monkeypatch):
    from types import SimpleNamespace

    async def _fake_info(audio_url):
        return {"duration": 180.0}

    async def _fake_smart(audio_url, target_duration_sec, transcription, *, audio_duration_sec=None):
        return SimpleNamespace(
            recommended=SimpleNamespace(start_sec=30.0, end_sec=90.0),
            method="ai_reuse_transcription",
            model_dump=lambda: {
                "recommended": {"start_sec": 30.0, "end_sec": 90.0},
                "method": "ai_reuse_transcription",
            },
        )

    monkeypatch.setattr("app.utils.media_service_client.audio_info", _fake_info)
    monkeypatch.setattr(
        "app.services.agent.video.music_smart_clip_service.analyze_music_smart_clip",
        _fake_smart,
    )
    result = await HostGateway().media_audio_analyze({
        "audio_url": "http://localhost/files/song.mp3",
        "target_duration_sec": 60.0,
        "transcription": {"sections": [{"name": "chorus", "start": 30, "end": 90}]},
    })
    assert result["method"].startswith("smart_clip:")
    assert result["master"]["start_sec"] == pytest.approx(30.0)
    assert result["master"]["duration_sec"] == pytest.approx(60.0)
    assert result["smart_clip"] is not None


def test_collect_artifact_audio_url_prefers_music_type():
    from app.chat.v2.executors import CapabilityExecutor
    from app.chat.v2.models import ArtifactVersion

    selected = [
        ArtifactVersion(
            id="v1",
            artifact_id="a-v",
            project_id="p",
            type="video",
            version=1,
            produced_by_task_id="t1",
            title="clip",
            summary="",
            uri="http://localhost/files/clip.mp4",
            metadata={},
        ),
        ArtifactVersion(
            id="m1",
            artifact_id="a-m",
            project_id="p",
            type="music",
            version=1,
            produced_by_task_id="t2",
            title="song",
            summary="",
            uri="http://localhost/files/song.mp3",
            metadata={},
        ),
    ]
    assert CapabilityExecutor._collect_artifact_audio_url(selected).endswith("song.mp3")


def test_system_prompt_mentions_seedance_mv():
    from types import SimpleNamespace

    from app.chat.v2.deep_agent_runtime import DeepAgentRuntime

    runtime = DeepAgentRuntime.__new__(DeepAgentRuntime)
    runtime.settings = SimpleNamespace(DEEP_AGENT_V2_MAX_PARALLEL_GENERATION_TASKS=2)
    prompt = runtime._system_prompt()
    assert "seedance-mv" in prompt
    assert "media.audio_analyze" in prompt
    assert "media.mix_audio" in prompt
