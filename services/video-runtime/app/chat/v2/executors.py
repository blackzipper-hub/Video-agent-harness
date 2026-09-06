from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from app.chat.models.video_state import (
    AudioFileUserInput, ImageUserInput, UserInput, VideoFileUserInput,
)
from app.chat.models.user_options import UserOption
from app.chat.services.agent.action_suggestions_merge import (
    parse_formatted_action_suggestions_body,
)

from .capabilities import CapabilityRegistry
from .models import AgentRun, ArtifactVersion, Task

logger = logging.getLogger(__name__)


def _detected_language_for_task(
    run: AgentRun, task: Task, parameters: dict[str, Any],
) -> str | None:
    explicit = (
        str(parameters.get("detected_language") or "").strip()
        or str(parameters.get("language") or "").strip()
        or str(task.parameters.get("language") or "").strip()
    )
    if explicit:
        return explicit
    return run.output_language


def _merge_selected_image_uris(
    explicit_images: Any,
    selected: list[ArtifactVersion],
) -> list[str]:
    """Replace internal artifact identifiers with provider-readable URIs."""
    image_versions = [
        item for item in selected
        if item.uri and item.type in {"image", "keyframe"}
    ]
    refs_to_uri: dict[str, str] = {}
    for item in image_versions:
        refs_to_uri[str(item.id)] = str(item.uri)
        refs_to_uri[str(item.artifact_id)] = str(item.uri)

    merged: list[str] = []
    raw_images = explicit_images if isinstance(explicit_images, list) else []
    for raw in raw_images:
        value = str(raw or "").strip()
        if not value:
            continue
        value = refs_to_uri.get(value, value)
        if value not in merged:
            merged.append(value)
    for item in image_versions:
        uri = str(item.uri)
        if uri not in merged:
            merged.append(uri)
    return merged


class WorkflowDelegateClient(Protocol):
    async def delegate_submit(
        self,
        *,
        target_agent: str,
        state: Any,
        idempotency_key: str | None = None,
        mode: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> Any:
        ...


@dataclass
class DelegateResult:
    remote_run_id: str
    status: str
    artifact: dict[str, Any] | None = None
    remote_thread_id: str | None = None


class SuggestedAction(BaseModel):
    label: str
    message: str
    is_direct_generate: bool = False
    generation_input: dict[str, Any] | None = None


class SuggestedActions(BaseModel):
    reply_type: str = "choice"
    suggestions: list[SuggestedAction] = Field(min_length=2)


class CapabilityExecutor:
    def __init__(
        self,
        workflow_client: WorkflowDelegateClient,
        capabilities: CapabilityRegistry,
        *,
        mcp_bridge: Any | None = None,
        sandbox_client: Any | None = None,
    ):
        self.workflow_client = workflow_client
        self.capabilities = capabilities
        self.mcp_bridge = mcp_bridge
        self.sandbox_client = sandbox_client

    async def submit(
        self,
        run: AgentRun,
        task: Task,
        artifacts: list[ArtifactVersion],
        idempotency_key: str,
        on_remote_submitted: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> DelegateResult:
        capability = self.capabilities.get(task.capability_id)
        selected = [
            item for item in artifacts if item.id in task.input_artifact_version_ids
        ]
        if capability.executor == "sandbox.run":
            if self.sandbox_client is None:
                raise RuntimeError("sandbox client is not configured")
            remote_run_id, artifact = await self.sandbox_client.run(
                run=run,
                task=task,
                capability=capability,
                artifacts=selected,
                idempotency_key=idempotency_key,
            )
            return DelegateResult(
                remote_run_id=remote_run_id,
                status="completed",
                artifact=artifact,
            )
        if capability.executor == "local.structured":
            return self._suggest_actions(task, run)
        if capability.executor == "atomic.direct":
            from .atomic_executor import execute_atomic

            remote_run_id, artifact = await execute_atomic(
                run=run,
                task=task,
                selected=selected,
                idempotency_key=idempotency_key,
                on_remote_submitted=on_remote_submitted,
            )
            return DelegateResult(
                remote_run_id=remote_run_id,
                status="completed",
                artifact=artifact,
                remote_thread_id=run.thread_id,
            )
        if capability.executor == "local.service":
            from app.orchestration.skills.constraint_contract import (
                activate_constraint_contract,
                reset_constraint_contract,
            )

            token = activate_constraint_contract(
                task.skill_context.constraint_contract if task.skill_context else None
            )
            try:
                return await self._run_local_service(
                    run, task, selected, idempotency_key, capability,
                    on_remote_submitted=on_remote_submitted,
                )
            finally:
                reset_constraint_contract(token)
        if capability.executor == "mcp.call":
            return await self._run_mcp_call(run, task, selected, idempotency_key, capability)
        if capability.executor != "video-agent.delegate" or not capability.target_agent:
            raise ValueError(f"unsupported capability executor: {capability.executor}")
        if capability.target_agent == "video":
            raise ValueError(
                "Deep Agent V2 blocks the legacy target_agent=video fixed pipeline; "
                "use atomic.video.generate (target_agent=video_gen)"
            )

        references = "\n".join(
            f"- {item.type} artifact {item.id} v{item.version}; "
            f"title={item.title}; uri={item.uri or 'none'}; details={item.summary}"
            for item in selected
        )
        objective = task.objective_with_skill_context()
        if references:
            objective += "\n\nUse these exact selected artifact versions:\n" + references
        if task.capability_id == "video.generate" and not task.parameters.get("single_video"):
            raise ValueError(
                "video.generate requires parameters.single_video=true; "
                "use video.pipeline.generate for the full master pipeline"
            )
        parameters = dict(task.parameters)
        # Some establishing/B-roll shots deliberately exclude the product. In
        # that case the selected first-frame artifact is authoritative and the
        # run-level product upload must not be forwarded: multimodal models can
        # otherwise copy the identity reference into an explicitly empty shot.
        exclude_run_input_images = bool(
            parameters.get("exclude_run_input_images")
            or parameters.get("exclude_product_identity")
        )
        delegate_mode: str | None = None
        user_option_value = dict(run.user_option or {})
        if task.capability_id in {
            "video.generate", "video_gen.generate", "atomic.video.generate",
        }:
            for option_name in (
                "duration",
                "resolution",
                "aspect_ratio",
                "video_generation_tool",
            ):
                if parameters.get(option_name) is not None:
                    user_option_value[option_name] = parameters[option_name]
        # Deep Agent generation already implies user intent: auto-continue VideoAgent
        # stage gates unless the plan explicitly asks for confirmation.
        require_confirm = bool(
            parameters.get("require_user_confirmation")
            or parameters.get("require_confirmation")
            or user_option_value.get("require_user_confirmation")
        )
        if "full_auto" in parameters:
            user_option_value["full_auto"] = bool(parameters.get("full_auto"))
        elif "full_auto" not in user_option_value:
            user_option_value["full_auto"] = not require_confirm
        if require_confirm:
            user_option_value["full_auto"] = False
        if task.capability_id == "video.pipeline.generate":
            parameters.update({"pipeline": "full", "mode": "master"})
            user_option_value["mode"] = "master"
            delegate_mode = "master"
        elif task.capability_id == "video.generate":
            parameters["mode"] = "instant"
            user_option_value["mode"] = "instant"
            delegate_mode = None
        elif task.capability_id == "video_gen.generate":
            delegate_mode = capability.mode
        elif task.capability_id == "atomic.video.generate":
            delegate_mode = capability.mode
        elif task.capability_id == "video.edit":
            parameters["mode"] = capability.mode or "edit"
            delegate_mode = capability.mode
        from app.utils.media_egress import resolve_outbound_media_url

        outbound_urls: dict[str, str] = {}

        async def outbound_url(url: str) -> str:
            if url not in outbound_urls:
                outbound_urls[url] = await resolve_outbound_media_url(url)
            return outbound_urls[url]

        async def outbound_value(value: Any) -> Any:
            if isinstance(value, str):
                if value.startswith(("http://", "https://")):
                    return await outbound_url(value)
                rewritten = value
                for local_url, public_url in outbound_urls.items():
                    rewritten = rewritten.replace(local_url, public_url)
                return rewritten
            if isinstance(value, list):
                return [await outbound_value(item) for item in value]
            if isinstance(value, dict):
                return {
                    key: await outbound_value(item) for key, item in value.items()
                }
            return value

        selected_images = [
            ImageUserInput(url=await outbound_url(item.uri), filename=item.title or None)
            for item in selected
            if item.type == "image" and item.uri
        ]
        selected_audio = [
            AudioFileUserInput(url=await outbound_url(item.uri), filename=item.title or None)
            for item in selected
            if item.type in {"audio", "music"} and item.uri
        ]
        selected_videos = [
            VideoFileUserInput(url=await outbound_url(item.uri), filename=item.title or None)
            for item in selected
            if item.type == "video" and item.uri
        ]
        for item in run.input_files:
            if item.url:
                await outbound_url(item.url)
        user_input = UserInput(
            user_input=await outbound_value(objective),
            # Selected artifact images are task-local inputs. For I2V the
            # provider consumes images[0] as the actual start frame, so these
            # must precede run-level uploads (which are reference-only).
            images=selected_images + [
                ImageUserInput(
                    url=await outbound_url(item.url), filename=item.filename,
                )
                for item in run.input_files
                if item.type == "image" and not exclude_run_input_images
            ],
            audio_files=[
                AudioFileUserInput(
                    url=await outbound_url(item.url), filename=item.filename,
                )
                for item in run.input_files if item.type in {"audio", "music"}
            ] + selected_audio,
            video_files=[
                VideoFileUserInput(
                    url=await outbound_url(item.url), filename=item.filename,
                )
                for item in run.input_files if item.type == "video"
            ] + selected_videos,
            user_option=UserOption.model_validate(user_option_value) if user_option_value else None,
        )
        parameters = await outbound_value(parameters)
        state = {
            "thread_id": f"{run.thread_id}:v2-task:{task.id}",
            "user_id": run.user_id,
            "detected_language": run.output_language,
            "user_input_data": user_input,
        }
        response = await self.workflow_client.delegate_submit(
            target_agent=capability.target_agent,
            state=state,
            idempotency_key=idempotency_key,
            mode=delegate_mode,
            parameters=parameters,
        )
        if not response.run_id:
            raise RuntimeError("VideoAgent delegate-submit returned no run_id")
        return DelegateResult(
            response.run_id,
            response.status or "queued",
            remote_thread_id=getattr(response, "thread_id", None) or state["thread_id"],
        )

    async def _run_local_service(
        self,
        run: AgentRun,
        task: Task,
        selected: list[ArtifactVersion],
        idempotency_key: str,
        capability,
        on_remote_submitted: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> DelegateResult:
        service_target = capability.service_target
        if not service_target:
            raise ValueError(f"local.service capability {capability.id} missing service_target")
        parameters = dict(task.parameters)
        project_thread_id = str(
            parameters.get("thread_id")
            or parameters.get("project_thread_id")
            or run.thread_id
        )
        remote_run_id = str(parameters.get("run_id") or f"v2-svc-{uuid4()}")
        # Local stage services import VideoAgent UserOption (app.models.*).
        # Chat-layer UserOption is a different class and must be revalidated.
        from app.models.user_options import UserOption as VideoAgentUserOption
        from app.services.agent.video.project_stage_context import (
            coerce_video_agent_user_option,
        )
        user_option_values = dict(run.user_option or {})
        for option_name in ("duration", "resolution", "aspect_ratio"):
            if parameters.get(option_name) is not None:
                user_option_values[option_name] = parameters[option_name]
        if parameters.get("content_category") is not None:
            from app.models.tool_enums import ContentCategory

            category = str(parameters["content_category"]).strip()
            category_aliases = {
                "short_drama": ContentCategory.SHORT_DRAMA.value,
                "music_video": ContentCategory.LIP_SYNC_MV.value,
                "product_launch": ContentCategory.PRODUCT_LAUNCH.value,
                # Workflow Skills use the semantic category `product_ad`, while
                # the legacy stage services expose the older Product Launch enum.
                "product_ad": ContentCategory.PRODUCT_LAUNCH.value,
                "scenario_product_ad": ContentCategory.PRODUCT_LAUNCH.value,
            }
            user_option_values["content_category"] = category_aliases.get(
                category.lower(), category
            )
        user_option = coerce_video_agent_user_option(
            user_option_values or VideoAgentUserOption()
        )
        result: dict[str, Any]
        if service_target == "execute_regenerate_keyframes":
            from app.services.task_enqueue_service import execute_regenerate_keyframes
            keyframes = parameters.get("keyframes")
            if not keyframes:
                raise ValueError("keyframe.regenerate requires parameters.keyframes")
            result = await execute_regenerate_keyframes(
                thread_id=project_thread_id,
                run_id=remote_run_id,
                user_id=run.user_id,
                keyframes=keyframes,
                user_option=user_option,
                regenerate_source="deep-agent-v2",
            )
        elif service_target == "execute_regenerate_characters":
            from app.services.task_enqueue_service import execute_regenerate_characters
            characters = parameters.get("characters")
            if not characters:
                raise ValueError("character.regenerate requires parameters.characters")
            result = await execute_regenerate_characters(
                thread_id=project_thread_id,
                run_id=remote_run_id,
                user_id=run.user_id,
                characters=characters,
                user_option=user_option,
                regenerate_source="deep-agent-v2",
            )
        elif service_target == "execute_regenerate_videos":
            from app.services.task_enqueue_service import execute_regenerate_videos
            videos = parameters.get("videos")
            if not videos:
                raise ValueError("shot.video.regenerate requires parameters.videos")
            result = await execute_regenerate_videos(
                thread_id=project_thread_id,
                run_id=remote_run_id,
                user_id=run.user_id,
                videos=videos,
                user_option=user_option,
                regenerate_source="deep-agent-v2",
            )
        elif service_target == "video_assembly_by_request":
            result = await self._assemble_videos(
                project_thread_id=project_thread_id,
                run=run,
                task=task,
                selected=selected,
                remote_run_id=remote_run_id,
                user_option=user_option,
                parameters=parameters,
            )
        elif service_target == "api_provider_generate":
            from app.chat.config import get_settings
            from app.integrations.providers.provider_bridge import (
                ProviderGenerateError,
                generate_video,
            )

            profile = dict(parameters)
            # Persist orchestration contracts on the task, but never leak them
            # into third-party provider payloads.
            profile.pop("_workflow_contract", None)
            profile.pop("_language_contract", None)
            profile["idempotency_key"] = idempotency_key
            if task.remote_operation_id:
                profile["remote_operation_id"] = task.remote_operation_id
            # Selected artifacts are explicit task dependencies. Always merge
            # them with parameter URLs so a workflow reference cannot be
            # silently dropped just because the planner supplied other images.
            merged_images = _merge_selected_image_uris(
                profile.get("images"), selected,
            )
            if merged_images:
                profile["images"] = merged_images
            if selected and not profile.get("videos"):
                profile["videos"] = [
                    item.uri for item in selected
                    if item.uri and "video" in (item.type or "")
                ]
            try:
                result = await generate_video(
                    profile,
                    fallbacks_json=get_settings().DEEP_AGENT_V2_PROVIDER_FALLBACKS_JSON,
                    on_remote_submitted=on_remote_submitted,
                )
            except ProviderGenerateError as exc:
                # Preserve structured category so the coordinator can avoid
                # false fallbacks on still-processing remote tasks.
                detail = exc.to_dict()
                raise RuntimeError(json.dumps(detail, ensure_ascii=False)) from exc
            result["summary"] = (
                f"provider bridge generated video via {result.get('provider_used')}"
            )
        elif service_target == "media_transcribe":
            from app.services.subtitle_transcription_service import transcribe_video

            video_url = parameters.get("video_url") or parameters.get("uri")
            if not video_url:
                urls = self._collect_artifact_video_urls(selected)
                video_url = urls[0] if urls else None
            if not video_url:
                raise ValueError(
                    "media.transcribe requires parameters.video_url "
                    "or a selected video artifact"
                )
            result = await transcribe_video(
                str(video_url),
                run_id=remote_run_id,
                language=str(parameters.get("language") or "") or None,
                model=str(parameters.get("model") or "") or None,
            )
            result["summary"] = (
                f"Transcribed {len(result.get('segments') or [])} timestamped segments "
                f"in {result.get('language') or 'unknown language'}"
            )
        elif service_target == "subtitle_compose":
            from app.utils import media_service_client as msc

            cues = parameters.get("cues")
            timing_mode = str(parameters.get("timing_mode") or "audio").lower()
            transcript = next(
                (item for item in selected if item.type == "transcript"),
                None,
            )
            if transcript is not None and timing_mode != "manual":
                metadata = transcript.metadata if isinstance(transcript.metadata, dict) else {}
                cues = metadata.get("segments")
            if not isinstance(cues, list) or not cues:
                raise ValueError(
                    "subtitle.compose requires parameters.cues or a selected transcript artifact"
                )
            cleaned_cues = []
            for cue in cues:
                if not isinstance(cue, dict):
                    continue
                try:
                    start = float(cue.get("start"))
                    end = float(cue.get("end"))
                except (TypeError, ValueError):
                    continue
                text = str(cue.get("text") or "").strip()
                if text and start >= 0 and end > start:
                    cleaned_cues.append({"start": start, "end": end, "text": text})
            if not cleaned_cues:
                raise ValueError("subtitle.compose received no valid timestamped cues")
            combined_text = "".join(item["text"] for item in cleaned_cues)
            contains_cjk = any("\u3400" <= char <= "\u9fff" for char in combined_text)
            composed = await msc.subtitle_compose(
                cleaned_cues,
                run_id=remote_run_id,
                subtitle_format=str(parameters.get("format") or "srt"),
                max_lines=int(parameters.get("max_lines") or 2),
                max_chars_per_line=int(
                    parameters.get("max_chars_per_line") or (18 if contains_cjk else 42)
                ),
                max_cps=float(parameters.get("max_cps") or (15 if contains_cjk else 20)),
            )
            result = {
                **composed,
                "uri": composed.get("result_url"),
                "title": f"Subtitles ({composed.get('format', 'srt').upper()})",
                "summary": (
                    f"Created {composed.get('cue_count', len(cleaned_cues))} subtitle cues; "
                    f"validation={composed.get('validation') or {}}"
                ),
                "source_transcript_artifact_id": transcript.id if transcript else None,
                "timing_mode": timing_mode,
                "timing_source": (
                    transcript.metadata.get("timing_source")
                    if transcript and isinstance(transcript.metadata, dict)
                    else "manual"
                ),
            }
        elif service_target == "media_subtitle_burn":
            from app.utils import media_service_client as msc

            video_url = parameters.get("video_url") or parameters.get("uri")
            if not video_url:
                urls = self._collect_artifact_video_urls(selected)
                video_url = urls[0] if urls else None
            subtitle = next((item for item in selected if item.type == "subtitle"), None)
            subtitle_url = parameters.get("subtitle_url") or (
                subtitle.uri if subtitle is not None else None
            )
            if not video_url or not subtitle_url:
                raise ValueError(
                    "media.subtitle_burn requires selected video and subtitle artifacts"
                )
            burned = await msc.subtitle_burn(
                str(video_url),
                str(subtitle_url),
                run_id=remote_run_id,
                style_preset=str(parameters.get("style_preset") or "clean"),
                position=str(parameters.get("position") or "bottom-safe"),
                font_name=str(parameters.get("font_name") or "") or None,
            )
            result = {
                **burned,
                "uri": burned.get("result_url"),
                "video_url": burned.get("result_url"),
                "title": "Captioned video",
                "summary": (
                    f"Burned subtitles into video using "
                    f"{parameters.get('style_preset') or 'clean'} style"
                ),
                "source_video_url": video_url,
                "source_subtitle_artifact_id": subtitle.id if subtitle else None,
            }
        elif service_target == "media_concat":
            from app.chat.v2.host_gateway import HostGateway

            gateway = HostGateway()
            urls = parameters.get("video_urls")
            if not urls:
                urls = [
                    u for u in self._collect_artifact_video_urls(selected) if u
                ]
            result = await gateway.media_concat({
                "video_urls": urls,
                "normalize": parameters.get("normalize", True),
                "transition_duration": parameters.get("transition_duration", 0.0),
                "run_id": remote_run_id,
            })
            result["summary"] = f"concatenated {result.get('video_count', 0)} clips"
        elif service_target == "media_audio_trim":
            from app.chat.v2.host_gateway import HostGateway

            gateway = HostGateway()
            audio_url = parameters.get("audio_url") or parameters.get("uri")
            if not audio_url:
                audio_url = self._collect_artifact_audio_url(selected)
            if not audio_url:
                raise ValueError(
                    "media.audio_trim requires parameters.audio_url "
                    "or a selected music artifact"
                )
            duration = parameters.get("duration", parameters.get("duration_sec"))
            if duration is None:
                raise ValueError("media.audio_trim requires parameters.duration")
            result = await gateway.media_audio_trim({
                "audio_url": audio_url,
                "start": parameters.get("start", parameters.get("start_sec", 0.0)),
                "duration": duration,
                "fade_in_sec": parameters.get("fade_in_sec"),
                "fade_out_sec": parameters.get("fade_out_sec"),
                "run_id": remote_run_id,
            })
        elif service_target == "media_audio_analyze":
            from app.chat.v2.host_gateway import HostGateway

            gateway = HostGateway()
            audio_url = parameters.get("audio_url") or parameters.get("uri")
            if not audio_url:
                audio_url = self._collect_artifact_audio_url(selected)
            if not audio_url:
                raise ValueError(
                    "media.audio_analyze requires parameters.audio_url "
                    "or a selected music artifact"
                )
            music_meta = self._collect_artifact_music_metadata(selected)
            result = await gateway.media_audio_analyze({
                "audio_url": audio_url,
                "target_duration_sec": parameters.get(
                    "target_duration_sec", parameters.get("target_duration")
                ),
                "clip_id": parameters.get("clip_id") or music_meta.get("clip_id"),
                "generated_lyrics": (
                    parameters.get("generated_lyrics") or music_meta.get("lyrics")
                ),
                "filename": parameters.get("filename") or music_meta.get("filename"),
                "user_input": parameters.get("user_input"),
                "transcribe": parameters.get("transcribe", True),
                "transcription": parameters.get("transcription"),
                "run_id": remote_run_id,
            })
        elif service_target == "media_audio_cut":
            from app.chat.v2.host_gateway import HostGateway

            gateway = HostGateway()
            analysis = (
                parameters.get("analysis")
                or parameters.get("audiomap")
                or self._collect_artifact_audiomap(selected)
            )
            audio_url = (
                parameters.get("audio_url")
                or (analysis or {}).get("audio_url")
                or self._collect_artifact_audio_url(selected)
            )
            if not audio_url:
                raise ValueError(
                    "media.audio_cut requires parameters.audio_url, an analysis "
                    "artifact, or a selected music artifact"
                )
            result = await gateway.media_audio_cut({
                "audio_url": audio_url,
                "analysis": analysis,
                "start_sec": parameters.get("start_sec", parameters.get("start")),
                "duration": parameters.get("duration", parameters.get("duration_sec")),
                "max_segment_sec": parameters.get("max_segment_sec"),
                "segments": parameters.get("segments"),
                "transcription": parameters.get("transcription"),
                "run_id": remote_run_id,
            })
        elif service_target == "media_mix_audio":
            from app.chat.v2.host_gateway import HostGateway

            gateway = HostGateway()
            video_url = parameters.get("video_url") or parameters.get("uri")
            if not video_url:
                urls = self._collect_artifact_video_urls(selected)
                video_url = urls[0] if urls else None
            audio_url = (
                parameters.get("audio_url")
                or parameters.get("music_url")
            )
            if not audio_url:
                audio_url = self._collect_artifact_audio_url(
                    selected, prefer_cut=True,
                )
            if not video_url or not audio_url:
                raise ValueError(
                    "media.mix_audio requires video_url and audio_url "
                    "(or selected video + music / audio_cut artifacts)"
                )
            result = await gateway.media_mix_audio({
                "video_url": video_url,
                "audio_url": audio_url,
                "mode": parameters.get("mode", "replace"),
                "audio_volume": parameters.get("audio_volume", 0.35),
                "loop_audio": parameters.get("loop_audio", False),
                "run_id": remote_run_id,
            })
        elif service_target == "suno_generate":
            from app.integrations.providers.suno_bridge import generate_suno_native

            result = await generate_suno_native(dict(parameters))
        elif service_target == "media_extract_frame":
            from app.chat.v2.host_gateway import HostGateway

            gateway = HostGateway()
            video_url = parameters.get("video_url") or parameters.get("uri")
            if not video_url:
                urls = self._collect_artifact_video_urls(selected)
                video_url = urls[0] if urls else None
            if not video_url:
                raise ValueError(
                    "media.extract_frame requires parameters.video_url "
                    "or a selected video artifact"
                )
            position = str(parameters.get("position") or "timestamp").strip().lower()
            if position != "last" and "timestamp" not in parameters:
                raise ValueError(
                    "media.extract_frame requires parameters.timestamp (seconds) "
                    "or position='last'"
                )
            result = await gateway.media_extract_frame({
                "video_url": video_url,
                "timestamp": parameters.get("timestamp"),
                "position": position,
                "format": parameters.get("format", "jpeg"),
                "run_id": remote_run_id,
            })
        elif service_target == "open_montage_tool_invoke":
            from app.integrations.providers.open_montage_bridge import invoke_om_tool

            tool = str(parameters.get("tool") or "").strip()
            raw_inputs = parameters.get("inputs")
            inputs = dict(raw_inputs) if isinstance(raw_inputs, dict) else {}
            # Allow flat parameters for convenience (excluding control keys).
            if not inputs:
                inputs = {
                    k: v for k, v in parameters.items()
                    if k not in {"tool", "inputs", "run_id"}
                }
            if not inputs.get("video_url") and not inputs.get("clips") and not inputs.get("video_urls"):
                urls = self._collect_artifact_video_urls(selected)
                if urls and tool in {"video_trimmer", "frame_sampler"}:
                    inputs.setdefault("video_url", urls[0])
                elif urls and tool == "video_stitch":
                    inputs.setdefault("clips", urls)
            result = await invoke_om_tool(
                tool,
                inputs,
                run_id=str(parameters.get("run_id") or remote_run_id),
            )
        elif service_target == "ark_protocol_generate":
            import asyncio

            from app.chat.config import get_settings
            from app.integrations.providers.ark_protocol_bridge import (
                create_ark_task_via_wavespeed,
                get_ark_task,
            )

            body = parameters.get("body")
            if not isinstance(body, dict):
                raise ValueError("api.ark_protocol.generate requires parameters.body object")
            body = dict(body)
            body.pop("_workflow_contract", None)
            body.pop("_language_contract", None)
            merged_images = _merge_selected_image_uris(
                body.get("images"), selected,
            )
            if merged_images:
                body["images"] = merged_images
            created = await create_ark_task_via_wavespeed(
                body,
                fallbacks_json=get_settings().DEEP_AGENT_V2_PROVIDER_FALLBACKS_JSON,
            )
            task_id = str(created.get("id") or "")
            result = created
            if parameters.get("wait", True) and task_id:
                interval = float(parameters.get("poll_interval") or 2.0)
                deadline = __import__("time").monotonic() + 900
                while __import__("time").monotonic() < deadline:
                    result = await get_ark_task(task_id)
                    status = str(result.get("status") or "")
                    if status in {"succeeded", "failed", "expired", "cancelled"}:
                        break
                    await asyncio.sleep(interval)
            video_url = (
                (result.get("content") or {}).get("video_url")
                if isinstance(result.get("content"), dict)
                else None
            )
            result = {
                **result,
                "uri": video_url,
                "video_url": video_url,
                "summary": (
                    f"ark protocol bridge {result.get('status')} "
                    f"via {(result.get('cuti_bridge') or {}).get('provider_used', 'wavespeed')}"
                ),
            }
        elif service_target == "media_hyperframes_caption":
            from app.utils import media_service_client as msc

            video_url = parameters.get("video_url") or parameters.get("uri")
            if not video_url:
                urls = self._collect_artifact_video_urls(selected)
                video_url = urls[0] if urls else None
            transcript = next((item for item in selected if item.type == "transcript"), None)
            metadata = (
                transcript.metadata
                if transcript is not None and isinstance(transcript.metadata, dict)
                else {}
            )
            words = metadata.get("words") or []
            cues = metadata.get("segments") or []
            caption_html = (
                parameters.get("caption_html")
                if isinstance(parameters.get("caption_html"), str)
                else None
            )
            composition_html = (
                parameters.get("composition_html")
                if isinstance(parameters.get("composition_html"), str)
                else None
            )
            if not video_url or not transcript or (not words and not cues):
                raise ValueError(
                    "media.hyperframes_caption requires selected video and timestamped transcript artifacts"
                )
            rendered = await msc.hyperframes_caption(
                str(video_url),
                run_id=remote_run_id,
                words=words,
                cues=cues,
                style=str(parameters.get("style") or "caption-highlight"),
                accent_color=str(parameters.get("accent_color") or "#ff1745"),
                position=str(parameters.get("position") or "bottom-safe"),
                playbook=parameters.get("playbook") if isinstance(parameters.get("playbook"), str) else None,
                layers=parameters.get("layers") if isinstance(parameters.get("layers"), list) else None,
                caption_html=caption_html,
                composition_html=composition_html,
            )
            playbook_name = parameters.get("playbook")
            style_name = str(parameters.get("style") or "caption-highlight")
            authored = bool((caption_html or "").strip() or (composition_html or "").strip())
            result = {
                **rendered,
                "uri": rendered.get("result_url"),
                "video_url": rendered.get("result_url"),
                "title": "HyperFrames captioned video",
                "summary": (
                    (
                        "Rendered HyperFrames overlay (authored HTML)"
                        if authored else f"Rendered animated captions with {style_name}"
                    )
                    + (f" / {playbook_name}" if playbook_name else "")
                ),
                "source_transcript_artifact_id": transcript.id,
            }
        elif service_target == "generate_research_by_request":
            from app.services.agent.video.generate_research_by_request_service import (
                generate_research_by_request,
            )
            result = await generate_research_by_request(
                thread_id=project_thread_id,
                run_id=remote_run_id,
                user_input=task.apply_skill_context(str(
                    parameters.get("user_input")
                    or parameters.get("brief")
                    or task.objective
                    or run.objective
                    or ""
                )),
                content_category=str(parameters.get("content_category") or "") or None,
                detected_language=_detected_language_for_task(run, task, parameters),
            )
        elif service_target == "generate_outline_by_request":
            from app.services.agent.video.generate_outline_by_request_service import (
                generate_outline_by_request,
            )
            result = await generate_outline_by_request(
                thread_id=project_thread_id,
                user_id=run.user_id,
                user_option=user_option,
                run_id=remote_run_id,
                user_input=task.apply_skill_context(str(parameters.get("user_input") or task.objective or run.objective or "")),
                images=parameters.get("images") or [
                    item.uri for item in selected if getattr(item, "uri", None)
                ],
            )
        elif service_target == "generate_characters_by_request":
            from app.services.agent.video.generate_characters_by_request_service import (
                generate_characters_by_request,
            )
            result = await generate_characters_by_request(
                thread_id=project_thread_id,
                user_id=run.user_id,
                user_option=user_option,
                run_id=remote_run_id,
                user_input=task.apply_skill_context(str(parameters.get("user_input") or task.objective or run.objective or "")),
                images=parameters.get("images") or [
                    item.uri for item in selected if getattr(item, "uri", None)
                ],
            )
        elif service_target == "generate_scenes_by_request":
            from app.services.agent.video.generate_scenes_by_request_service import (
                generate_scenes_by_request,
            )
            result = await generate_scenes_by_request(
                thread_id=project_thread_id,
                user_id=run.user_id,
                user_option=user_option,
                run_id=remote_run_id,
                user_input=task.apply_skill_context(str(parameters.get("user_input") or task.objective or run.objective or "")),
                images=parameters.get("images"),
            )
        elif service_target == "generate_shots_by_request":
            from app.services.agent.video.generate_shots_by_request_service import (
                generate_shots_by_request,
            )
            result = await generate_shots_by_request(
                thread_id=project_thread_id,
                user_id=run.user_id,
                user_option=user_option,
                run_id=remote_run_id,
                user_input=task.apply_skill_context(str(parameters.get("user_input") or task.objective or run.objective or "")),
            )
        elif service_target == "generate_keyframes_by_request":
            from app.services.agent.video.generate_keyframes_by_request_service import (
                generate_keyframes_by_request,
            )
            raw_shot_uuids = parameters.get("shot_uuids")
            shot_uuids = None
            if isinstance(raw_shot_uuids, list):
                shot_uuids = [str(u) for u in raw_shot_uuids if u]
            result = await generate_keyframes_by_request(
                thread_id=project_thread_id,
                user_id=run.user_id,
                user_option=user_option,
                run_id=remote_run_id,
                user_input=task.apply_skill_context(str(parameters.get("user_input") or task.objective or run.objective or "")),
                shot_uuids=shot_uuids,
            )
        elif service_target == "generate_shot_videos_by_request":
            from app.services.agent.video.generate_shot_videos_by_request_service import (
                generate_shot_videos_by_request,
            )
            raw_kf = parameters.get("keyframe_uuids")
            raw_shots = parameters.get("shot_uuids")
            result = await generate_shot_videos_by_request(
                thread_id=project_thread_id,
                user_id=run.user_id,
                user_option=user_option,
                run_id=remote_run_id,
                user_input=task.apply_skill_context(str(parameters.get("user_input") or task.objective or run.objective or "")),
                keyframe_uuids=[str(u) for u in raw_kf if u] if isinstance(raw_kf, list) else None,
                shot_uuids=[str(u) for u in raw_shots if u] if isinstance(raw_shots, list) else None,
                shot_workflow_mode=str(parameters.get("shot_workflow_mode") or "") or None,
            )
        else:
            raise ValueError(f"unsupported local.service target: {service_target}")
        summary = (
            f"{capability.id} completed for thread {project_thread_id}; "
            f"inputs={len(selected)}; idempotency={idempotency_key}"
        )
        if isinstance(result, dict):
            summary = str(result.get("summary") or result.get("message") or summary)
            if service_target == "api_provider_generate" and result.get("raw_task_id"):
                remote_run_id = str(result["raw_task_id"])
        uri = None
        if isinstance(result, dict):
            uri = (
                result.get("uri")
                or result.get("final_video_url")
                or result.get("video_url")
                or result.get("audio_url")
                or result.get("result_url")
                or result.get("image_url")
            )
            if not uri:
                images = result.get("images")
                if isinstance(images, list):
                    for item in images:
                        if not isinstance(item, dict):
                            continue
                        candidate = (
                            item.get("image_url")
                            or item.get("uri")
                            or item.get("url")
                        )
                        if isinstance(candidate, str) and candidate.strip():
                            uri = candidate.strip()
                            break
        return DelegateResult(
            remote_run_id=remote_run_id,
            status="completed",
            remote_thread_id=project_thread_id,
            artifact={
                "title": (
                    str(result.get("title")).strip()
                    if isinstance(result, dict) and result.get("title")
                    else capability.id
                ),
                "summary": summary,
                "uri": uri,
                "metadata": result if isinstance(result, dict) else {"result": result},
            },
        )

    async def _assemble_videos(
        self,
        *,
        project_thread_id: str,
        run: AgentRun,
        task: Task,
        selected: list[ArtifactVersion],
        remote_run_id: str,
        user_option: Any,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Prefer VideoAgent assemble; fall back to artifact URL concat for Seedance clips."""
        from app.exceptions import BusinessException, BusinessExceptionCode
        from app.services.agent.video_agent_service import get_video_agent_service

        try:
            service = get_video_agent_service()
            return await service.video_assembly_by_request(
                thread_id=project_thread_id,
                segment_versions=parameters.get("segment_versions"),
                user_id=run.user_id,
                user_option=user_option,
                videos=parameters.get("videos"),
            )
        except BusinessException as exc:
            missing_project = (
                exc.error_code == BusinessExceptionCode.RESOURCE_NOT_FOUND
                and any(
                    token in str(exc.detail or "")
                    for token in ("故事梗概", "视频片段", "story", "segment")
                )
            )
            if not missing_project:
                raise

        video_urls = self._collect_artifact_video_urls(selected)
        if parameters.get("video_urls"):
            # Explicit URL list replaces artifact expansion (Skill-controlled).
            video_urls = []
            for item in parameters["video_urls"]:
                if isinstance(item, str) and item.strip():
                    video_urls.append(item.strip())
        # Preserve order, drop duplicates.
        ordered: list[str] = []
        seen: set[str] = set()
        for url in video_urls:
            if url in seen:
                continue
            seen.add(url)
            ordered.append(url)
        if len(ordered) < 1:
            raise ValueError(
                "video.assemble needs VideoAgent shot segments or input video artifacts"
            )
        if len(ordered) == 1:
            return {
                "summary": "single clip, no concat required",
                "message": "仅一段视频，无需拼接",
                "final_video_url": ordered[0],
                "uri": ordered[0],
                "video_count": 1,
                "assembly_mode": "artifact_passthrough",
            }

        from app.utils import media_service_client as msc

        concat = await msc.video_concat(
            ordered, run_id=remote_run_id, normalize=True,
        )
        final_url = concat["result_url"]
        return {
            "summary": f"assembled {len(ordered)} artifact clips",
            "message": f"已将 {len(ordered)} 段视频拼接为成片",
            "final_video_url": final_url,
            "uri": final_url,
            "duration": concat.get("duration"),
            "video_count": len(ordered),
            "source_video_urls": ordered,
            "assembly_mode": "artifact_concat",
            "fallback_reason": "videoagent_story_outline_missing",
        }

    @staticmethod
    def _collect_artifact_video_urls(selected: list[ArtifactVersion]) -> list[str]:
        """One URL per selected artifact: prefer canonical uri over nested history."""
        urls: list[str] = []
        for artifact in selected:
            if isinstance(artifact.uri, str) and artifact.uri.strip():
                urls.append(artifact.uri.strip())
                continue
            metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
            for key in ("video_url", "url", "uri", "final_video_url", "result_url"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    urls.append(value.strip())
                    break
            else:
                videos = metadata.get("videos")
                if isinstance(videos, list):
                    for item in reversed(videos):
                        if not isinstance(item, dict):
                            continue
                        for key in ("video_url", "url", "uri"):
                            value = item.get(key)
                            if isinstance(value, str) and value.strip():
                                urls.append(value.strip())
                                break
                        else:
                            continue
                        break
        return urls

    @staticmethod
    def _collect_artifact_audiomap(
        selected: list[ArtifactVersion],
    ) -> dict[str, Any] | None:
        typed = [
            artifact for artifact in selected
            if str(getattr(artifact, "type", "") or "").lower() == "audiomap"
        ]
        for artifact in typed or selected:
            metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
            if metadata.get("smart_clip") or metadata.get("sections"):
                return metadata
        return None

    @staticmethod
    def _collect_artifact_music_metadata(
        selected: list[ArtifactVersion],
    ) -> dict[str, Any]:
        for artifact in selected:
            kind = str(getattr(artifact, "type", "") or "").lower()
            metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
            uri = artifact.uri if isinstance(artifact.uri, str) else ""
            if kind not in {"music", "audio"} and not uri.lower().endswith(
                (".mp3", ".wav", ".m4a", ".aac", ".flac")
            ):
                continue
            lyrics = metadata.get("lyrics") or metadata.get("generated_lyrics")
            return {
                "lyrics": lyrics if isinstance(lyrics, str) else None,
                "clip_id": metadata.get("clip_id") or metadata.get("suno_clip_id"),
                "filename": metadata.get("filename") or metadata.get("title"),
            }
        return {}

    @staticmethod
    def _collect_artifact_audio_url(
        selected: list[ArtifactVersion],
        *,
        prefer_cut: bool = False,
    ) -> str | None:
        """Prefer music/audio artifacts; fall back to any uri that looks like audio."""
        if prefer_cut:
            for artifact in selected:
                if str(getattr(artifact, "type", "") or "").lower() != "audio_cut":
                    continue
                metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
                master = metadata.get("master")
                master_url = (
                    master.get("audio_url") if isinstance(master, dict) else None
                )
                for candidate in (master_url, artifact.uri, metadata.get("audio_url")):
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
        for artifact in selected:
            kind = str(getattr(artifact, "type", "") or "").lower()
            metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
            candidates: list[str] = []
            if isinstance(artifact.uri, str) and artifact.uri.strip():
                candidates.append(artifact.uri.strip())
            for key in ("audio_url", "music_url", "url", "uri", "result_url"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())
            for url in candidates:
                lower = url.lower()
                if kind in {"music", "audio"} or any(
                    lower.endswith(ext) for ext in (".mp3", ".wav", ".m4a", ".aac", ".flac")
                ):
                    return url
        return None

    async def _run_mcp_call(
        self,
        run: AgentRun,
        task: Task,
        selected: list[ArtifactVersion],
        idempotency_key: str,
        capability,
    ) -> DelegateResult:
        if self.mcp_bridge is None:
            raise ValueError(
                f"mcp.call capability {capability.id} has no MCP bridge configured"
            )
        return await self.mcp_bridge.call(
            run=run,
            task=task,
            selected=selected,
            idempotency_key=idempotency_key,
            capability=capability,
        )

    @staticmethod
    def _suggest_actions(task: Task, run: AgentRun | None = None) -> DelegateResult:
        formatted = str(task.parameters.get("formatted_suggestions") or task.objective)
        parsed = parse_formatted_action_suggestions_body(
            formatted, result_model=SuggestedActions,
        )
        if parsed is None:
            raw = task.parameters.get("suggestions")
            try:
                parsed = SuggestedActions.model_validate({
                    "reply_type": task.parameters.get("reply_type", "choice"),
                    "suggestions": raw,
                })
            except ValidationError:
                source = run.objective if run else task.objective
                is_zh = any("\u4e00" <= character <= "\u9fff" for character in source)
                parsed = SuggestedActions(suggestions=[
                    SuggestedAction(
                        label="完善视觉风格" if is_zh else "Define the visual style",
                        message=(
                            "帮我完善这个视频的视觉风格、叙事形式和整体氛围"
                            if is_zh else
                            "Help me define this video's visual style, narrative format, and mood"
                        ),
                    ),
                    SuggestedAction(
                        label="规划视频结构" if is_zh else "Plan the video structure",
                        message=(
                            "先规划视频时长、分镜、旁白和配乐"
                            if is_zh else
                            "First plan the duration, shots, narration, and soundtrack"
                        ),
                    ),
                ])
        if not any(item.is_direct_generate for item in parsed.suggestions):
            source = run.objective if run else task.objective
            is_zh = any("\u4e00" <= character <= "\u9fff" for character in source)
            parsed.suggestions.append(SuggestedAction(
                label="直接生成" if is_zh else "Generate now",
                message="直接生成" if is_zh else "Generate now",
                is_direct_generate=True,
                generation_input=task.parameters.get("generation_input"),
            ))
        return DelegateResult(
            remote_run_id=f"local:{task.id}",
            status="completed",
            artifact={
                "title": "Suggested actions",
                "summary": parsed.model_dump_json(),
                "metadata": parsed.model_dump(mode="json"),
            },
        )
