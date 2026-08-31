#!/usr/bin/env python3
"""_build_lipsync_audio_url_map：同一段 audio_segment 拆成多镜头时 lipsync 应对齐子窗口"""

from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, patch

import app.utils.video_utils as video_utils_mod

from app.models.video_state import DetailedShot, AudioTranscription, AudioSegment
from app.models.tool_enums import GenerationMode
from app.services.agent.video.video_generation_service import _build_lipsync_audio_url_map

SEG_UUID = "4b92d8e2-e5fc-4df8-853e-abc904cdccb1"
SEG_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
UNKNOWN_SEG = "00000000-0000-0000-0000-000000000099"


def _shot(shot_number: int, mode: str, duration: float, seg: str = SEG_UUID) -> DetailedShot:
    return DetailedShot(
        shot_number=shot_number,
        duration=duration,
        generation_mode=mode,
        audio_segment_ids=[seg],
        character_ids=[],
    )


@pytest.mark.asyncio
async def test_lipsync_uses_offset_after_normal_on_same_segment():
    calls: list[tuple] = []

    async def trim_side_effect(url, start_time=0, duration=0):
        calls.append((url, start_time, duration))
        return f"trimmed_{len(calls)}"

    transcription = AudioTranscription(
        task="transcribe",
        language="zh",
        duration=100.0,
        text="x",
        segments=[
            AudioSegment(
                uuid=SEG_UUID,
                id=1,
                start=17.3,
                end=20.6,
                duration=3.3,
                text="明天在路上",
            ),
        ],
        audio_url="https://example.com/full.mp3",
    )

    with patch(
        "app.services.agent.utils.database_utils.get_audio_transcription_from_db",
        new_callable=AsyncMock,
        return_value=transcription,
    ), patch(
        "app.crud.video.video_audio.get_music_generation_versions_by_music_generation_ids",
        new_callable=AsyncMock,
        return_value=[],
    ), patch.object(
        video_utils_mod,
        "trim_audio_clip",
        new=AsyncMock(side_effect=trim_side_effect),
    ):
        shots = [
            _shot(10, GenerationMode.NORMAL.value, 1.65),
            _shot(11, GenerationMode.LIPSYNC.value, 1.65),
        ]
        audio_map = await _build_lipsync_audio_url_map(shots, ["tx-uuid"], [])

    assert len(calls) == 2
    assert calls[0] == ("https://example.com/full.mp3", 17.3, 3.3)
    assert calls[1][0] == "trimmed_1"
    assert calls[1][1] == pytest.approx(1.65)
    assert calls[1][2] == pytest.approx(1.65)
    assert audio_map == {11: "trimmed_2"}
    assert 10 not in audio_map


@pytest.mark.asyncio
async def test_segment_with_only_normal_shots_skips_trim():
    async def trim_side_effect(url, start_time=0, duration=0):
        raise AssertionError("trim should not run when no lipsync on segment")

    transcription = AudioTranscription(
        task="transcribe",
        language="zh",
        duration=100.0,
        text="x",
        segments=[
            AudioSegment(uuid=SEG_UUID, id=1, start=0.0, end=2.0, duration=2.0, text="a"),
        ],
        audio_url="https://example.com/full.mp3",
    )

    with patch(
        "app.services.agent.utils.database_utils.get_audio_transcription_from_db",
        new_callable=AsyncMock,
        return_value=transcription,
    ), patch(
        "app.crud.video.video_audio.get_music_generation_versions_by_music_generation_ids",
        new_callable=AsyncMock,
        return_value=[],
    ), patch.object(
        video_utils_mod,
        "trim_audio_clip",
        new=AsyncMock(side_effect=trim_side_effect),
    ):
        shots = [
            _shot(1, GenerationMode.NORMAL.value, 1.0),
            _shot(2, GenerationMode.NORMAL.value, 1.0),
        ]
        audio_map = await _build_lipsync_audio_url_map(shots, ["tx-uuid"], [])

    assert audio_map == {}


@pytest.mark.asyncio
async def test_empty_transcription_uuids_returns_empty_map():
    audio_map = await _build_lipsync_audio_url_map([], [], [])
    assert audio_map == {}


@pytest.mark.asyncio
async def test_transcription_none_returns_empty_map():
    with patch(
        "app.services.agent.utils.database_utils.get_audio_transcription_from_db",
        new_callable=AsyncMock,
        return_value=None,
    ):
        audio_map = await _build_lipsync_audio_url_map(
            [_shot(1, GenerationMode.LIPSYNC.value, 1.0)], ["tx-uuid"], []
        )
    assert audio_map == {}


@pytest.mark.asyncio
async def test_two_lipsync_same_segment_offsets():
    calls: list[tuple] = []

    async def trim_side_effect(url, start_time=0, duration=0):
        calls.append((url, start_time, duration))
        return f"t{len(calls)}"

    transcription = AudioTranscription(
        task="transcribe",
        language="zh",
        duration=50.0,
        text="x",
        segments=[
            AudioSegment(uuid=SEG_UUID, id=1, start=0.0, end=4.0, duration=4.0, text="ab"),
        ],
        audio_url="https://example.com/full.mp3",
    )

    with patch(
        "app.services.agent.utils.database_utils.get_audio_transcription_from_db",
        new_callable=AsyncMock,
        return_value=transcription,
    ), patch(
        "app.crud.video.video_audio.get_music_generation_versions_by_music_generation_ids",
        new_callable=AsyncMock,
        return_value=[],
    ), patch.object(
        video_utils_mod,
        "trim_audio_clip",
        new=AsyncMock(side_effect=trim_side_effect),
    ):
        shots = [
            _shot(3, GenerationMode.LIPSYNC.value, 1.8),
            _shot(4, GenerationMode.LIPSYNC.value, 1.8),
        ]
        audio_map = await _build_lipsync_audio_url_map(shots, ["tx-uuid"], [])

    assert len(calls) == 3
    assert calls[0] == ("https://example.com/full.mp3", 0.0, 4.0)
    assert calls[1] == ("t1", 0.0, 1.8)
    assert calls[2] == ("t1", 1.8, 1.8)
    assert audio_map == {3: "t2", 4: "t3"}


@pytest.mark.asyncio
async def test_non_lipsync_modes_still_advance_offset():
    """empty_shot / normal 不占映射，但推进 offset。"""
    calls: list[tuple] = []

    async def trim_side_effect(url, start_time=0, duration=0):
        calls.append((url, start_time, duration))
        return f"t{len(calls)}"

    transcription = AudioTranscription(
        task="transcribe",
        language="zh",
        duration=50.0,
        text="x",
        segments=[
            AudioSegment(uuid=SEG_UUID, id=1, start=0.0, end=10.0, duration=10.0, text="x"),
        ],
        audio_url="https://example.com/full.mp3",
    )

    with patch(
        "app.services.agent.utils.database_utils.get_audio_transcription_from_db",
        new_callable=AsyncMock,
        return_value=transcription,
    ), patch(
        "app.crud.video.video_audio.get_music_generation_versions_by_music_generation_ids",
        new_callable=AsyncMock,
        return_value=[],
    ), patch.object(
        video_utils_mod,
        "trim_audio_clip",
        new=AsyncMock(side_effect=trim_side_effect),
    ):
        shots = [
            _shot(1, GenerationMode.LIPSYNC.value, 1.0),
            _shot(2, GenerationMode.EMPTY_SHOT.value, 0.5),
            _shot(3, GenerationMode.NORMAL.value, 1.0),
            _shot(4, GenerationMode.LIPSYNC.value, 1.0),
        ]
        audio_map = await _build_lipsync_audio_url_map(shots, ["tx-uuid"], [])

    # segment trim + lipsync@1 + lipsync@4 with offset 1+0.5+1 = 2.5
    assert calls[2][0] == "t1"
    assert calls[2][1] == pytest.approx(2.5)
    assert calls[2][2] == pytest.approx(1.0)
    assert set(audio_map.keys()) == {1, 4}


@pytest.mark.asyncio
async def test_shots_sorted_by_shot_number_not_input_order():
    calls: list[tuple] = []

    async def trim_side_effect(url, start_time=0, duration=0):
        calls.append((url, start_time, duration))
        return f"t{len(calls)}"

    transcription = AudioTranscription(
        task="transcribe",
        language="zh",
        duration=50.0,
        text="x",
        segments=[
            AudioSegment(uuid=SEG_UUID, id=1, start=0.0, end=3.0, duration=3.0, text="x"),
        ],
        audio_url="https://example.com/full.mp3",
    )

    with patch(
        "app.services.agent.utils.database_utils.get_audio_transcription_from_db",
        new_callable=AsyncMock,
        return_value=transcription,
    ), patch(
        "app.crud.video.video_audio.get_music_generation_versions_by_music_generation_ids",
        new_callable=AsyncMock,
        return_value=[],
    ), patch.object(
        video_utils_mod,
        "trim_audio_clip",
        new=AsyncMock(side_effect=trim_side_effect),
    ):
        shots = [
            _shot(2, GenerationMode.LIPSYNC.value, 1.0),
            _shot(1, GenerationMode.NORMAL.value, 2.0),
        ]
        await _build_lipsync_audio_url_map(shots, ["tx-uuid"], [])

    assert calls[1][0] == "t1"
    assert calls[1][1] == pytest.approx(2.0)
    assert calls[1][2] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_music_url_skips_full_audio_segment_trim():
    """有 music_url 时不再从 full_audio 裁 segment，仅对 lipsync 从 music 上裁。"""
    calls: list[tuple] = []

    async def trim_side_effect(url, start_time=0, duration=0):
        calls.append((url, start_time, duration))
        return f"t{len(calls)}"

    transcription = AudioTranscription(
        task="transcribe",
        language="zh",
        duration=50.0,
        text="x",
        segments=[
            AudioSegment(uuid=SEG_UUID, id=1, start=1.0, end=4.0, duration=3.0, text="x"),
        ],
        audio_url="https://example.com/full.mp3",
    )

    music_ver = SimpleNamespace(
        music_generation_id="mg-1",
        version_number=1,
        music_url="https://cdn.example.com/seg_music.mp3",
        audio_segment_ids=[SEG_UUID],
    )

    with patch(
        "app.services.agent.utils.database_utils.get_audio_transcription_from_db",
        new_callable=AsyncMock,
        return_value=transcription,
    ), patch(
        "app.crud.video.video_audio.get_music_generation_versions_by_music_generation_ids",
        new_callable=AsyncMock,
        return_value=[music_ver],
    ), patch.object(
        video_utils_mod,
        "trim_audio_clip",
        new=AsyncMock(side_effect=trim_side_effect),
    ):
        shots = [
            _shot(10, GenerationMode.NORMAL.value, 1.0),
            _shot(11, GenerationMode.LIPSYNC.value, 2.0),
        ]
        await _build_lipsync_audio_url_map(shots, ["tx-uuid"], ["mg-1"])

    assert len(calls) == 1
    assert calls[0][0] == "https://cdn.example.com/seg_music.mp3"
    assert calls[0][1] == pytest.approx(1.0)
    assert calls[0][2] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_unknown_segment_skipped_other_segment_ok():
    calls: list[tuple] = []

    async def trim_side_effect(url, start_time=0, duration=0):
        calls.append((url, start_time, duration))
        return f"t{len(calls)}"

    transcription = AudioTranscription(
        task="transcribe",
        language="zh",
        duration=50.0,
        text="x",
        segments=[
            AudioSegment(uuid=SEG_B, id=1, start=0.0, end=2.0, duration=2.0, text="ok"),
        ],
        audio_url="https://example.com/full.mp3",
    )

    with patch(
        "app.services.agent.utils.database_utils.get_audio_transcription_from_db",
        new_callable=AsyncMock,
        return_value=transcription,
    ), patch(
        "app.crud.video.video_audio.get_music_generation_versions_by_music_generation_ids",
        new_callable=AsyncMock,
        return_value=[],
    ), patch.object(
        video_utils_mod,
        "trim_audio_clip",
        new=AsyncMock(side_effect=trim_side_effect),
    ):
        shots = [
            _shot(1, GenerationMode.LIPSYNC.value, 2.0, seg=UNKNOWN_SEG),
            _shot(2, GenerationMode.LIPSYNC.value, 2.0, seg=SEG_B),
        ]
        audio_map = await _build_lipsync_audio_url_map(shots, ["tx-uuid"], [])

    assert len(calls) == 2
    assert calls[0] == ("https://example.com/full.mp3", 0.0, 2.0)
    assert 1 not in audio_map
    assert audio_map[2] == "t2"


@pytest.mark.asyncio
async def test_shot_without_audio_segment_ids_ignored():
    calls: list[tuple] = []

    async def trim_side_effect(url, start_time=0, duration=0):
        calls.append((url, start_time, duration))
        return f"t{len(calls)}"

    transcription = AudioTranscription(
        task="transcribe",
        language="zh",
        duration=50.0,
        text="x",
        segments=[
            AudioSegment(uuid=SEG_UUID, id=1, start=0.0, end=2.0, duration=2.0, text="x"),
        ],
        audio_url="https://example.com/full.mp3",
    )

    no_seg = DetailedShot(
        shot_number=0,
        duration=99.0,
        generation_mode=GenerationMode.LIPSYNC.value,
        audio_segment_ids=None,
        character_ids=[],
    )

    with patch(
        "app.services.agent.utils.database_utils.get_audio_transcription_from_db",
        new_callable=AsyncMock,
        return_value=transcription,
    ), patch(
        "app.crud.video.video_audio.get_music_generation_versions_by_music_generation_ids",
        new_callable=AsyncMock,
        return_value=[],
    ), patch.object(
        video_utils_mod,
        "trim_audio_clip",
        new=AsyncMock(side_effect=trim_side_effect),
    ):
        shots = [
            no_seg,
            _shot(1, GenerationMode.LIPSYNC.value, 2.0),
        ]
        audio_map = await _build_lipsync_audio_url_map(shots, ["tx-uuid"], [])

    assert 0 not in audio_map
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_build_lipsync_audio_url_map_from_narrations():
    from app.services.agent.video.video_generation_service import (
        _build_lipsync_audio_url_map_from_narrations,
    )

    shots = [
        _shot(1, GenerationMode.LIPSYNC.value, 2.0),
        _shot(2, GenerationMode.NORMAL.value, 1.0),
        _shot(3, GenerationMode.LIPSYNC.value, 3.0),
    ]
    versions = [
        SimpleNamespace(
            shot_number=1,
            audio_url="https://example.com/n1.mp3",
            duration=2.0,
            success=True,
            version_number=1,
        ),
        SimpleNamespace(
            shot_number=3,
            audio_url="https://example.com/n3.mp3",
            duration=3.0,
            success=True,
            version_number=1,
        ),
    ]

    with patch(
        "app.crud.video.video_audio.get_narration_versions_by_narration_ids",
        new_callable=AsyncMock,
        return_value=versions,
    ):
        audio_map = await _build_lipsync_audio_url_map_from_narrations(shots, ["n1", "n3"])

    assert audio_map == {
        1: "https://example.com/n1.mp3",
        3: "https://example.com/n3.mp3",
    }
