"""media_core accepts dest URLs and execute_build step ids for the same inputs."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.video_runtime.builtin_plugins.media_core import MediaCorePlugin
from app.video_runtime.models import MediaArtifactVersion
from app.video_runtime.security import CapabilityExecutionEnvelope, CapabilityGrant
from app.capabilities.manifests.platform import platform_capabilities
from app.chat.v2.skill_catalog import SkillCatalog
from jsonschema.validators import validator_for


def _envelope(capability: str) -> CapabilityExecutionEnvelope:
    return CapabilityExecutionEnvelope(
        CapabilityGrant(
            project_id="project-1",
            session_id="session-1",
            user_id="user-1",
            plugin_id="cuti.media-core",
            capability=capability,
            allowed_capabilities=[capability],
            max_cost_usd=1,
            timeout_seconds=30,
            idempotency_key=f"key-{capability}",
            audit_id="audit-1",
            nonce="nonce-1",
            expires_at=int(time.time()) + 60,
        ),
    )


def _clip(step_id: str, uri: str) -> dict:
    return MediaArtifactVersion(
        project_id="project-1",
        artifact_id=step_id,
        type="video",
        uri=uri,
        metadata={"plan_step_id": step_id},
    ).model_dump(mode="json")


class MediaCoreDualInputTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, capability: str, parameters: dict, completed: dict | None = None):
        plugin = MediaCorePlugin()
        return await plugin.capability_handlers()[capability](
            _envelope(capability),
            {
                "build": {"id": "build-1"},
                "step": {
                    "step_id": "assembled-video",
                    "capability": capability,
                    "output_artifact_id": "assembled",
                    "output_artifact_type": "video_assembled",
                    "parameters": parameters,
                },
                "completed_artifacts": completed or {},
            },
        )

    async def test_concat_accepts_dest_video_urls(self):
        gateway = AsyncMock(return_value={"uri": "https://cdn.example/out.mp4", "video_count": 2})
        with patch("app.chat.v2.host_gateway.HostGateway.media_concat", gateway):
            result = await self._run("media.concat", {
                "video_urls": ["https://cdn.example/a.mp4", "https://cdn.example/b.mp4"],
                "transition_duration": 0,
            })
        gateway.assert_awaited_once()
        payload = gateway.await_args.args[0]
        self.assertEqual(payload["video_urls"], ["https://cdn.example/a.mp4", "https://cdn.example/b.mp4"])
        self.assertEqual(result.uri, "https://cdn.example/out.mp4")

    async def test_concat_accepts_video_steps(self):
        gateway = AsyncMock(return_value={"uri": "https://cdn.example/out.mp4", "video_count": 2})
        with patch("app.chat.v2.host_gateway.HostGateway.media_concat", gateway):
            result = await self._run(
                "media.concat",
                {"video_steps": ["shot-1-video", "shot-2-video"], "normalize": True},
                {
                    "shot-1-video": _clip("shot-1-video", "https://cdn.example/a.mp4"),
                    "shot-2-video": _clip("shot-2-video", "https://cdn.example/b.mp4"),
                },
            )
        payload = gateway.await_args.args[0]
        self.assertEqual(payload["video_urls"], ["https://cdn.example/a.mp4", "https://cdn.example/b.mp4"])
        self.assertEqual(result.uri, "https://cdn.example/out.mp4")

    async def test_mix_audio_accepts_dest_urls(self):
        gateway = AsyncMock(return_value={"uri": "https://cdn.example/mixed.mp4"})
        with patch("app.chat.v2.host_gateway.HostGateway.media_mix_audio", gateway):
            await self._run("media.mix_audio", {
                "video_url": "https://cdn.example/v.mp4",
                "audio_url": "https://cdn.example/a.mp3",
                "mode": "replace",
            })
        payload = gateway.await_args.args[0]
        self.assertEqual(payload["video_url"], "https://cdn.example/v.mp4")
        self.assertEqual(payload["audio_url"], "https://cdn.example/a.mp3")


def _schema(capability_id: str) -> dict:
    return next(item.parameters_schema for item in platform_capabilities() if item.id == capability_id)


class DestAndStepSchemaTest(unittest.TestCase):
    def test_concat_and_mix_accept_dest_urls_or_step_ids(self):
        concat = _schema("media.concat")
        mix = _schema("media.mix_audio")
        validator_for(concat)(concat).validate({
            "video_urls": ["https://cdn.example/a.mp4", "https://cdn.example/b.mp4"],
            "transition_duration": 0,
        })
        validator_for(concat)(concat).validate({
            "video_steps": ["shot-1-video", "shot-2-video"],
            "normalize": True,
        })
        validator_for(mix)(mix).validate({
            "video_url": "https://cdn.example/v.mp4",
            "audio_url": "https://cdn.example/a.mp3",
            "mode": "replace",
        })
        validator_for(mix)(mix).validate({
            "video_step": "assembled-video",
            "audio_step": "music-cut",
            "mode": "replace",
        })

    def test_hyperframes_captions_skill_teaches_caption_html(self):
        root = Path(__file__).resolve().parents[2] / "skills" / "builtin"
        catalog = SkillCatalog([root])
        catalog.discover()
        skill = catalog.load("hyperframes-captions")
        self.assertIsNone(skill.contract)
        self.assertIn("caption_html", skill.instructions)
        self.assertIn("media.hyperframes_caption", skill.instructions)
        self.assertIn("video_skill_load", skill.instructions)
        self.assertIn("不要只报一个 registry 组件名", skill.instructions)
