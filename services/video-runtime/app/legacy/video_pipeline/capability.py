"""Manifest for the disabled legacy fixed master pipeline fallback."""

from app.capabilities.models import CapabilityInputs, CapabilityManifest, default_terminal_events


def legacy_video_pipeline_capability() -> CapabilityManifest:
    return CapabilityManifest(
        id="video.pipeline.generate",
        description="Legacy fixed VideoAgent master pipeline; disabled in dynamic Studio.",
        executor="video-agent.delegate",
        target_agent="video",
        mode="master",
        skill_name="generate-video-pipeline",
        inputs=CapabilityInputs(optional=["story", "image", "music", "video"]),
        output_type="video",
        terminal_events=default_terminal_events("video"),
        # Dynamic Studio disables this through runtime policy. Keeping the
        # manifest enabled here preserves the explicit legacy fallback path.
        enabled=True,
        trust_level="trusted",
    )
