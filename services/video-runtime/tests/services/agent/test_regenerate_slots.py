"""
Regenerate 关键帧/视频：请求体验证 + 路由到共享 helper 的单元测试（无真实 DB/LLM）。

运行（示例）：
  conda run -n cuti-video-local pytest tests/services/agent/test_regenerate_slots.py -v
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.api.agent.agent_router_endpoints import (
    KeyframeRequest,
    KeyframeVersionRequest,
    RegenerateKeyframesRequest,
    RegenerateVideosRequest,
    VideoRequest,
    VideoVersionRequest,
)
from app.models.user_options import UserOption
from app.models.video_state import KeyframeVersion
from app.services.agent.video.regenerate import regenerate_pack_keyframe_result
from app.services.agent.video_agent_service import VideoAgentService


# ----- Pydantic：KeyframeRequest / VideoRequest -----


class TestKeyframeRequestValidation:
    def test_uuid_empty_requires_shot_number(self):
        with pytest.raises(ValidationError):
            KeyframeRequest(uuid="", versions=[KeyframeVersionRequest(uuid="")])

    def test_uuid_empty_ok_with_shot_number(self):
        m = KeyframeRequest(
            uuid="",
            shot_number=3,
            frame_index=1,
            versions=[KeyframeVersionRequest(uuid="")],
        )
        assert m.shot_number == 3
        assert m.frame_index == 1

    def test_nonempty_uuid_shot_optional(self):
        m = KeyframeRequest(
            uuid="kf-abc",
            versions=[KeyframeVersionRequest(uuid="ver-1")],
        )
        assert m.shot_number is None

    def test_regenerate_keyframes_request_roundtrip(self):
        body = RegenerateKeyframesRequest(
            keyframes=[
                KeyframeRequest(
                    uuid="kf-1",
                    versions=[KeyframeVersionRequest(uuid="v1", custom_prompt="x")],
                )
            ],
            thread_id="thread-1",
        )
        assert len(body.keyframes) == 1

    def test_keyframe_version_instruction_field(self):
        v = KeyframeVersionRequest(uuid="v1", instruction="更亮一些")
        assert v.instruction == "更亮一些"
        assert v.custom_prompt is None


class TestVideoRequestValidation:
    def test_uuid_empty_requires_shot_number(self):
        with pytest.raises(ValidationError):
            VideoRequest(uuid="", versions=[VideoVersionRequest(uuid="")])

    def test_uuid_empty_ok_with_shot(self):
        m = VideoRequest(uuid="", shot_number=2, versions=[VideoVersionRequest(uuid="")])
        assert m.shot_number == 2

    def test_nonempty_uuid(self):
        VideoRequest(uuid="vg-1", versions=[VideoVersionRequest(uuid="vv-1")])


# ----- video.regenerate.regenerate_keyframe_service.regenerate_pack_keyframe_result -----


class TestPackKeyframeRegenResult:
    def test_success(self):
        kf = KeyframeVersion(
            shot_number=1,
            t2i_prompt="p",
            provider="x",
            keyframe_url="https://u",
            success=True,
        )
        d = regenerate_pack_keyframe_result(kf, "new-uuid")
        assert d["success"] is True
        assert d["new_version_uuid"] == "new-uuid"
        assert d["error_msg"] is None

    def test_failure_no_uuid(self):
        kf = KeyframeVersion(
            shot_number=1,
            t2i_prompt="p",
            provider="x",
            success=False,
            error_msg="bad",
        )
        d = regenerate_pack_keyframe_result(kf, None)
        assert d["success"] is False
        assert d["new_version_uuid"] is None
        assert d["error_msg"] == "bad"


# ----- VideoAgentService.regenerate_*_by_request 路由（mock） -----


@pytest.fixture
def svc() -> VideoAgentService:
    return VideoAgentService()


@pytest.mark.asyncio
class TestRegenerateKeyframesRouting:
    async def test_with_version_uuid_calls_regenerate_keyframe_version(self, svc: VideoAgentService):
        version_db = SimpleNamespace(user_id="u1", keyframe_id="kf-1")

        async def fake_get_ver(_uuid):
            return version_db

        fake_result = {
            "success": True,
            "keyframe_version": MagicMock(keyframe_url="u"),
            "new_version_uuid": "nv",
            "storyboard_edit_record_id": None,
            "error_msg": None,
        }

        with patch("langsmith.get_current_run_tree", return_value=MagicMock(id="ls-1")):
            with patch("app.crud.error_tracking.create_task_record", new_callable=AsyncMock):
                with patch("app.crud.error_tracking.update_task_record", new_callable=AsyncMock):
                    with patch(
                        "app.crud.video.video_keyframe.get_keyframe_by_uuid",
                        new_callable=AsyncMock,
                        return_value=SimpleNamespace(conversation_id=1, thread_id="t1"),
                    ):
                        with patch(
                            "app.crud.video.video_keyframe.get_keyframe_version_by_uuid",
                            side_effect=fake_get_ver,
                        ):
                            with patch.object(
                                svc,
                                "_regenerate_keyframe_version",
                                new_callable=AsyncMock,
                                return_value=fake_result,
                            ) as m_regen:
                                with patch(
                                    "app.crud.video.video_generation.get_video_generation_by_keyframe_id",
                                    new_callable=AsyncMock,
                                    return_value=None,
                                ):
                                    req = SimpleNamespace(
                                        uuid="kf-1",
                                        frame_index=0,
                                        versions=[
                                            SimpleNamespace(
                                                uuid="ver-1",
                                                custom_prompt="cp",
                                                use_reflection=False,
                                            )
                                        ],
                                    )
                                    out = await svc.regenerate_keyframes_by_request(
                                        [req],
                                        user_id="u1",
                                        user_option=None,
                                        request_thread_id="t1",
                                    )
        m_regen.assert_awaited_once()
        assert out["successful_count"] == 1
        assert out["results"][0]["keyframe_uuid"] == "kf-1"

    async def test_without_version_uuid_calls_append_keyframe_helper(self, svc: VideoAgentService):
        with patch("langsmith.get_current_run_tree", return_value=MagicMock(id="ls-1")):
            with patch("app.crud.error_tracking.create_task_record", new_callable=AsyncMock):
                with patch("app.crud.error_tracking.update_task_record", new_callable=AsyncMock):
                    with patch(
                        "app.crud.video.video_keyframe.get_keyframe_by_uuid",
                        new_callable=AsyncMock,
                        return_value=SimpleNamespace(conversation_id=1, thread_id="t1"),
                    ):
                        with patch(
                            "app.services.agent.video.regenerate.regenerate_by_request_service.regenerate_append_keyframe_version_resolving_parent",
                            new_callable=AsyncMock,
                            return_value=(
                                "kf-new",
                                {
                                    "success": True,
                                    "keyframe_version": MagicMock(keyframe_url="u"),
                                    "new_version_uuid": "nv",
                                    "storyboard_edit_record_id": None,
                                    "error_msg": None,
                                },
                            ),
                        ) as m_append:
                            with patch(
                                "app.crud.video.video_generation.get_video_generation_by_keyframe_id",
                                new_callable=AsyncMock,
                                return_value=None,
                            ):
                                req = SimpleNamespace(
                                    uuid="kf-1",
                                    frame_index=0,
                                    versions=[
                                        SimpleNamespace(
                                            uuid="",
                                            custom_prompt=None,
                                            use_reflection=False,
                                        )
                                    ],
                                )
                                out = await svc.regenerate_keyframes_by_request(
                                    [req],
                                    user_id="u1",
                                    user_option=UserOption(),
                                    request_thread_id="t1",
                                )
        m_append.assert_awaited_once()
        assert out["successful_count"] == 1
        assert out["results"][0]["keyframe_uuid"] == "kf-new"


@pytest.mark.asyncio
class TestRegenerateVideosRouting:
    async def test_with_version_uuid_calls_regenerate_video_version(self, svc: VideoAgentService):
        version_db = SimpleNamespace(user_id="u1", video_generation_id="vg-1")

        async def fake_get_ver(_uuid):
            return version_db

        fake_result = {
            "success": True,
            "video_generation_version": MagicMock(video_url="https://v"),
            "new_version_uuid": "nv",
            "error_msg": None,
        }

        with patch("langsmith.get_current_run_tree", return_value=MagicMock(id="ls-1")):
            with patch("app.crud.error_tracking.create_task_record", new_callable=AsyncMock):
                with patch("app.crud.error_tracking.update_task_record", new_callable=AsyncMock):
                    with patch(
                        "app.crud.video.video_generation.get_video_generation_by_uuid",
                        new_callable=AsyncMock,
                        return_value=SimpleNamespace(conversation_id=1, thread_id="t1"),
                    ):
                        with patch(
                            "app.crud.video.video_generation.get_video_generation_version_by_uuid",
                            side_effect=fake_get_ver,
                        ):
                            with patch.object(
                                svc,
                                "_regenerate_video_version",
                                new_callable=AsyncMock,
                                return_value=fake_result,
                            ) as m_regen:
                                req = SimpleNamespace(
                                    uuid="vg-1",
                                    versions=[
                                        SimpleNamespace(uuid="vv-1", custom_prompt=None),
                                    ],
                                )
                                out = await svc.regenerate_videos_by_request(
                                    [req],
                                    user_id="u1",
                                    user_option=None,
                                    request_thread_id="t1",
                                )
        m_regen.assert_awaited_once()
        assert out["successful_count"] == 1

    async def test_with_video_uuid_empty_version_calls_append_helper(self, svc: VideoAgentService):
        vg_row = SimpleNamespace(
            uuid="vg-1",
            user_id="u1",
            conversation_id="1",
            thread_id="t1",
        )

        async def fake_get_vg(_uuid):
            return vg_row

        with patch("langsmith.get_current_run_tree", return_value=MagicMock(id="ls-1")):
            with patch("app.crud.error_tracking.create_task_record", new_callable=AsyncMock):
                with patch("app.crud.error_tracking.update_task_record", new_callable=AsyncMock):
                    with patch(
                        "app.crud.video.video_generation.get_video_generation_by_uuid",
                        side_effect=fake_get_vg,
                    ):
                        with patch(
                            "app.services.agent.video.regenerate.regenerate_by_request_service.regenerate_append_video_generation_version_for_row",
                            new_callable=AsyncMock,
                            return_value={
                                "success": True,
                                "video_generation_version": MagicMock(video_url="u"),
                                "new_version_uuid": "nv",
                                "error_msg": None,
                            },
                        ) as m_append:
                            req = SimpleNamespace(
                                uuid="vg-1",
                                versions=[SimpleNamespace(uuid="", custom_prompt="direct")],
                            )
                            out = await svc.regenerate_videos_by_request(
                                [req],
                                user_id="u1",
                                user_option=None,
                                request_thread_id="t1",
                            )
        assert m_append.await_count >= 1
        assert out["successful_count"] == 1

    async def test_without_video_uuid_calls_ensure_and_append_helpers(self, svc: VideoAgentService):
        vg_row = SimpleNamespace(uuid="vg-new", user_id="u1")

        with patch("langsmith.get_current_run_tree", return_value=MagicMock(id="ls-1")):
            with patch("app.crud.error_tracking.create_task_record", new_callable=AsyncMock):
                with patch("app.crud.error_tracking.update_task_record", new_callable=AsyncMock):
                    with patch(
                        "app.crud.conversation.async_get_conversation_by_thread_id",
                        new_callable=AsyncMock,
                        return_value=SimpleNamespace(id=99, user_id="u1"),
                    ):
                        with patch(
                            "app.services.agent.video.regenerate.regenerate_by_request_service.regenerate_ensure_video_generation_row_for_shot",
                            new_callable=AsyncMock,
                            return_value=vg_row,
                        ) as m_ensure:
                            with patch(
                                "app.services.agent.video.regenerate.regenerate_by_request_service.regenerate_append_video_generation_version_for_row",
                                new_callable=AsyncMock,
                                return_value={
                                    "success": True,
                                    "video_generation_version": MagicMock(video_url="u"),
                                    "new_version_uuid": "nv",
                                    "error_msg": None,
                                },
                            ) as m_append:
                                req = SimpleNamespace(
                                    uuid="",
                                    shot_number=2,
                                    versions=[SimpleNamespace(uuid="", custom_prompt=None)],
                                )
                                out = await svc.regenerate_videos_by_request(
                                    [req],
                                    user_id="u1",
                                    user_option=None,
                                    request_thread_id="t1",
                                )
        m_ensure.assert_awaited_once()
        m_append.assert_awaited_once()
        assert out["successful_count"] == 1
        assert out["results"][0]["video_uuid"] == "vg-new"
