"""Capability descriptions for platform provider/protocol integrations."""

from app.capabilities.models import CapabilityInputs, CapabilityManifest, default_terminal_events


def provider_capabilities() -> list[CapabilityManifest]:
    return [
        CapabilityManifest(
            id="api.provider.generate",
            description="Generate video through the configured provider fallback bridge.",
            executor="local.service",
            service_target="api_provider_generate",
            skill_name="api-provider-bridge",
            inputs=CapabilityInputs(optional=["image", "video", "audio", "music", "audio_cut"]),
            output_type="video",
            terminal_events=default_terminal_events("video"),
            parameters_schema={"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "provider": {"type": "string"}, "model": {"type": "string"}, "duration": {"type": "integer"}, "resolution": {"type": "string"}, "aspect_ratio": {"type": "string"}, "generate_audio": {"type": "boolean"}, "images": {"type": "array", "items": {"type": "string"}}, "videos": {"type": "array", "items": {"type": "string"}}, "audios": {"type": "array", "items": {"type": "string"}}}, "additionalProperties": True},
        ),
        CapabilityManifest(
            id="api.ark_protocol.generate",
            description="Translate Ark generation requests to the configured WaveSpeed provider.",
            executor="local.service",
            service_target="ark_protocol_generate",
            skill_name="ark-wavespeed-protocol-bridge",
            inputs=CapabilityInputs(optional=["image", "video", "audio", "music", "audio_cut"]),
            output_type="video",
            terminal_events=default_terminal_events("video"),
            parameters_schema={"type": "object", "required": ["body"], "properties": {"body": {"type": "object"}, "wait": {"type": "boolean"}, "poll_interval": {"type": "number"}}, "additionalProperties": False},
        ),
        CapabilityManifest(
            id="open_montage.tool.invoke",
            description="Invoke an Open Montage media tool through the platform integration.",
            executor="local.service",
            service_target="open_montage_tool_invoke",
            skill_name="open-montage-tools",
            inputs=CapabilityInputs(optional=["video", "image", "audio"]),
            output_type="video",
            terminal_events=default_terminal_events("video"),
            parameters_schema={"type": "object", "required": ["tool"], "properties": {"tool": {"type": "string"}, "inputs": {"type": "object"}, "run_id": {"type": "string"}}, "additionalProperties": False},
        ),
    ]

