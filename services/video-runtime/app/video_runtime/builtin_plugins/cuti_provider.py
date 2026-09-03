from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from ..models import MediaArtifactVersion
from ..plugins import BaseVideoPlugin
from ..security import CapabilityExecutionEnvelope


AtomicExecutor = Callable[..., Awaitable[tuple[str, dict[str, Any]]]]


class CutiAtomicProviderPlugin(BaseVideoPlugin):
    """Transition adapter that reuses Cuti's proven atomic provider implementations."""

    CAPABILITIES = (
        "atomic.text.generate",
        "atomic.image.generate",
        "atomic.music.generate",
        "atomic.video.generate",
        "suno.generate",
        "api.provider.generate",
    )

    def __init__(self, executor: AtomicExecutor | None = None) -> None:
        self._executor = executor

    def capability_handlers(self):
        return {capability: self._generate for capability in self.CAPABILITIES}

    async def _generate(
        self,
        envelope: CapabilityExecutionEnvelope,
        payload: dict[str, Any],
    ) -> MediaArtifactVersion:
        from app.chat.v2.models import AgentRun, ArtifactVersion, Task
        from app.orchestration.skills.models import SkillContext

        planned = payload.get("step")
        if planned is not None:
            step = dict(planned)
            source = MediaArtifactVersion(
                artifact_id=str(step.get("output_artifact_id") or ""),
                project_id=envelope.grant.project_id,
                type=str(step.get("output_artifact_type") or "generated_media"),
                title=str((step.get("parameters") or {}).get("title") or step.get("step_id") or "Media"),
                summary=str((step.get("parameters") or {}).get("prompt") or ""),
                metadata={
                    "generation_parameters": dict(step.get("parameters") or {}),
                    "rebuild_capability": envelope.grant.capability,
                    "plan_step_id": step.get("step_id"),
                },
            )
            completed_payload = payload.get("completed_artifacts", {})
            raw_skill_context = step.get("skill_context")
        else:
            source = MediaArtifactVersion.model_validate(payload["source"])
            completed_payload = payload.get("completed_replacements", {})
            raw_skill_context = source.metadata.get("skill_context")
        skill_context = (
            SkillContext.model_validate(raw_skill_context)
            if raw_skill_context
            else None
        )
        metadata = dict(source.metadata)
        parameters = dict(metadata.get("generation_parameters") or {})
        parameters.update(metadata.get("rebuild_parameters") or {})
        completed_by_step = {
            key: MediaArtifactVersion.model_validate(value)
            for key, value in completed_payload.items()
        }
        start_step = parameters.pop("start_image_from_step", None)
        strict_start_step = parameters.pop("strict_start_frame_from_step", None)
        character_step = parameters.pop("character_reference_from_step", None)
        character_steps = parameters.pop("character_reference_from_steps", None) or []
        reference_steps = parameters.pop("reference_from_steps", None) or []
        video_reference_steps = parameters.pop("video_reference_from_steps", None) or []
        audio_reference_steps = parameters.pop("audio_reference_from_steps", None) or []
        audio_reference_step = parameters.pop("audio_reference_from_step", None)
        audio_segment_index = parameters.pop("audio_segment_index", None)
        start_artifact = completed_by_step.get(str(start_step or strict_start_step or ""))
        if start_artifact and start_artifact.uri:
            parameters.setdefault("start_image_url", start_artifact.uri)
            parameters.setdefault("image_url", start_artifact.uri)
        character_artifact = completed_by_step.get(str(character_step or ""))
        if character_artifact and character_artifact.uri:
            parameters.setdefault("images", [character_artifact.uri])
            parameters.setdefault("image_urls", [character_artifact.uri])
        character_urls = [
            completed_by_step[str(step)].uri
            for step in character_steps
            if completed_by_step.get(str(step)) and completed_by_step[str(step)].uri
        ]
        if character_urls:
            parameters.setdefault("images", character_urls)
            parameters.setdefault("image_urls", character_urls)
        reference_urls = [
            completed_by_step[str(step)].uri
            for step in reference_steps
            if completed_by_step.get(str(step)) and completed_by_step[str(step)].uri
        ]
        if reference_urls:
            parameters.setdefault("images", reference_urls)
            parameters.setdefault("reference_urls", reference_urls)
            parameters.setdefault("image_urls", reference_urls)
        video_reference_urls = [
            completed_by_step[str(step)].uri
            for step in video_reference_steps
            if completed_by_step.get(str(step)) and completed_by_step[str(step)].uri
        ]
        if video_reference_urls:
            parameters.setdefault("videos", video_reference_urls)
            parameters.setdefault("reference_videos", video_reference_urls)
            parameters.setdefault("video_urls", video_reference_urls)
        audio_reference_urls = [
            completed_by_step[str(step)].uri
            for step in audio_reference_steps
            if completed_by_step.get(str(step)) and completed_by_step[str(step)].uri
        ]
        if audio_reference_urls:
            parameters.setdefault("audios", audio_reference_urls)
            parameters.setdefault("reference_audios", audio_reference_urls)
            parameters.setdefault("audio_urls", audio_reference_urls)
        audio_reference = completed_by_step.get(str(audio_reference_step or ""))
        if audio_reference:
            audio_url = audio_reference.uri
            if audio_segment_index is not None:
                segments = audio_reference.metadata.get("segments") or []
                index = int(audio_segment_index)
                if index >= len(segments):
                    raise ValueError(f"audio cut has no segment {index}")
                segment = segments[index] if isinstance(segments[index], dict) else {}
                audio_url = segment.get("audio_url") or audio_url
            if audio_url:
                parameters.setdefault("audios", [audio_url])
                parameters.setdefault("audio_url", audio_url)
                parameters.setdefault("audio_urls", [audio_url])
        explicit_prompt = parameters.get("prompt")
        if not explicit_prompt:
            objective = str(parameters.get("objective") or "").strip()
            instruction = str(parameters.get("instruction") or "").strip()
            explicit_prompt = "\n\n".join(
                value for value in (objective, instruction) if value
            )
        prompt = str(
            explicit_prompt
            or metadata.get("rebuild_prompt")
            or metadata.get("prompt")
            or source.summary
            or source.title
        ).strip()
        if not prompt:
            raise ValueError("Cuti provider rebuild requires a persisted prompt")
        # Match Cuti's atomic execution boundary: Workflow/Director instructions
        # may guide an LLM planning/text stage, but a leaf media provider must
        # consume the already-compiled prompt exactly as written.  Appending an
        # entire Workflow SKILL to an image/video prompt makes the provider try
        # to render every workflow section (for example character + scene +
        # product sheets in one image) instead of the requested artifact.
        skill_prompt_applied = (
            skill_context is not None
            and envelope.grant.capability == "atomic.text.generate"
        )
        if skill_prompt_applied:
            prompt = skill_context.apply_to(prompt)
        parameters["prompt"] = prompt
        parameters.setdefault("artifact_title", source.title or source.type)

        selected = [source, *[
            MediaArtifactVersion.model_validate(item)
            for item in completed_payload.values()
        ]]
        legacy_artifacts = [ArtifactVersion(
            id=item.id,
            artifact_id=item.artifact_id,
            project_id=item.project_id,
            type=item.type,
            version=item.version,
            status=item.status,
            produced_by_task_id=f"video-build:{payload['build']['id']}",
            title=item.title,
            summary=item.summary,
            uri=item.uri,
            metadata=item.metadata,
            created_at=item.created_at,
        ) for item in selected]
        run = AgentRun(
            id=f"video-build:{payload['build']['id']}",
            thread_id=envelope.grant.session_id,
            project_id=envelope.grant.project_id,
            user_id=envelope.grant.user_id,
            objective=prompt,
            idempotency_key=envelope.grant.idempotency_key,
            output_language=str(metadata.get("output_language") or "en"),
        )
        task = Task(
            run_id=run.id,
            revision=0,
            client_key=str(metadata.get("plan_step_id") or source.id),
            capability_id=envelope.grant.capability,
            objective=prompt,
            input_artifact_version_ids=[item.id for item in legacy_artifacts],
            parameters=parameters,
            resolved_skills=(
                list(skill_context.applied_skills) if skill_context is not None else []
            ),
            skill_context=skill_context,
        )
        capability = envelope.grant.capability
        if capability == "suno.generate":
            from app.integrations.providers.suno_bridge import generate_suno_native

            profile = dict(parameters)
            profile.pop("workflow_parameters", None)
            generated = await generate_suno_native(profile)
            remote_operation_id = str(generated.get("clip_id") or envelope.grant.idempotency_key)
        elif capability == "api.provider.generate":
            from app.integrations.providers.provider_bridge import generate_video

            profile = dict(parameters)
            profile.pop("workflow_parameters", None)
            generated = await generate_video(
                profile, on_remote_submitted=envelope.report_remote_operation,
            )
            if not generated.get("uri"):
                generated["uri"] = generated.get("video_url")
            remote_operation_id = str(
                generated.get("raw_task_id") or envelope.grant.idempotency_key
            )
        else:
            executor = self._executor
            if executor is None:
                from app.chat.v2.atomic_executor import execute_atomic
                executor = execute_atomic
            remote_operation_id, generated = await executor(
                run=run,
                task=task,
                selected=legacy_artifacts,
                idempotency_key=envelope.grant.idempotency_key,
                on_remote_submitted=envelope.report_remote_operation,
            )
        generated_metadata = {
            **metadata,
            **dict(generated.get("metadata") or {}),
            "rebuild_capability": envelope.grant.capability,
            # Keep the concrete URLs resolved from BuildStep dependencies for
            # post-generation identity/continuity validators. The Runtime also
            # preserves the immutable, unresolved plan parameters separately.
            "resolved_generation_parameters": dict(parameters),
            "skill_prompt_applied": skill_prompt_applied,
        }
        digest_payload = json.dumps(
            {"uri": generated.get("uri"), "metadata": generated_metadata},
            sort_keys=True,
            default=str,
        ).encode()
        return MediaArtifactVersion(
            artifact_id=source.artifact_id,
            project_id=source.project_id,
            type=source.type,
            version=source.version + 1,
            uri=generated.get("uri"),
            title=str(generated.get("title") or source.title),
            summary=str(generated.get("summary") or source.summary),
            content_digest=hashlib.sha256(digest_payload).hexdigest(),
            generation_spec_digest=hashlib.sha256(
                json.dumps(parameters, sort_keys=True, default=str).encode(),
            ).hexdigest(),
            provider_id=str(generated_metadata.get("provider_used") or "cuti"),
            provider_version=str(generated_metadata.get("model") or "legacy-atomic"),
            plugin_id="cuti.atomic-providers",
            plugin_version="1.0.0",
            provenance={
                "source_artifact_version_id": source.id if planned is None else None,
                "remote_operation_id": remote_operation_id,
                "idempotency_key": envelope.grant.idempotency_key,
                "skills": (
                    [
                        item.model_dump(mode="json")
                        for item in skill_context.applied_skills
                    ]
                    if skill_context is not None
                    else []
                ),
            },
            metadata=generated_metadata,
        )
