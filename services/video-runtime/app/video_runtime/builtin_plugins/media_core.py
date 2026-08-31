from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from ..models import MediaArtifactVersion
from ..plugins import BaseVideoPlugin
from ..security import CapabilityExecutionEnvelope


def resolve_tts_voice_id(requested: str, text: str) -> str:
    """Resolve descriptive VideoSpec voices to a provider-supported MiniMax id."""
    from app.models.image_result import VoiceID, default_voice_for_language_and_gender

    value = requested.strip()
    supported = {item.value for item in VoiceID}
    if value in supported:
        return value
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    aliases = {
        "young-adult-neutral-english-male": VoiceID.ENGLISH_MAGNETIC_MALE_2.value,
        "neutral-english-male": VoiceID.ENGLISH_MAGNETIC_MALE_2.value,
        "english-male": VoiceID.ENGLISH_MAGNETIC_MALE_2.value,
        "young-adult-neutral-english-female": VoiceID.ENGLISH_STEADY_FEMALE_1.value,
        "neutral-english-female": VoiceID.ENGLISH_STEADY_FEMALE_1.value,
        "english-female": VoiceID.ENGLISH_STEADY_FEMALE_1.value,
        "neutral-chinese-male": VoiceID.CHINESE_MALE_ANNOUNCER.value,
        "chinese-male": VoiceID.CHINESE_MALE_ANNOUNCER.value,
        "neutral-chinese-female": VoiceID.CHINESE_NEWS_ANCHOR.value,
        "chinese-female": VoiceID.CHINESE_NEWS_ANCHOR.value,
    }
    if normalized in aliases:
        return aliases[normalized]
    is_zh = bool(re.search(r"[\u3400-\u9fff]", text)) or any(
        token in normalized for token in ("chinese", "mandarin", "zh")
    )
    male = (
        any(token in normalized.split("-") for token in ("male", "man", "boy", "guy"))
        or "男" in value
    )
    female = (
        any(token in normalized.split("-") for token in ("female", "woman", "girl", "lady"))
        or "女" in value
    )
    gender = "m" if male and not female else "f"
    return default_voice_for_language_and_gender("zh" if is_zh else "en", gender)


async def generate_minimax_speech(
    *, text: str, voice_id: str, emotion: str, timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Submit and poll WaveSpeed TTS without legacy S3/duration side effects."""
    import aiohttp
    from app.llm.wavespeed_service import get_wavespeed_service

    service = get_wavespeed_service()
    request_id = await service.create_speech_task(
        text=text,
        voice_id=voice_id,
        emotion=emotion,
        speed=1.0,
        pitch=0,
        volume=1.0,
    )
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    url = f"{service.base_url}/predictions/{request_id}/result"
    headers = {"Authorization": f"Bearer {service.api_key}"}
    while asyncio.get_running_loop().time() < deadline:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                result = await response.json()
        if result.get("code") != 200:
            raise RuntimeError(str(result.get("message") or "WaveSpeed TTS request failed"))
        data = result.get("data") or {}
        status = str(data.get("status") or "unknown").lower()
        if status == "completed":
            outputs = data.get("outputs") or []
            if not outputs:
                raise RuntimeError("WaveSpeed TTS completed without an audio output")
            return {
                "success": True,
                "audio_url": str(outputs[0]),
                "request_id": request_id,
                "provider": "wavespeed",
                "voice_id": voice_id,
            }
        if status == "failed":
            raise RuntimeError(str(data.get("error") or "WaveSpeed TTS failed"))
        if status not in {"created", "queued", "pending", "processing", "running"}:
            raise RuntimeError(f"Unsupported WaveSpeed TTS status: {status}")
        await asyncio.sleep(2)
    raise TimeoutError(f"WaveSpeed TTS timed out after {timeout_seconds} seconds")


class MediaCorePlugin(BaseVideoPlugin):
    """Thin plugin facade over Cuti's existing media service and FFmpeg operations."""

    CAPABILITIES = (
        "runtime.artifact.persist",
        "media.tts",
        "media.extract_frame",
        "media.timeline.compose",
        "media.concat",
        "media.mix_audio",
        "media.subtitle.compose",
        "media.subtitle.burn",
        "media.lipsync",
    )

    def capability_handlers(self):
        return {capability: self._execute for capability in self.CAPABILITIES}

    async def _execute(
        self, envelope: CapabilityExecutionEnvelope, payload: dict[str, Any],
    ) -> MediaArtifactVersion:
        if payload.get("step") is not None:
            step = dict(payload["step"])
            parameters = dict(step.get("parameters") or {})
            completed_payload = payload.get("completed_artifacts", {})
        else:
            source = MediaArtifactVersion.model_validate(payload["source"])
            parameters = dict(source.metadata.get("generation_parameters") or {})
            parameters.update(source.metadata.get("rebuild_parameters") or {})
            step = {
                "step_id": source.metadata.get("plan_step_id") or source.id,
                "output_artifact_id": source.artifact_id,
                "output_artifact_type": source.type,
            }
            completed_payload = payload.get("completed_replacements", {})
        completed = {
            key: MediaArtifactVersion.model_validate(value)
            for key, value in completed_payload.items()
        }
        capability = envelope.grant.capability
        result: dict[str, Any]
        if capability == "runtime.artifact.persist":
            result = {"content": parameters.get("content")}
        elif capability == "media.tts":
            voice_id = resolve_tts_voice_id(
                str(parameters.get("voice_id") or "Wise_Woman"),
                str(parameters.get("text") or ""),
            )
            raw = await generate_minimax_speech(
                text=str(parameters.get("text") or ""),
                voice_id=voice_id,
                emotion=str(parameters.get("emotion") or "neutral"),
            )
            if not raw.get("success") or not raw.get("audio_url"):
                raise RuntimeError(str(raw.get("error") or "TTS returned no audio URL"))
            result = {**raw, "uri": raw["audio_url"]}
        elif capability == "media.extract_frame":
            from app.chat.v2.host_gateway import HostGateway
            source = self._required(completed, parameters.get("source_video_step"))
            result = await HostGateway().media_extract_frame({
                "video_url": source.uri,
                "position": parameters.get("position", "last"),
                "run_id": f"video-build-{payload['build']['id']}-{step['step_id']}",
            })
        elif capability == "media.timeline.compose":
            items, cursor = [], 0.0
            for shot, source_step in zip(
                parameters.get("shots", []), parameters.get("video_steps", []), strict=True,
            ):
                source = self._required(completed, source_step)
                duration = float(shot["duration_seconds"])
                items.append({
                    "shotId": shot["id"], "artifactVersionId": source.id,
                    "track": "video", "startSeconds": cursor, "durationSeconds": duration,
                })
                cursor += duration
            result = {"timeline": {"durationSeconds": cursor, "items": items}}
        elif capability == "media.concat":
            from app.chat.v2.host_gateway import HostGateway
            sources = [self._required(completed, item) for item in parameters.get("video_steps", [])]
            result = await HostGateway().media_concat({
                "video_urls": [item.uri for item in sources],
                "run_id": f"video-build-{payload['build']['id']}-{step['step_id']}",
                "normalize": bool(parameters.get("normalize", True)),
                "transition_duration": float(parameters.get("transition_duration") or 0.0),
            })
        elif capability == "media.mix_audio":
            from app.chat.v2.host_gateway import HostGateway
            video = self._required(completed, parameters.get("video_step"))
            audio = self._required(completed, parameters.get("audio_step"))
            result = await HostGateway().media_mix_audio({
                "video_url": video.uri, "audio_url": audio.uri,
                "mode": parameters.get("mode", "overlay"),
                "audio_volume": parameters.get("audio_volume", 0.25),
                "run_id": f"video-build-{payload['build']['id']}-{step['step_id']}",
            })
        elif capability == "media.subtitle.compose":
            from app.utils import media_service_client as msc
            result = await msc.subtitle_compose(
                parameters.get("cues", []),
                run_id=f"video-build-{payload['build']['id']}-{step['step_id']}",
                subtitle_format=str(parameters.get("format") or "srt"),
            )
            result = {**result, "uri": result.get("result_url")}
        elif capability == "media.subtitle.burn":
            from app.utils import media_service_client as msc
            video = self._required(completed, parameters.get("video_step"))
            subtitle = self._required(completed, parameters.get("subtitle_step"))
            result = await msc.subtitle_burn(
                str(video.uri), str(subtitle.uri),
                run_id=f"video-build-{payload['build']['id']}-{step['step_id']}",
            )
            result = {**result, "uri": result.get("result_url")}
        elif capability == "media.lipsync":
            from app.llm.wavespeed_service import get_wavespeed_service
            video_url = str(parameters.get("video_url") or "")
            audio_url = str(parameters.get("audio_url") or "")
            if not video_url and parameters.get("video_step"):
                video_url = str(self._required(completed, parameters["video_step"]).uri)
            if not audio_url and parameters.get("audio_step"):
                audio_url = str(self._required(completed, parameters["audio_step"]).uri)
            if not video_url or not audio_url:
                raise ValueError("lipsync requires video_url and audio_url")
            generated = await get_wavespeed_service().generate_lipsync(
                audio_url=audio_url,
                video_url=video_url,
                model=str(parameters.get("model") or "sync/lipsync-2-pro"),
            )
            result = generated.model_dump(mode="json") if hasattr(generated, "model_dump") else dict(generated)
            uri = result.get("video_url") or result.get("url")
            if not uri or result.get("success") is False:
                raise RuntimeError(str(result.get("error") or "lipsync returned no video"))
            result = {**result, "uri": uri}
        else:  # pragma: no cover - registry prevents this
            raise ValueError(f"unsupported media capability: {capability}")

        digest = hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()
        return MediaArtifactVersion(
            artifact_id=str(step.get("output_artifact_id") or ""),
            project_id=envelope.grant.project_id,
            type=str(step.get("output_artifact_type") or "media"),
            uri=result.get("uri") or result.get("result_url"),
            title=str(parameters.get("title") or step["step_id"]),
            summary=str(result.get("summary") or ""),
            content_digest=digest,
            generation_spec_digest=hashlib.sha256(
                json.dumps(parameters, sort_keys=True, default=str).encode(),
            ).hexdigest(),
            plugin_id="cuti.media-core",
            plugin_version="1.0.0",
            provenance={"idempotency_key": envelope.grant.idempotency_key},
            metadata={
                **result,
                "generation_parameters": parameters,
                "rebuild_capability": capability,
                "plan_step_id": step.get("step_id"),
            },
        )

    @staticmethod
    def _required(
        completed: dict[str, MediaArtifactVersion], step_id: Any,
    ) -> MediaArtifactVersion:
        artifact = completed.get(str(step_id or ""))
        if artifact is None or not artifact.uri:
            raise ValueError(f"required media output is unavailable: {step_id}")
        return artifact
