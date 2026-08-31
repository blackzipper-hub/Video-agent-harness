"""Workflow 含旁白步但无 TTS 时仍应发 narrations_generated。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.video_state import DetailedShot, GenerationConfig
from app.services.agent.base_agent import MessageType
from app.services.agent.video.narration_generation_service import (
    _emit_empty_narrations_generated,
    narration_generation_node,
)


@pytest.mark.asyncio
async def test_emit_empty_narrations_generated_payload(monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(
        "app.services.agent.utils.prompt_utils.generate_completion_message_stream",
        AsyncMock(return_value=("本批无需单独旁白配音，旁白步骤已完成。", None)),
    )
    state = {
        "conversation_id": 1,
        "thread_id": "t1",
        "run_id": "r1",
        "detected_language": "zh",
    }
    out = await _emit_empty_narrations_generated(
        send, state, reason="no_narration_text"
    )
    assert out == {"narrations_with_versions": []}
    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    assert kwargs["event_type"] == MessageType.NARRATIONS_GENERATED
    assert kwargs["message"] == "本批无需单独旁白配音，旁白步骤已完成。"
    assert kwargs["extra_data"]["narration_count"] == 0
    assert kwargs["extra_data"]["empty"] is True
    assert kwargs["extra_data"]["reason"] == "no_narration_text"


@pytest.mark.asyncio
async def test_narration_node_emits_when_no_tts_text(monkeypatch):
    """Short Drama：generate_narration=True 但 shot.narration 为空 → 仍发完成事件。"""
    send = AsyncMock()
    monkeypatch.setattr(
        "app.services.agent.utils.prompt_utils.generate_completion_message_stream",
        AsyncMock(return_value=("无需 TTS 旁白，步骤完成。", None)),
    )
    shots = [
        DetailedShot(
            shot_number=1,
            duration=5.0,
            character_ids=[],
            narration="",
            dialogue="凯尔说：「等等。」",
            scene_description="大厅",
            lighting="冷蓝",
            is_bridge=False,
        )
    ]
    monkeypatch.setattr(
        "app.services.agent.video.narration_generation_service.get_detailed_shots_from_db",
        AsyncMock(return_value=shots),
    )
    monkeypatch.setattr(
        "app.services.agent.video.narration_generation_service.get_characters_from_db",
        AsyncMock(return_value=[]),
    )

    state = {
        "conversation_id": 1,
        "thread_id": "t1",
        "run_id": "r1",
        "shot_uuids": ["shot-1"],
        "detected_language": "zh",
        "generation_config": GenerationConfig.for_short_drama(),
        "user_input_data": MagicMock(
            user_option=MagicMock(content_category=MagicMock(value="Short Drama"))
        ),
    }
    out = await narration_generation_node(state, runtime=MagicMock(), send_event_func=send)
    assert out["narrations_with_versions"] == []
    done_calls = [
        c for c in send.await_args_list
        if c.kwargs.get("event_type") == MessageType.NARRATIONS_GENERATED
    ]
    assert done_calls
    assert done_calls[-1].kwargs.get("message") == "无需 TTS 旁白，步骤完成。"


@pytest.mark.asyncio
async def test_narration_node_skips_event_when_config_disables(monkeypatch):
    send = AsyncMock()
    state = {
        "conversation_id": 1,
        "generation_config": GenerationConfig.for_video_with_sound(),
        "shot_uuids": ["shot-1"],
    }
    out = await narration_generation_node(state, runtime=MagicMock(), send_event_func=send)
    assert out == {"narrations_with_versions": []}
    send.assert_not_awaited()
