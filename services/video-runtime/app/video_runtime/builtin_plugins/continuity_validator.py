from __future__ import annotations

import os

from ..models import MediaArtifactVersion, ValidationResult
from ..plugins import BaseVideoPlugin, PluginContext


class ContinuityValidatorPlugin(BaseVideoPlugin):
    """Structural checks always run; the existing Cuti VLM check is optional."""

    async def validate_artifact(
        self, context: PluginContext, artifact: MediaArtifactVersion,
    ) -> list[ValidationResult]:
        if artifact.type not in {"timeline", "video_clip", "final_video", "video_mixed", "video_assembled"}:
            return []
        issues: list[str] = []
        metadata: dict = {"mode": "structural"}
        if artifact.type in {"video_clip", "final_video", "video_mixed", "video_assembled"} and not artifact.uri:
            issues.append("video artifact has no playable URI")
        if artifact.type == "timeline":
            timeline = artifact.metadata.get("timeline") or {}
            items = timeline.get("items") or []
            cursor = 0.0
            for item in items:
                start = float(item.get("startSeconds", -1))
                duration = float(item.get("durationSeconds", 0))
                if abs(start - cursor) > 0.001:
                    issues.append("timeline contains a gap or overlap")
                if duration <= 0 or not item.get("artifactVersionId"):
                    issues.append("timeline contains an invalid clip reference")
                cursor = start + duration

        parameters = artifact.metadata.get("generation_parameters") or {}
        if not issues and artifact.uri and parameters.get("final_output"):
            from app.utils import media_service_client as msc
            try:
                info = await msc.video_info(str(artifact.uri))
                metadata = {"mode": "media-service-ffprobe", "videoInfo": info}
                duration = float(info.get("duration") or 0)
                target = float(parameters.get("target_duration_seconds") or 0)
                if duration <= 0:
                    issues.append("final video cannot be decoded")
                elif target and abs(duration - target) > 1:
                    issues.append(f"final video duration {duration:.2f}s differs from target {target:.2f}s")
                if parameters.get("require_audio") and not info.get("has_audio"):
                    issues.append("final video has no audio track")
            except BaseException as exc:
                issues.append(f"final video probe failed: {exc}")

        if (
            not issues
            and artifact.type == "video_clip"
            and os.getenv("VIDEO_CONTINUITY_LLM_ENABLED", "").lower() in {"1", "true", "yes"}
        ):
            parameters = artifact.metadata.get("generation_parameters") or {}
            start_image_url = parameters.get("start_image_url")
            if start_image_url:
                from app.tools.video.video_consistency import check_video_consistency_llm
                checked = await check_video_consistency_llm(
                    str(start_image_url), str(artifact.uri),
                    str(parameters.get("prompt") or ""),
                    character_ref_image_urls=list(parameters.get("image_urls") or []),
                )
                metadata = {"mode": "cuti-video-consistency", "result": checked.model_dump(mode="json")}
                if not checked.passed:
                    issues.append(checked.reason_overall or "video continuity check failed")
        return [ValidationResult(
            project_id=artifact.project_id,
            build_id=context.build_id or "",
            artifact_version_id=artifact.id,
            validator_id="cuti.continuity-validator",
            passed=not issues,
            issues=issues,
            metadata=metadata,
        )]
