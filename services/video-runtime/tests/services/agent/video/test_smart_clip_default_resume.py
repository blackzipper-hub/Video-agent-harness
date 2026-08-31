"""resume 未显式传 smart_clip 时，after_music 默认采用 AI 推荐裁切。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent.agent_router_service import (
    build_recommended_smart_clip_decision,
    inject_default_smart_clip_into_resume_dict,
)


def test_build_recommended_smart_clip_decision():
    sc = {
        "music_generation_uuid": "mg-1",
        "recommended": {
            "start_sec": 10.0,
            "end_sec": 25.0,
            "fade_in_sec": 0.5,
            "fade_out_sec": 1.0,
        },
    }
    d = build_recommended_smart_clip_decision(sc)
    assert d["accepted"] is True
    assert d["start_sec"] == 10.0
    assert d["end_sec"] == 25.0
    assert d["music_generation_uuid"] == "mg-1"


@pytest.mark.asyncio
async def test_inject_default_when_after_music_ready():
    rd = {"run_id": "r1", "interrupt_msgid": 42}
    msg = MagicMock()
    msg.event_data = {
        "interrupt_data": {
            "step": "after_music",
            "smart_clip": {
                "status": "ready",
                "music_generation_uuid": "mg-abc",
                "recommended": {"start_sec": 1.0, "end_sec": 16.0, "fade_in_sec": 0, "fade_out_sec": 0},
            },
        }
    }
    with patch(
        "app.crud.conversation.async_get_message_by_id",
        new_callable=AsyncMock,
        return_value=msg,
    ):
        out = await inject_default_smart_clip_into_resume_dict(rd)
    assert out["smart_clip"]["accepted"] is True
    assert out["smart_clip"]["start_sec"] == 1.0
    assert out["smart_clip"]["end_sec"] == 16.0


@pytest.mark.asyncio
async def test_inject_skipped_when_not_after_music():
    rd = {"run_id": "r1", "interrupt_msgid": 42}
    msg = MagicMock()
    msg.event_data = {
        "interrupt_data": {
            "step": "after_outline",
            "smart_clip": {"status": "ready", "recommended": {"start_sec": 0, "end_sec": 10}},
        }
    }
    with patch(
        "app.crud.conversation.async_get_message_by_id",
        new_callable=AsyncMock,
        return_value=msg,
    ):
        out = await inject_default_smart_clip_into_resume_dict(rd)
    assert "smart_clip" not in out


@pytest.mark.asyncio
async def test_inject_skipped_when_explicit_reject():
    rd = {
        "run_id": "r1",
        "interrupt_msgid": 42,
        "smart_clip": {"accepted": False, "music_generation_uuid": "mg-1"},
    }
    out = await inject_default_smart_clip_into_resume_dict(rd)
    assert out["smart_clip"]["accepted"] is False
