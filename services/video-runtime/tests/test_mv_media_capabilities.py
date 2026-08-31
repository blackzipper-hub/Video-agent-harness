"""Broad case coverage for seedance-mv media capabilities (analyze/trim/mix)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.capabilities.models import CapabilityRegistry
from app.chat.v2.capability_loader import build_registry
from app.chat.v2.executors import CapabilityExecutor
from app.chat.v2.host_gateway import HostGateway, HostGatewayError
from app.chat.v2.models import AgentRun, ArtifactVersion, Task
from app.chat.v2.mv_audio import (
    build_audiomap,
    plan_seedance_segments,
    resolve_master_window,
)
from app.chat.v2.skill_catalog import SkillCatalog
from app.chat.v2.workflows import (
    WORKFLOWS,
    capability_requires_workflow,
    configure_workflows,
    inject_workflow_parameters,
    is_workflow_skill,
)


def _music(uri: str = "http://localhost/files/song.mp3") -> ArtifactVersion:
    return ArtifactVersion(
        id=str(uuid4()),
        artifact_id=str(uuid4()),
        project_id="p",
        type="music",
        version=1,
        produced_by_task_id="t-music",
        title="song",
        summary="",
        uri=uri,
        metadata={},
    )


def _video(uri: str = "http://localhost/files/clip.mp4") -> ArtifactVersion:
    return ArtifactVersion(
        id=str(uuid4()),
        artifact_id=str(uuid4()),
        project_id="p",
        type="video",
        version=1,
        produced_by_task_id="t-video",
        title="clip",
        summary="",
        uri=uri,
        metadata={},
    )


def _assert_segments_cover(window_start: float, window_end: float, segments: list[dict]):
    assert segments
    assert segments[0]["start_sec"] == pytest.approx(window_start, abs=1e-3)
    assert segments[-1]["end_sec"] == pytest.approx(window_end, abs=1e-3)
    total = sum(s["duration_sec"] for s in segments)
    assert total == pytest.approx(window_end - window_start, abs=1e-2)
    for i in range(1, len(segments)):
        assert segments[i]["start_sec"] == pytest.approx(
            segments[i - 1]["end_sec"], abs=1e-3
        )
    assert all(s["duration_sec"] <= 15.0 + 1e-6 for s in segments)


# ─── pure planning edge cases ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "duration,target,expected",
    [
        (30.0, None, (0.0, 30.0)),
        (30.0, 30.0, (0.0, 30.0)),
        (30.0, 45.0, (0.0, 30.0)),  # target longer than track → full
        (100.0, 20.0, (40.0, 60.0)),
        (15.0, 15.0, (0.0, 15.0)),
    ],
)
def test_resolve_master_window_matrix(duration, target, expected):
    assert resolve_master_window(duration, target_duration_sec=target) == expected


def test_resolve_master_window_clamps_end_past_duration():
    start, end = resolve_master_window(50.0, start_sec=40.0, end_sec=999.0)
    assert (start, end) == (40.0, 50.0)


def test_resolve_master_window_start_only_to_end():
    start, end = resolve_master_window(80.0, start_sec=20.0)
    assert (start, end) == (20.0, 80.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start_sec": -1.0},
        {"start_sec": 10.0, "end_sec": 10.0},
        {"start_sec": 50.0, "end_sec": 60.0},  # start >= duration(30)
        {"target_duration_sec": 0.0},
        {"target_duration_sec": -5.0},
    ],
)
def test_resolve_master_window_rejects_invalid(kwargs):
    with pytest.raises(ValueError):
        resolve_master_window(30.0, **kwargs)


@pytest.mark.parametrize(
    "window",
    [0.5, 4.0, 15.0, 15.001, 16.0, 29.0, 30.0, 45.0, 47.0, 60.0, 61.0, 180.0],
)
def test_plan_seedance_segments_invariants_for_many_lengths(window):
    segs = plan_seedance_segments(window)
    _assert_segments_cover(0.0, window, segs)
    if window >= 4.0:
        # Prefer no sub-4s pieces when the whole window can support it.
        assert all(s["duration_sec"] >= 4.0 - 1e-6 or len(segs) == 1 for s in segs)


def test_plan_seedance_segments_custom_max():
    segs = plan_seedance_segments(40.0, max_segment_sec=10.0, min_segment_sec=4.0)
    assert len(segs) == 4
    assert all(s["duration_sec"] == pytest.approx(10.0) for s in segs)


def test_plan_seedance_segments_rejects_bad_bounds():
    with pytest.raises(ValueError):
        plan_seedance_segments(10.0, max_segment_sec=3.0, min_segment_sec=5.0)
    with pytest.raises(ValueError):
        plan_seedance_segments(0.0)
    with pytest.raises(ValueError):
        plan_seedance_segments(-1.0)


def test_build_audiomap_shape():
    segs = plan_seedance_segments(30.0, window_start_sec=10.0)
    am = build_audiomap(
        audio_url="http://x/a.mp3",
        audio_duration_sec=100.0,
        window_start_sec=10.0,
        window_end_sec=40.0,
        segments=segs,
        method="center_window",
    )
    assert am["segment_count"] == len(segs)
    assert am["master"]["duration_sec"] == pytest.approx(30.0)
    assert am["alignment"]["spine"] == "music"


# ─── host gateway: analyze ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_full_track_short_song(monkeypatch):
    async def _info(_url):
        return {"duration": 12.5}

    monkeypatch.setattr("app.utils.media_service_client.audio_info", _info)
    result = await HostGateway().media_audio_analyze({
        "audio_url": "http://localhost/files/short.mp3",
    })
    assert result["method"] == "full_track"
    assert result["segment_count"] == 1
    assert result["master"]["duration_sec"] == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_analyze_explicit_start_end(monkeypatch):
    async def _info(_url):
        return {"duration": 200.0}

    monkeypatch.setattr("app.utils.media_service_client.audio_info", _info)
    result = await HostGateway().media_audio_analyze({
        "audio_url": "http://localhost/files/long.mp3",
        "start_sec": 45.0,
        "end_sec": 75.0,
    })
    assert result["master"]["start_sec"] == pytest.approx(45.0)
    assert result["master"]["end_sec"] == pytest.approx(75.0)
    assert result["segment_count"] == 2
    _assert_segments_cover(45.0, 75.0, result["segments"])


@pytest.mark.asyncio
async def test_analyze_custom_max_segment(monkeypatch):
    async def _info(_url):
        return {"duration": 40.0}

    monkeypatch.setattr("app.utils.media_service_client.audio_info", _info)
    result = await HostGateway().media_audio_analyze({
        "audio_url": "http://localhost/files/a.mp3",
        "max_segment_sec": 10,
    })
    assert result["segment_count"] == 4
    assert all(s["duration_sec"] <= 10.0 + 1e-6 for s in result["segments"])


@pytest.mark.asyncio
async def test_analyze_zero_duration_rejected(monkeypatch):
    async def _info(_url):
        return {"duration": 0}

    monkeypatch.setattr("app.utils.media_service_client.audio_info", _info)
    with pytest.raises(HostGatewayError, match="duration"):
        await HostGateway().media_audio_analyze({
            "audio_url": "http://localhost/files/a.mp3",
        })


@pytest.mark.asyncio
async def test_analyze_smart_clip_failure_falls_back_to_center(monkeypatch):
    async def _info(_url):
        return {"duration": 120.0}

    async def _boom(*_a, **_k):
        raise RuntimeError("gemini down")

    monkeypatch.setattr("app.utils.media_service_client.audio_info", _info)
    monkeypatch.setattr(
        "app.services.agent.video.music_smart_clip_service.analyze_music_smart_clip",
        _boom,
    )
    result = await HostGateway().media_audio_analyze({
        "audio_url": "http://localhost/files/a.mp3",
        "target_duration_sec": 60.0,
        "transcription": {"sections": []},
    })
    assert result["method"] == "center_window_fallback"
    assert result["master"]["duration_sec"] == pytest.approx(60.0)
    assert result["master"]["start_sec"] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_analyze_target_without_transcription_uses_center(monkeypatch):
    async def _info(_url):
        return {"duration": 120.0}

    monkeypatch.setattr("app.utils.media_service_client.audio_info", _info)
    result = await HostGateway().media_audio_analyze({
        "audio_url": "http://localhost/files/a.mp3",
        "target_duration_sec": 45.0,
    })
    assert result["method"] == "center_window"
    assert result["smart_clip"] is None


@pytest.mark.asyncio
async def test_analyze_dispatch_requires_grant(monkeypatch):
    async def _info(_url):
        return {"duration": 10.0}

    monkeypatch.setattr("app.utils.media_service_client.audio_info", _info)
    with pytest.raises(HostGatewayError, match="not granted"):
        await HostGateway().dispatch(
            "media.audio_analyze",
            {"audio_url": "http://localhost/files/a.mp3"},
            allowed_capabilities=["media.concat"],
        )
    ok = await HostGateway().dispatch(
        "media.audio_analyze",
        {"audio_url": "http://localhost/files/a.mp3"},
        allowed_capabilities=["media.audio_analyze"],
    )
    assert ok["segment_count"] == 1


# ─── host gateway: trim ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trim_rejects_missing_duration():
    with pytest.raises(HostGatewayError, match="duration"):
        await HostGateway().media_audio_trim({
            "audio_url": "http://localhost/files/a.mp3",
            "start": 0,
        })


@pytest.mark.asyncio
async def test_trim_rejects_negative_start():
    with pytest.raises(HostGatewayError, match="start"):
        await HostGateway().media_audio_trim({
            "audio_url": "http://localhost/files/a.mp3",
            "start": -1,
            "duration": 5,
        })


@pytest.mark.asyncio
async def test_trim_rejects_zero_duration():
    with pytest.raises(HostGatewayError, match="duration"):
        await HostGateway().media_audio_trim({
            "audio_url": "http://localhost/files/a.mp3",
            "duration": 0,
        })


@pytest.mark.asyncio
async def test_trim_empty_result_url(monkeypatch):
    async def _trim(*_a, **_k):
        return {"result_url": None}

    monkeypatch.setattr("app.utils.media_service_client.audio_trim", _trim)
    with pytest.raises(HostGatewayError, match="no result_url"):
        await HostGateway().media_audio_trim({
            "audio_url": "http://localhost/files/a.mp3",
            "duration": 10,
        })


@pytest.mark.asyncio
async def test_trim_dispatch_alias_uri(monkeypatch):
    calls = {}

    async def _trim(audio_url, start, duration, run_id):
        calls["audio_url"] = audio_url
        return {"result_url": "http://localhost/files/out.mp3"}

    monkeypatch.setattr("app.utils.media_service_client.audio_trim", _trim)
    result = await HostGateway().dispatch(
        "media.audio_trim",
        {"uri": "http://localhost/files/in.mp3", "duration": 8, "start": 2},
        allowed_capabilities=["media.audio_trim"],
    )
    assert calls["audio_url"].endswith("in.mp3")
    assert result["duration_sec"] == 8


# ─── host gateway: mix ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mix_default_mode_is_replace(monkeypatch):
    called = {"add": 0, "mix": 0}

    async def _add(*_a, **_k):
        called["add"] += 1
        return {"result_url": "http://localhost/files/r.mp4"}

    async def _mix(*_a, **_k):
        called["mix"] += 1
        return {"result_url": "http://localhost/files/m.mp4"}

    monkeypatch.setattr("app.utils.media_service_client.video_add_audio", _add)
    monkeypatch.setattr("app.utils.media_service_client.video_mix_audio", _mix)
    result = await HostGateway().media_mix_audio({
        "video_url": "http://localhost/files/v.mp4",
        "music_url": "http://localhost/files/a.mp3",
    })
    assert called == {"add": 1, "mix": 0}
    assert result["mode"] == "replace"


@pytest.mark.asyncio
async def test_mix_rejects_bad_mode():
    with pytest.raises(HostGatewayError, match="mode"):
        await HostGateway().media_mix_audio({
            "video_url": "http://localhost/files/v.mp4",
            "audio_url": "http://localhost/files/a.mp3",
            "mode": "duck",
        })


@pytest.mark.asyncio
async def test_mix_requires_both_urls():
    with pytest.raises(HostGatewayError, match="video_url"):
        await HostGateway().media_mix_audio({"audio_url": "http://x/a.mp3"})
    with pytest.raises(HostGatewayError, match="audio_url"):
        await HostGateway().media_mix_audio({"video_url": "http://x/v.mp4"})


# ─── executor local.service wiring ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_executor_audio_analyze_from_selected_music(monkeypatch):
    async def _info(_url):
        return {"duration": 30.0}

    monkeypatch.setattr("app.utils.media_service_client.audio_info", _info)
    registry = build_registry(include_platform=True)
    executor = CapabilityExecutor(object(), registry)
    run = AgentRun(
        thread_id="t1", project_id="p1", user_id="u1",
        objective="mv", idempotency_key="k1",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="analyze",
        capability_id="media.audio_analyze", objective="analyze",
        parameters={},
    )
    result = await executor._run_local_service(
        run, task, [_music()], "idem-analyze",
        registry.get("media.audio_analyze"),
    )
    assert result.status == "completed"
    assert result.artifact["metadata"]["segment_count"] == 2
    assert result.artifact["metadata"]["master"]["duration_sec"] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_executor_audio_trim_from_parameters(monkeypatch):
    async def _trim(audio_url, start, duration, run_id):
        assert start == 15.0
        assert duration == 15.0
        return {"result_url": "http://localhost/files/seg.mp3"}

    monkeypatch.setattr("app.utils.media_service_client.audio_trim", _trim)
    registry = build_registry(include_platform=True)
    executor = CapabilityExecutor(object(), registry)
    run = AgentRun(
        thread_id="t1", project_id="p1", user_id="u1",
        objective="mv", idempotency_key="k1",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="trim",
        capability_id="media.audio_trim", objective="trim",
        parameters={
            "audio_url": "http://localhost/files/song.mp3",
            "start": 15,
            "duration": 15,
        },
    )
    result = await executor._run_local_service(
        run, task, [], "idem-trim",
        registry.get("media.audio_trim"),
    )
    assert result.artifact["uri"].endswith("seg.mp3")


@pytest.mark.asyncio
async def test_executor_audio_trim_requires_duration_or_artifact():
    registry = build_registry(include_platform=True)
    executor = CapabilityExecutor(object(), registry)
    run = AgentRun(
        thread_id="t1", project_id="p1", user_id="u1",
        objective="mv", idempotency_key="k1",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="trim",
        capability_id="media.audio_trim", objective="trim",
        parameters={"start": 0},
    )
    with pytest.raises(ValueError, match="audio_url"):
        await executor._run_local_service(
            run, task, [], "idem",
            registry.get("media.audio_trim"),
        )


@pytest.mark.asyncio
async def test_executor_mix_audio_from_selected_artifacts(monkeypatch):
    async def _add(video_url, audio_segments, run_id):
        assert video_url.endswith("clip.mp4")
        assert audio_segments[0]["audio_url"].endswith("song.mp3")
        return {"result_url": "http://localhost/files/final.mp4"}

    monkeypatch.setattr("app.utils.media_service_client.video_add_audio", _add)
    registry = build_registry(include_platform=True)
    executor = CapabilityExecutor(object(), registry)
    run = AgentRun(
        thread_id="t1", project_id="p1", user_id="u1",
        objective="mv", idempotency_key="k1",
    )
    task = Task(
        run_id=run.id, revision=1, client_key="mix",
        capability_id="media.mix_audio", objective="mix",
        parameters={"mode": "replace"},
    )
    result = await executor._run_local_service(
        run, task, [_video(), _music()], "idem-mix",
        registry.get("media.mix_audio"),
    )
    assert result.artifact["uri"].endswith("final.mp4")
    assert result.artifact["metadata"]["mode"] == "replace"


@pytest.mark.asyncio
async def test_executor_analyze_trim_mix_pipeline(monkeypatch):
    """Simulate MV spine: analyze → trim each segment → mix master."""
    async def _info(_url):
        return {"duration": 45.0}

    trims: list[tuple[float, float]] = []

    async def _trim(audio_url, start, duration, run_id):
        trims.append((start, duration))
        return {"result_url": f"http://localhost/files/seg-{len(trims)}.mp3"}

    async def _add(video_url, audio_segments, run_id):
        return {"result_url": "http://localhost/files/mv-final.mp4"}

    monkeypatch.setattr("app.utils.media_service_client.audio_info", _info)
    monkeypatch.setattr("app.utils.media_service_client.audio_trim", _trim)
    monkeypatch.setattr("app.utils.media_service_client.video_add_audio", _add)

    gw = HostGateway()
    audiomap = await gw.media_audio_analyze({
        "audio_url": "http://localhost/files/song.mp3",
    })
    assert audiomap["segment_count"] == 3
    for seg in audiomap["segments"]:
        clip = await gw.media_audio_trim({
            "audio_url": audiomap["audio_url"],
            "start": seg["start_sec"],
            "duration": seg["duration_sec"],
        })
        assert "seg-" in clip["uri"]

    master = await gw.media_audio_trim({
        "audio_url": audiomap["audio_url"],
        "start": audiomap["master"]["start_sec"],
        "duration": audiomap["master"]["duration_sec"],
    })
    final = await gw.media_mix_audio({
        "video_url": "http://localhost/files/concat.mp4",
        "audio_url": master["uri"],
        "mode": "replace",
    })
    assert final["uri"].endswith("mv-final.mp4")
    assert len(trims) == audiomap["segment_count"] + 1
    assert sum(d for _, d in trims[: audiomap["segment_count"]]) == pytest.approx(45.0)


# ─── artifact audio collection ───────────────────────────────────────────────


def test_collect_audio_from_extension_when_type_generic():
    selected = [
        ArtifactVersion(
            id="1", artifact_id="a", project_id="p", type="file", version=1,
            produced_by_task_id="t", title="x", summary="",
            uri="http://localhost/files/track.wav", metadata={},
        )
    ]
    assert CapabilityExecutor._collect_artifact_audio_url(selected).endswith(".wav")


def test_collect_audio_from_metadata_music_url():
    selected = [
        ArtifactVersion(
            id="1", artifact_id="a", project_id="p", type="music", version=1,
            produced_by_task_id="t", title="x", summary="",
            uri="", metadata={"music_url": "http://localhost/files/meta.mp3"},
        )
    ]
    assert CapabilityExecutor._collect_artifact_audio_url(selected).endswith("meta.mp3")


def test_collect_audio_returns_none_for_video_only():
    assert CapabilityExecutor._collect_artifact_audio_url([_video()]) is None


# ─── skill / workflow / registry ─────────────────────────────────────────────


def test_seedance_mv_skill_files_and_workflow_contract():
    root = Path(__file__).resolve().parents[1] / "skills" / "external" / "seedance-mv"
    assert (root / "SKILL.md").is_file()
    assert (root / "references" / "mv-beat-sync.md").is_file()
    assert (root / "references" / "mv-examples.md").is_file()

    catalog = SkillCatalog([root.parent])
    catalog.discover()
    skill = catalog.load("seedance-mv")
    text = skill.instructions
    for token in (
        "media.audio_analyze",
        "media.audio_trim",
        "media.mix_audio",
        "media.concat",
        "Never",
        "Music spine",
    ):
        assert token in text

    configure_workflows(
        SkillCatalog([
            Path(__file__).resolve().parents[1] / "skills" / "system",
            Path(__file__).resolve().parents[1] / "skills" / "builtin",
            Path(__file__).resolve().parents[1] / "skills" / "external",
        ]),
        CapabilityRegistry(),
    )
    assert is_workflow_skill("seedance-mv")
    assert is_workflow_skill("seedance2")
    spec = WORKFLOWS["seedance-mv"]
    assert set(spec.pipeline) <= set(spec.allowed_capabilities or ())
    for cap in (
        "media.audio_analyze",
        "media.audio_trim",
        "media.mix_audio",
        "api.provider.generate",
    ):
        assert cap in spec.pipeline or cap in (spec.allowed_capabilities or ())
    params = inject_workflow_parameters({}, spec)
    assert params["workflow_mode"] == "seedance_mv"
    assert params["content_category"] == "music_video"


def test_mv_capabilities_are_workflow_free_and_registered():
    registry = build_registry(include_platform=True)
    for cap_id in ("media.audio_analyze", "media.audio_trim", "media.mix_audio"):
        manifest = registry.get(cap_id)
        assert manifest.executor == "local.service"
        assert not capability_requires_workflow(cap_id)
        assert manifest.parameters_schema.get("type") == "object"


def test_seedance2_untouched_by_mv_skill():
    root = Path(__file__).resolve().parents[1] / "skills" / "external" / "seedance2"
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "media.audio_analyze" not in text
    assert "seedance-mv" not in text
