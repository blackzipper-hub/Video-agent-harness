"""Capability descriptions for platform provider/protocol integrations."""

from app.capabilities.models import CapabilityInputs, CapabilityManifest, default_terminal_events


def provider_capabilities() -> list[CapabilityManifest]:
    return [
        CapabilityManifest(
            id="api.provider.generate",
            description=(
                "Generate video. Set model to minimax-h3 (MV H3) or "
                "doubao-seedance-2-0 / seedance-2.5. Do not put MiniMax or H3 in provider."
            ),
            executor="local.service",
            service_target="api_provider_generate",
            skill_name="api-provider-bridge",
            inputs=CapabilityInputs(optional=["image", "video", "audio", "music", "audio_cut"]),
            output_type="video",
            terminal_events=default_terminal_events("video"),
            parameters_schema={
                "type": "object",
                "required": ["prompt", "model"],
                "properties": {
                    "prompt": {"type": "string", "minLength": 1},
                    "model": {
                        "type": "string",
                        "enum": [
                            "minimax-h3",
                            "h3",
                            "doubao-seedance-2-0",
                            "doubao-seedance-2-0-260128",
                            "seedance-2.5",
                            "seedance-2.0",
                        ],
                        "description": (
                            "Which generator. MV H3: minimax-h3 (also h3). "
                            "Seedance: doubao-seedance-2-0 or seedance-2.5. "
                            "Not a vendor brand name."
                        ),
                    },
                    "provider": {
                        "type": "string",
                        "enum": ["wavespeed", "ark"],
                        "description": (
                            "HTTP backend only. Omit this. Do not put MiniMax, H3, "
                            "Hailuo, or Seedance here — those go in model."
                        ),
                    },
                    "duration": {"type": "integer"},
                    "resolution": {"type": "string"},
                    "aspect_ratio": {"type": "string"},
                    "generate_audio": {"type": "boolean"},
                    "images": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Identity and setting references. Include uploaded sources "
                            "that lock identity, not only a generated sheet."
                        ),
                    },
                    "videos": {"type": "array", "items": {"type": "string"}},
                    "audios": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
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
    ]
