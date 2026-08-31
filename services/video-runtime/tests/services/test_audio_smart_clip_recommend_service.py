"""单元测试：上传音频 Smart Clip 推荐（service + endpoint + 共享 flow）。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.agent.audio_smart_clip_endpoints import recommend_upload_smart_clip
from app.exceptions import BusinessException, BusinessExceptionCode
from app.models.video_state import AudioTranscription
from app.services.agent.video.audio_smart_clip_recommend_service import (
    recommend_upload_audio_smart_clip,
)
from app.services.agent.video.music_smart_clip_service import SmartClipAnalysis, SmartClipSelection
from app.services.agent.video.smart_clip_flow import (
    build_upload_crop_recommend_payload,
    transcribe_audio_for_analysis,
)


def _fake_transcription(duration: float = 120.0) -> AudioTranscription:
    return AudioTranscription(
        task="transcribe",
        language="en",
        duration=duration,
        text="hello",
        segments=[],
        audio_url="https://cdn.example.com/test.mp3",
        is_instrumental=False,
    )


def _fake_analysis(duration: float = 120.0, target: float = 30.0) -> SmartClipAnalysis:
    rec = SmartClipSelection(
        start_sec=10.0,
        end_sec=40.0,
        fade_in_sec=0.0,
        fade_out_sec=0.5,
        target_duration_sec=target,
        actual_duration_sec=30.0,
        duration_error_sec=0.0,
        reasoning="test reasoning",
    )
    return SmartClipAnalysis(
        audio_duration_sec=duration,
        target_duration_sec=target,
        recommended=rec,
        fallback_used=False,
        method="heuristic_center",
    )


def _ready_payload() -> dict:
    return build_upload_crop_recommend_payload(_fake_analysis())


@pytest.mark.asyncio
async def test_transcribe_audio_for_analysis_fast_path_uses_gemini():
    with patch(
        "app.tools.transcribe.gemini.transcribe_audio_with_gemini",
        new=AsyncMock(return_value=_fake_transcription()),
    ) as gemini_mock:
        out = await transcribe_audio_for_analysis(
            "https://cdn.example.com/a.mp3",
            filename="a.mp3",
            fast_path=True,
        )
    assert out is not None
    gemini_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_recommend_upload_audio_smart_clip_success():
    content = b"fake-audio-bytes"
    with patch(
        "app.services.agent.video.audio_smart_clip_recommend_service.s3_utils.upload_audio",
        new=AsyncMock(return_value="https://cdn.example.com/upload.mp3"),
    ), patch(
        "app.services.agent.video.audio_smart_clip_recommend_service.transcribe_audio_for_analysis",
        new=AsyncMock(return_value=_fake_transcription(120.0)),
    ) as transcribe_mock, patch(
        "app.services.agent.video.audio_smart_clip_recommend_service.run_smart_clip_analysis",
        new=AsyncMock(return_value=_fake_analysis(120.0, 30.0)),
    ):
        out = await recommend_upload_audio_smart_clip(
            content,
            filename="song.mp3",
            content_type="audio/mpeg",
            target_duration_sec=30.0,
        )

    assert out["status"] == "ready"
    assert out["recommended"]["start_sec"] == 10.0
    assert out["recommended"]["end_sec"] == 40.0
    assert "reasoning" not in out["recommended"]
    assert "peaks" not in out
    transcribe_mock.assert_awaited_once()
    assert transcribe_mock.await_args.kwargs["fast_path"] is True


@pytest.mark.asyncio
async def test_recommend_upload_audio_smart_clip_transcribe_failed():
    content = b"fake-audio-bytes"
    with patch(
        "app.services.agent.video.audio_smart_clip_recommend_service.s3_utils.upload_audio",
        new=AsyncMock(return_value="https://cdn.example.com/upload.mp3"),
    ), patch(
        "app.services.agent.video.audio_smart_clip_recommend_service.transcribe_audio_for_analysis",
        new=AsyncMock(return_value=None),
    ):
        out = await recommend_upload_audio_smart_clip(
            content,
            filename="song.wav",
            content_type="audio/wav",
            target_duration_sec=15.0,
        )

    assert out["status"] == "failed"
    assert out["recommended"] is None


@pytest.mark.asyncio
async def test_recommend_upload_audio_smart_clip_rejects_empty_file():
    with pytest.raises(BusinessException) as exc:
        await recommend_upload_audio_smart_clip(
            b"",
            filename="song.mp3",
            content_type="audio/mpeg",
            target_duration_sec=30.0,
        )
    assert exc.value.error_code == BusinessExceptionCode.BUSINESS_ERROR


@pytest.mark.asyncio
async def test_recommend_upload_audio_smart_clip_rejects_non_audio():
    with pytest.raises(BusinessException) as exc:
        await recommend_upload_audio_smart_clip(
            b"not-audio",
            filename="readme.txt",
            content_type="text/plain",
            target_duration_sec=30.0,
        )
    assert "仅支持音频" in str(exc.value)


@pytest.mark.asyncio
async def test_recommend_upload_audio_smart_clip_rejects_invalid_target():
    with pytest.raises(BusinessException):
        await recommend_upload_audio_smart_clip(
            b"fake-audio-bytes",
            filename="song.mp3",
            content_type="audio/mpeg",
            target_duration_sec=0.0,
        )


@pytest.mark.asyncio
async def test_recommend_upload_audio_smart_clip_passthrough_short_audio():
    content = b"fake-audio-bytes"
    with patch(
        "app.services.agent.video.audio_smart_clip_recommend_service.s3_utils.upload_audio",
        new=AsyncMock(return_value="https://cdn.example.com/upload.mp3"),
    ), patch(
        "app.services.agent.video.audio_smart_clip_recommend_service.transcribe_audio_for_analysis",
        new=AsyncMock(return_value=_fake_transcription(14.0)),
    ):
        out = await recommend_upload_audio_smart_clip(
            content,
            filename="song.mp3",
            content_type="audio/mpeg",
            target_duration_sec=15.0,
        )

    assert out["status"] == "ready"
    assert out["recommended"]["start_sec"] == 0.0
    assert out["recommended"]["end_sec"] == 14.0


def _make_upload_file(content: bytes, filename: str, content_type: str) -> MagicMock:
    upload = MagicMock()
    upload.read = AsyncMock(return_value=content)
    upload.filename = filename
    upload.content_type = content_type
    return upload


@pytest.mark.asyncio
async def test_endpoint_recommend_upload_smart_clip_success():
    upload = _make_upload_file(b"fake-audio", "song.mp3", "audio/mpeg")
    with patch(
        "app.api.agent.audio_smart_clip_endpoints.recommend_upload_audio_smart_clip",
        new=AsyncMock(return_value=_ready_payload()),
    ):
        resp = await recommend_upload_smart_clip(
            file=upload,
            target_duration_sec=30.0,
            user_id="user-test-1",
        )

    assert resp.code == 0
    assert resp.data["status"] == "ready"
    assert resp.data["recommended"]["start_sec"] == 10.0
    upload.read.assert_awaited_once()


@pytest.mark.asyncio
async def test_endpoint_recommend_upload_smart_clip_invalid_target():
    upload = _make_upload_file(b"fake-audio", "song.mp3", "audio/mpeg")
    with pytest.raises(BusinessException) as exc:
        await recommend_upload_smart_clip(
            file=upload,
            target_duration_sec=0.0,
            user_id="user-test-1",
        )
    assert "target_duration_sec" in str(exc.value)


@pytest.mark.asyncio
async def test_endpoint_recommend_upload_smart_clip_propagates_service_error():
    upload = _make_upload_file(b"", "song.mp3", "audio/mpeg")
    with patch(
        "app.api.agent.audio_smart_clip_endpoints.recommend_upload_audio_smart_clip",
        new=AsyncMock(
            side_effect=BusinessException(BusinessExceptionCode.BUSINESS_ERROR, "文件为空"),
        ),
    ):
        with pytest.raises(BusinessException) as exc:
            await recommend_upload_smart_clip(
                file=upload,
                target_duration_sec=30.0,
                user_id="user-test-1",
            )
    assert "文件为空" in str(exc.value)
