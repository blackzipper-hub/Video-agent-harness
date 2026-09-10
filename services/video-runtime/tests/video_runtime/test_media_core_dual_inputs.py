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
    async def test_transcription_does_not_prime_audio_with_script(self):
        transcribe = AsyncMock(return_value={"text": "Actual speech", "segments": [{"start": 1, "end": 2, "text": "Actual speech"}]})
        with patch("app.services.subtitle_transcription_service.transcribe_video", transcribe):
            await self._run("media.transcribe", {"video_url": "https://cdn.example/v.mp4", "prompt": "Invented screenplay"})
        self.assertIsNone(transcribe.await_args.kwargs["prompt"])

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

    async def test_concat_uses_public_uri_when_canonical_exists(self):
        gateway = AsyncMock(return_value={"uri": "https://cdn.example/out.mp4", "video_count": 2})
        with patch("app.chat.v2.host_gateway.HostGateway.media_concat", gateway):
            await self._run(
                "media.concat",
                {"video_steps": ["shot-1-video", "shot-2-video"], "transition_duration": 0},
                {
                    "shot-1-video": MediaArtifactVersion(
                        project_id="project-1", artifact_id="shot-1-video", type="video",
                        uri="https://cdn.example/a-wm.mp4",
                        metadata={
                            "plan_step_id": "shot-1-video",
                            "canonical_uri": "https://cdn.example/a-clean.mp4",
                        },
                    ).model_dump(mode="json"),
                    "shot-2-video": MediaArtifactVersion(
                        project_id="project-1", artifact_id="shot-2-video", type="video",
                        uri="https://cdn.example/b-wm.mp4",
                        metadata={
                            "plan_step_id": "shot-2-video",
                            "canonical_uri": "https://cdn.example/b-clean.mp4",
                        },
                    ).model_dump(mode="json"),
                },
            )
        self.assertEqual(
            gateway.await_args.args[0]["video_urls"],
            ["https://cdn.example/a-wm.mp4", "https://cdn.example/b-wm.mp4"],
        )

    async def test_extract_frame_uses_canonical_uri(self):
        gateway = AsyncMock(return_value={"uri": "https://cdn.example/frame.png"})
        with patch("app.chat.v2.host_gateway.HostGateway.media_extract_frame", gateway):
            await self._run(
                "media.extract_frame",
                {"source_video_step": "shot-1-video", "position": "last"},
                {
                    "shot-1-video": MediaArtifactVersion(
                        project_id="project-1", artifact_id="shot-1-video", type="video",
                        uri="https://cdn.example/a-wm.mp4",
                        metadata={
                            "plan_step_id": "shot-1-video",
                            "canonical_uri": "https://cdn.example/a-clean.mp4",
                        },
                    ).model_dump(mode="json"),
                },
            )
        self.assertEqual(
            gateway.await_args.args[0]["video_url"],
            "https://cdn.example/a-clean.mp4",
        )

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

    async def test_audio_cut_uses_audiomap_recommendation_when_window_omitted(self):
        gateway = AsyncMock(return_value={
            "uri": "https://cdn.example/cut.mp3",
            "result_url": "https://cdn.example/cut.mp3",
        })
        audiomap = MediaArtifactVersion(
            project_id="project-1",
            artifact_id="music-analysis",
            type="audiomap",
            uri="",
            metadata={
                "plan_step_id": "music-analysis",
                "audio_url": "https://cdn.example/song.mp3",
                "smart_clip": {"recommended": {"start_sec": 12.5, "end_sec": 42.5}},
            },
        ).model_dump(mode="json")
        with patch("app.chat.v2.host_gateway.HostGateway.media_audio_cut", gateway):
            await self._run(
                "media.audio_cut",
                {},
                {"music-analysis": audiomap},
            )
        payload = gateway.await_args.args[0]
        self.assertEqual(payload["audio_url"], "https://cdn.example/song.mp3")
        self.assertEqual(
            payload["analysis"]["smart_clip"]["recommended"]["start_sec"],
            12.5,
        )
        self.assertIsNone(payload["start_sec"])
        self.assertIsNone(payload["duration"])

    async def test_mix_prefers_audio_cut_master(self):
        gateway = AsyncMock(return_value={"uri": "https://cdn.example/mixed.mp4"})
        cut = MediaArtifactVersion(
            project_id="project-1",
            artifact_id="music-cut",
            type="audio_cut",
            uri="https://cdn.example/cut-uri.mp3",
            metadata={
                "plan_step_id": "music-cut",
                "master": {"audio_url": "https://cdn.example/cut-master.mp3"},
            },
        ).model_dump(mode="json")
        with patch("app.chat.v2.host_gateway.HostGateway.media_mix_audio", gateway):
            await self._run(
                "media.mix_audio",
                {"video_url": "https://cdn.example/v.mp4"},
                {"music-cut": cut},
            )
        payload = gateway.await_args.args[0]
        self.assertEqual(payload["audio_url"], "https://cdn.example/cut-master.mp3")


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

    def test_hyperframes_captions_skill_teaches_cuti_template_flow(self):
        root = Path(__file__).resolve().parents[2] / "skills" / "builtin"
        catalog = SkillCatalog([root])
        catalog.discover()
        skill = catalog.load("hyperframes-captions")
        self.assertIsNone(skill.contract)
        self.assertIn("timestamps", skill.instructions)
        self.assertIn("media.hyperframes_caption", skill.instructions)
        self.assertNotIn("不要只报一个 registry 组件名", skill.instructions)


class HyperframesCaptionExecuteTest(unittest.IsolatedAsyncioTestCase):
    async def test_translations_keep_transcript_timing_and_template_placement(self):
        transcript = MediaArtifactVersion(project_id="project-1", artifact_id="transcription", type="transcript", metadata={
            "segments": [{"start": 3.2, "end": 4.8, "text": "Hello"}],
            "words": [{"start": 3.2, "end": 4.8, "word": "Hello"}],
        }).model_dump(mode="json")
        caption = AsyncMock(return_value={"result_url": "https://cdn.example/out.mp4"})
        with patch("app.utils.media_service_client.hyperframes_caption", caption):
            await self._run({"video_url": "https://cdn.example/v.mp4", "translated_texts": ["你好"],
                             "caption_html": "<div>invented</div>", "position": "bottom-safe"},
                            {"transcription": transcript})
        self.assertEqual(caption.await_args.kwargs["cues"], [{"start": 3.2, "end": 4.8, "text": "你好"}])
        self.assertEqual(caption.await_args.kwargs["words"], [])
        self.assertIsNone(caption.await_args.kwargs["caption_html"])
        self.assertEqual(caption.await_args.kwargs["position"], "bottom-safe")

    async def _run(self, parameters: dict, completed: dict | None = None):
        plugin = MediaCorePlugin()
        return await plugin.capability_handlers()["media.hyperframes_caption"](
            _envelope("media.hyperframes_caption"),
            {
                "build": {"id": "build-1"},
                "step": {
                    "step_id": "final-video",
                    "capability": "media.hyperframes_caption",
                    "output_artifact_id": "final",
                    "output_artifact_type": "final_video",
                    "depends_on": ["transcription"],
                    "parameters": parameters,
                },
                "completed_artifacts": completed or {},
            },
        )

    async def test_caption_html_does_not_require_transcript_words(self):
        caption = AsyncMock(return_value={"result_url": "https://cdn.example/captioned.mp4"})
        with patch("app.utils.media_service_client.hyperframes_caption", caption):
            result = await self._run({
                "video_url": "https://cdn.example/v.mp4",
                "caption_html": "<!doctype html><html></html>",
            })
        caption.assert_awaited_once()
        payload = caption.await_args.kwargs
        self.assertEqual(payload["caption_html"], "<!doctype html><html></html>")
        self.assertEqual(payload["words"], [])
        self.assertEqual(payload["cues"], [])
        self.assertEqual(caption.await_args.kwargs["style"], "caption-highlight")
        self.assertEqual(result.uri, "https://cdn.example/captioned.mp4")

    async def test_transcript_cues_render_without_authored_html(self):
        caption = AsyncMock(return_value={"result_url": "https://cdn.example/captioned.mp4"})
        transcript = MediaArtifactVersion(
            project_id="project-1",
            artifact_id="transcription",
            type="transcript",
            uri=None,
            metadata={"plan_step_id": "transcription"},
        ).model_dump(mode="json")
        transcript["metadata"].update({
            "segments": [{"start": 1, "end": 2, "text": "字幕"}],
        })
        with patch("app.utils.media_service_client.hyperframes_caption", caption):
            result = await self._run(
                {
                    "video_url": "https://cdn.example/v.mp4",
                    "transcription_step": "transcription",
                    "style": "caption-editorial-emphasis",
                },
                {"transcription": transcript},
            )
        self.assertEqual(result.uri, "https://cdn.example/captioned.mp4")
        self.assertEqual(caption.await_args.kwargs["style"], "caption-editorial-emphasis")
        self.assertEqual(caption.await_args.kwargs["cues"][0]["text"], "字幕")
        self.assertIsNone(caption.await_args.kwargs["caption_html"])

    async def test_missing_transcript_and_html_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timestamped transcript"):
            await self._run({"video_url": "https://cdn.example/v.mp4"})

    async def test_replacing_static_captions_uses_original_video(self):
        caption = AsyncMock(return_value={"result_url": "https://cdn.example/dynamic.mp4"})
        source = _clip("captioned-video", "https://cdn.example/static-captioned.mp4")
        source["metadata"].update({
            "capability": "media.subtitle_burn",
            "source_video_url": "https://cdn.example/original.mp4",
        })
        with patch("app.utils.media_service_client.hyperframes_caption", caption):
            await self._run(
                {
                    "video_step": "captioned-video",
                    "caption_html": "<body><div data-composition-id=\"captions\"></div></body>",
                },
                {"captioned-video": source},
            )

        self.assertEqual(caption.await_args.args[0], "https://cdn.example/original.mp4")
