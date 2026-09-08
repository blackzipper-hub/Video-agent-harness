from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from ..models import (
    MediaArtifactVersion,
    MediaCapabilityContract,
    MediaCapabilityInputContract,
)
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
        "media.audio.analyze",
        "media.audio_analyze",
        "media.audio.cut",
        "media.audio_cut",
        "media.audio.trim",
        "media.probe",
        "media.transcribe",
        "media.timeline.compose",
        "media.concat",
        "media.mix_audio",
        "subtitle.compose",
        "media.subtitle.compose",
        "media.subtitle_burn",
        "media.subtitle.burn",
        "media.hyperframes_caption",
        "media.lipsync",
    )

    def capability_handlers(self):
        return {capability: self._execute for capability in self.CAPABILITIES}

    def media_capability_contracts(self) -> list[MediaCapabilityContract]:
        """Describe workflow-free edits that an Agent may append to any project.

        These are deliberately owned by the Media Plugin instead of a generation
        Workflow. Installing another Media Plugin can therefore extend the edit
        surface without changing every Workflow compiler.
        """

        def source(
            role: str,
            artifact_types: list[str],
            parameter: str,
            *,
            multiple: bool = False,
            description: str = "",
        ) -> MediaCapabilityInputContract:
            return MediaCapabilityInputContract(
                role=role,
                artifact_types=artifact_types,
                parameter=parameter,
                multiple=multiple,
                description=description,
            )

        return [
            MediaCapabilityContract(
                capability="runtime.artifact.persist",
                description=(
                    "Persist Agent-authored planning content as a durable project Artifact "
                    "without invoking another language model. The PlanPatch chooses the "
                    "specific document Artifact type."
                ),
                output_artifact_type="document",
                estimated_cost=0.0,
                parameters_schema={
                    "type": "object",
                    "required": ["content"],
                    "properties": {
                        "content": {},
                        "title": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            ),
            MediaCapabilityContract(
                capability="media.transcribe",
                description="Transcribe the selected video into timestamped speech. For songs, pass known lyrics in parameters.prompt to guide recognition; unreliable word timing is rejected before subtitle rendering.",
                inputs=[source("video", ["video"], "video_step")],
                output_artifact_type="transcript",
                estimated_cost=0.01,
                skill_id="subtitle-authoring",
            ),
            MediaCapabilityContract(
                capability="subtitle.compose",
                description="Create a validated subtitle file from a timestamped transcript.",
                inputs=[source("transcript", ["transcript"], "transcription_step")],
                output_artifact_type="subtitle",
                skill_id="subtitle-authoring",
            ),
            MediaCapabilityContract(
                capability="media.subtitle_burn",
                description="Burn a subtitle Artifact into a selected video as a new video version.",
                inputs=[
                    source("video", ["video"], "video_step"),
                    source("subtitle", ["subtitle"], "subtitle_step"),
                ],
                output_artifact_type="video",
                replaces_input_role="video",
                estimated_cost=0.02,
                skill_id="subtitle-authoring",
            ),
            MediaCapabilityContract(
                capability="media.hyperframes_caption",
                description="Render dynamic HyperFrames captions over a selected video.",
                inputs=[
                    source("video", ["video"], "video_step"),
                    source("transcript", ["transcript"], "transcription_step"),
                ],
                output_artifact_type="video",
                replaces_input_role="video",
                estimated_cost=0.02,
                skill_id="hyperframes-captions",
            ),
            MediaCapabilityContract(
                capability="media.extract_frame",
                description="Extract a still frame from an existing video.",
                inputs=[source("video", ["video"], "source_video_step")],
                output_artifact_type="image",
            ),
            MediaCapabilityContract(
                capability="media.concat",
                description="Concatenate selected videos in the supplied order.",
                inputs=[source("videos", ["video"], "video_steps", multiple=True)],
                output_artifact_type="video_assembled",
                estimated_cost=0.02,
            ),
            MediaCapabilityContract(
                capability="media.mix_audio",
                description="Replace or overlay the audio of a selected video.",
                inputs=[
                    source("video", ["video"], "video_step"),
                    source("audio", ["audio"], "audio_step"),
                ],
                output_artifact_type="video",
                replaces_input_role="video",
                estimated_cost=0.01,
            ),
            MediaCapabilityContract(
                capability="media.lipsync",
                description="Create a lip-synchronized version of a selected video.",
                inputs=[
                    source("video", ["video"], "video_step"),
                    source("audio", ["audio"], "audio_step"),
                ],
                output_artifact_type="video",
                replaces_input_role="video",
                estimated_cost=0.35,
            ),
            MediaCapabilityContract(
                capability="media.audio.analyze",
                description="Analyze timing, beats, lyrics, and structure of selected audio.",
                inputs=[source("audio", ["audio"], "audio_step")],
                output_artifact_type="audio_analysis",
            ),
            MediaCapabilityContract(
                capability="media.audio.trim",
                description="Trim a selected audio Artifact without regenerating it.",
                inputs=[source("audio", ["audio"], "audio_step")],
                output_artifact_type="audio",
            ),
            MediaCapabilityContract(
                capability="media.probe",
                description="Inspect duration and streams of an existing media Artifact.",
                inputs=[source("media", ["*"], "media_step")],
                output_artifact_type="media_probe",
            ),
            MediaCapabilityContract(
                capability="media.tts",
                description="Generate narration audio from supplied text.",
                output_artifact_type="audio_narration",
                estimated_cost=0.05,
            ),
        ]

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
            video_url = self._url_from_step_or_direct(
                completed,
                step_id=parameters.get("source_video_step"),
                url=parameters.get("video_url") or parameters.get("uri"),
                label="source_video_step or video_url",
            )
            result = await HostGateway().media_extract_frame({
                "video_url": video_url,
                "position": parameters.get("position", "last"),
                "run_id": f"video-build-{payload['build']['id']}-{step['step_id']}",
            })
        elif capability in {"media.audio.analyze", "media.audio_analyze"}:
            from app.chat.v2.host_gateway import HostGateway
            audio_url = self._url_from_step_or_direct(
                completed,
                step_id=parameters.get("audio_step"),
                url=parameters.get("audio_url") or parameters.get("uri"),
                label="audio_step or audio_url",
            )
            result = await HostGateway().media_audio_analyze({
                "audio_url": audio_url,
                "target_duration_sec": parameters.get("target_duration_sec"),
                "clip_id": parameters.get("clip_id"),
                "generated_lyrics": parameters.get("generated_lyrics"),
                "filename": parameters.get("filename"),
                "user_input": parameters.get("user_input"),
                "transcribe": parameters.get("transcribe", True),
                "transcription": parameters.get("transcription"),
                "run_id": f"video-build-{payload['build']['id']}-{step['step_id']}",
            })
        elif capability in {"media.audio.cut", "media.audio_cut"}:
            from app.chat.v2.host_gateway import HostGateway
            audio_url = self._url_from_step_or_direct(
                completed,
                step_id=parameters.get("audio_step"),
                url=parameters.get("audio_url") or parameters.get("uri"),
                label="audio_step or audio_url",
            )
            analysis: dict[str, Any] = {}
            analysis_step = parameters.get("analysis_step")
            if analysis_step:
                item = completed.get(str(analysis_step))
                if item is None:
                    raise ValueError(f"required media output is unavailable: {analysis_step}")
                analysis = dict(item.metadata or {})
            result = await HostGateway().media_audio_cut({
                "audio_url": audio_url,
                "analysis": analysis,
                "start_sec": parameters.get("start_sec", parameters.get("start")),
                "duration": parameters.get("duration", parameters.get("duration_sec")),
                "max_segment_sec": parameters.get("max_segment_sec"),
                "segments": parameters.get("segments"),
                "transcription": parameters.get("transcription"),
                "run_id": f"video-build-{payload['build']['id']}-{step['step_id']}",
            })
        elif capability == "media.audio.trim":
            from app.chat.v2.host_gateway import HostGateway
            audio_url = self._url_from_step_or_direct(
                completed,
                step_id=parameters.get("audio_step"),
                url=parameters.get("audio_url") or parameters.get("uri"),
                label="audio_step or audio_url",
            )
            start = parameters.get("start", parameters.get("start_sec"))
            duration = parameters.get("duration", parameters.get("duration_sec"))
            analysis_step = parameters.get("analysis_step")
            if analysis_step:
                analysis = completed.get(str(analysis_step))
                if analysis is None:
                    raise ValueError(f"required media output is unavailable: {analysis_step}")
                if parameters.get("use_master_window"):
                    master = analysis.metadata.get("master") or {}
                    start = master.get("start_sec", 0)
                    duration = master.get("duration_sec")
                    if duration is None and master.get("end_sec") is not None:
                        duration = float(master["end_sec"]) - float(start or 0)
                else:
                    segments = analysis.metadata.get("segments") or []
                    index = int(parameters.get("segment_index") or 0)
                    if index >= len(segments):
                        raise ValueError(f"audio analysis has no segment {index}")
                    segment = segments[index]
                    start = segment.get("start_sec", segment.get("start"))
                    duration = segment.get("duration_sec", segment.get("duration"))
            result = await HostGateway().media_audio_trim({
                "audio_url": audio_url,
                "start": start or 0,
                "duration": duration,
                **{
                    key: parameters[key]
                    for key in ("fade_in_sec", "fade_out_sec")
                    if key in parameters
                },
                "run_id": f"video-build-{payload['build']['id']}-{step['step_id']}",
            })
        elif capability == "media.probe":
            from app.utils import media_service_client as msc
            media_url = self._url_from_step_or_direct(
                completed,
                step_id=parameters.get("media_step"),
                url=parameters.get("media_url") or parameters.get("uri") or parameters.get("video_url") or parameters.get("audio_url"),
                label="media_step or media_url",
            )
            media_type = str(parameters.get("media_type") or "video")
            result = (
                await msc.audio_info(media_url)
                if media_type == "audio"
                else await msc.video_info(media_url)
            )
        elif capability == "media.transcribe":
            from app.services.subtitle_transcription_service import transcribe_video
            video_url = self._url_from_step_or_direct(
                completed,
                step_id=parameters.get("video_step"),
                url=parameters.get("video_url") or parameters.get("uri"),
                label="video_step or video_url",
            )
            result = await transcribe_video(
                video_url,
                run_id=f"video-build-{payload['build']['id']}-{step['step_id']}",
                language=parameters.get("language"),
                model=parameters.get("model"),
                prompt=parameters.get("prompt"),
            )
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
            result = await HostGateway().media_concat({
                "video_urls": self._urls_from_steps_or_direct(
                    completed,
                    step_ids=parameters.get("video_steps"),
                    urls=parameters.get("video_urls"),
                    label="video_steps or video_urls",
                ),
                "run_id": f"video-build-{payload['build']['id']}-{step['step_id']}",
                "normalize": bool(parameters.get("normalize", True)),
                "transition_duration": float(parameters.get("transition_duration") or 0.0),
            })
        elif capability == "media.mix_audio":
            from app.chat.v2.host_gateway import HostGateway
            video_url = self._url_from_step_or_direct(
                completed,
                step_id=parameters.get("video_step"),
                url=parameters.get("video_url") or parameters.get("uri"),
                label="video_step or video_url",
            )
            audio_url = self._url_from_step_or_direct(
                completed,
                step_id=parameters.get("audio_step"),
                url=parameters.get("audio_url") or parameters.get("music_url"),
                label="audio_step or audio_url",
            )
            result = await HostGateway().media_mix_audio({
                "video_url": video_url, "audio_url": audio_url,
                "mode": parameters.get("mode", "replace"),
                "audio_volume": parameters.get("audio_volume", 0.25),
                "run_id": f"video-build-{payload['build']['id']}-{step['step_id']}",
            })
        elif capability in {"media.subtitle.compose", "subtitle.compose"}:
            from app.utils import media_service_client as msc
            cues = parameters.get("cues", [])
            transcription_step = parameters.get("transcription_step")
            if transcription_step:
                transcription = completed.get(str(transcription_step))
                if transcription is None:
                    raise ValueError(f"required media output is unavailable: {transcription_step}")
                cues = transcription.metadata.get("segments") or []
            result = await msc.subtitle_compose(
                cues,
                run_id=f"video-build-{payload['build']['id']}-{step['step_id']}",
                subtitle_format=str(parameters.get("format") or "srt"),
                max_lines=int(parameters.get("max_lines") or 2),
                max_chars_per_line=int(parameters.get("max_chars_per_line") or 42),
                max_cps=float(parameters.get("max_cps") or 20),
            )
            result = {**result, "uri": result.get("result_url")}
        elif capability in {"media.subtitle.burn", "media.subtitle_burn"}:
            from app.utils import media_service_client as msc
            video_url = self._url_from_step_or_direct(
                completed,
                step_id=parameters.get("video_step"),
                url=parameters.get("video_url") or parameters.get("uri"),
                label="video_step or video_url",
            )
            subtitle_url = self._url_from_step_or_direct(
                completed,
                step_id=parameters.get("subtitle_step"),
                url=parameters.get("subtitle_url"),
                label="subtitle_step or subtitle_url",
            )
            result = await msc.subtitle_burn(
                video_url, subtitle_url,
                run_id=f"video-build-{payload['build']['id']}-{step['step_id']}",
                style_preset=str(parameters.get("style_preset") or "clean"),
                position=str(parameters.get("position") or "bottom-safe"),
                font_name=str(parameters.get("font_name") or "") or None,
            )
            result = {**result, "uri": result.get("result_url")}
        elif capability == "media.hyperframes_caption":
            from app.utils import media_service_client as msc
            video_url = self._url_from_step_or_direct(
                completed,
                step_id=parameters.get("video_step"),
                url=parameters.get("video_url") or parameters.get("uri"),
                label="video_step or video_url",
            )
            transcription_step = parameters.get("transcription_step")
            transcription = completed.get(str(transcription_step or ""))
            caption_html = parameters.get("caption_html")
            composition_html = parameters.get("composition_html")
            words = list(parameters.get("words") or [])
            cues = list(parameters.get("cues") or parameters.get("segments") or [])
            if transcription is not None:
                metadata = transcription.metadata or {}
                words = words or metadata.get("words") or []
                cues = cues or metadata.get("segments") or []
            elif transcription_step:
                raise ValueError(
                    f"required media output is unavailable: {transcription_step}"
                )
            if not words and not cues:
                raise ValueError(
                    "media.hyperframes_caption requires a timestamped transcript"
                )
            result = await msc.hyperframes_caption(
                video_url,
                run_id=f"video-build-{payload['build']['id']}-{step['step_id']}",
                words=words,
                cues=cues,
                style=str(parameters.get("style") or "caption-highlight"),
                accent_color=str(parameters.get("accent_color") or "#ff1745"),
                position=str(parameters.get("position") or "bottom-safe"),
                playbook=parameters.get("playbook") if isinstance(parameters.get("playbook"), str) else None,
                layers=parameters.get("layers") if isinstance(parameters.get("layers"), list) else None,
                caption_html=caption_html if isinstance(caption_html, str) else None,
                composition_html=composition_html if isinstance(composition_html, str) else None,
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

    @classmethod
    def _url_from_step_or_direct(
        cls,
        completed: dict[str, MediaArtifactVersion],
        *,
        step_id: Any,
        url: Any,
        label: str,
    ) -> str:
        """Accept dest URLs or execute_build step ids for the same media input."""
        direct = str(url or "").strip()
        if direct:
            return direct
        if str(step_id or "").strip():
            return str(cls._required(completed, step_id).uri)
        raise ValueError(f"required media output is unavailable: {label}")

    @classmethod
    def _urls_from_steps_or_direct(
        cls,
        completed: dict[str, MediaArtifactVersion],
        *,
        step_ids: Any,
        urls: Any,
        label: str,
    ) -> list[str]:
        steps = [item for item in (step_ids or []) if item not in (None, "")]
        if steps:
            return [str(cls._required(completed, item).uri) for item in steps]
        if isinstance(urls, str) and urls.strip():
            return [urls.strip()]
        collected = [
            str(item).strip()
            for item in (urls or [])
            if str(item or "").strip()
        ]
        if collected:
            return collected
        raise ValueError(f"required media output is unavailable: {label}")
