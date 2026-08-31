"""Platform-owned atomic capability descriptions.

These are execution contracts, not Skills: they contain no prompt methodology,
creative knowledge, or workflow fragments.
"""

from __future__ import annotations

from app.capabilities.models import CapabilityInputs, CapabilityManifest, default_terminal_events


def _manifest(
    capability_id: str,
    description: str,
    executor: str,
    output_type: str,
    *,
    alias: str,
    target_agent: str | None = None,
    service_target: str | None = None,
    mode: str | None = None,
    required: list[str] | None = None,
    optional: list[str] | None = None,
    parameters_schema: dict | None = None,
) -> CapabilityManifest:
    return CapabilityManifest(
        id=capability_id,
        description=description,
        executor=executor,
        target_agent=target_agent,
        service_target=service_target,
        mode=mode,
        # Kept only as a backwards-compatible command alias. It no longer
        # means that the capability is loaded from a Skill package.
        skill_name=alias,
        inputs=CapabilityInputs(required=required or [], optional=optional or []),
        output_type=output_type,
        terminal_events=default_terminal_events(output_type),
        parameters_schema=parameters_schema or {},
        trust_level="trusted",
    )


def platform_capabilities() -> list[CapabilityManifest]:
    """Return platform-owned atomic execution capabilities."""
    return [
        _manifest("atomic.text.generate", "Generate one text artifact directly from the final prompt. Media model IDs are never used for this language-model call.", "atomic.direct", "text", alias="atomic-text", optional=["text", "image"], parameters_schema={"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string", "minLength": 1}, "llm_model": {"type": "string", "description": "Optional OpenAI language-model override"}, "model": {"type": "string", "description": "Legacy text-model override; media model IDs are ignored"}, "artifact_title": {"type": "string"}}, "additionalProperties": True}),
        _manifest("atomic.image.generate", "Generate one image directly from the final prompt and explicit references.", "atomic.direct", "image", alias="atomic-image", optional=["text", "image"], parameters_schema={"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string", "minLength": 1}, "model": {"type": "string"}, "resolution": {"type": "string", "enum": ["480p", "720p", "1080p"]}, "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"]}, "images": {"type": "array", "items": {"type": "string"}}, "artifact_title": {"type": "string"}}, "additionalProperties": True}),
        _manifest("atomic.music.generate", "Generate one music artifact directly from the final prompt.", "atomic.direct", "music", alias="atomic-music", parameters_schema={"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string", "minLength": 1}, "has_lyrics": {"type": "boolean"}, "auto_lyrics": {"type": "boolean"}, "target_duration": {"type": "integer", "minimum": 1}, "tags": {"type": "string"}, "vocal_gender": {"type": "string", "enum": ["f", "m"]}, "artifact_title": {"type": "string"}}, "additionalProperties": True}),
        _manifest("atomic.video.generate", "Generate one video directly from the final prompt and explicit references. Use start_image_url for a strict opening frame; generic images remain identity/style references.", "atomic.direct", "video", alias="atomic-video", optional=["image", "music", "video"], parameters_schema={"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string", "minLength": 1}, "provider": {"type": "string"}, "model": {"type": "string"}, "generation_mode": {"type": "string", "enum": ["t2v", "i2v", "image_to_video", "image-to-video"]}, "duration": {"type": "integer"}, "resolution": {"type": "string"}, "aspect_ratio": {"type": "string"}, "generate_audio": {"type": "boolean"}, "start_image_url": {"type": "string", "description": "Strict first-frame input for image-to-video generation"}, "end_image_url": {"type": "string", "description": "Optional strict last-frame input; never inferred from generic references"}, "images": {"type": "array", "items": {"type": "string"}}, "videos": {"type": "array", "items": {"type": "string"}}, "audios": {"type": "array", "items": {"type": "string"}}, "artifact_title": {"type": "string"}}, "additionalProperties": True}),
        _manifest("story.generate", "Generate or rewrite a complete story.", "video-agent.delegate", "story", alias="generate-story", target_agent="story", optional=["image", "music"]),
        _manifest("image.generate", "Generate an image using selected project artifacts.", "video-agent.delegate", "image", alias="generate-image", target_agent="image", optional=["story", "music"]),
        _manifest("music.generate", "Generate music using selected project artifacts.", "video-agent.delegate", "music", alias="generate-music", target_agent="music", optional=["story", "image"]),
        _manifest("video.generate", "Generate one video in instant mode.", "video-agent.delegate", "video", alias="generate-video", target_agent="video", mode="instant", optional=["story", "image", "music", "video"]),
        _manifest("video_gen.generate", "Generate video through the direct video generation agent.", "video-agent.delegate", "video", alias="generate-video-direct", target_agent="video_gen", optional=["story", "image", "music", "video"]),
        _manifest("video.edit", "Edit an existing selected video.", "video-agent.delegate", "video", alias="video-edit", target_agent="video", mode="edit", required=["video"]),
        _manifest("outline.generate", "Generate a persisted story outline with chapters.", "local.service", "outline", alias="generate-outline", service_target="generate_outline_by_request", optional=["image", "music"]),
        _manifest("character.generate", "Generate main characters for a project.", "local.service", "character", alias="generate-characters", service_target="generate_characters_by_request", optional=["outline", "story", "image"]),
        _manifest("scene.generate", "Generate scenes from a story outline.", "local.service", "scene", alias="generate-scenes", service_target="generate_scenes_by_request", optional=["outline", "story", "character", "image"]),
        _manifest("shot.generate", "Generate detailed storyboard shots.", "local.service", "shot", alias="generate-shots", service_target="generate_shots_by_request", optional=["scene", "story", "image"]),
        _manifest("keyframe.generate", "Generate keyframes for detailed shots.", "local.service", "keyframe", alias="generate-keyframe", service_target="generate_keyframes_by_request", optional=["shot", "story", "image"]),
        _manifest("shot.video.generate", "Generate videos for existing shots.", "local.service", "video", alias="generate-shot-videos", service_target="generate_shot_videos_by_request", required=["shot"], optional=["story", "keyframe", "outline", "character", "image"]),
        _manifest("character.regenerate", "Regenerate selected characters.", "local.service", "character", alias="regenerate-characters", service_target="execute_regenerate_characters", optional=["character", "story", "image"]),
        _manifest("keyframe.regenerate", "Regenerate selected keyframes.", "local.service", "keyframe", alias="regenerate-keyframes", service_target="execute_regenerate_keyframes", optional=["keyframe", "story", "image"]),
        _manifest("shot.video.regenerate", "Regenerate selected shot videos.", "local.service", "video", alias="regenerate-shot-videos", service_target="execute_regenerate_videos", optional=["video", "keyframe", "story"]),
        _manifest("video.assemble", "Assemble selected shot segments into a final video.", "local.service", "video", alias="assemble-video", service_target="video_assembly_by_request", optional=["video"]),
        _manifest("actions.suggest", "Produce structured recommended next actions.", "local.structured", "action_suggestions", alias="suggest-actions", service_target="action_suggestions"),
        _manifest("media.concat", "Concatenate an ordered list of videos. Set transition_duration for a short cross-fade between continuation clips.", "local.service", "video", alias="media-concat", service_target="media_concat", required=["video"], parameters_schema={"type": "object", "required": ["video_urls"], "properties": {"video_urls": {"type": "array", "items": {"type": "string"}, "minItems": 1}, "normalize": {"type": "boolean"}, "transition_duration": {"type": "number", "minimum": 0, "maximum": 1, "default": 0}, "run_id": {"type": "string"}}, "additionalProperties": False}),
        _manifest("media.extract_frame", "Extract a still frame from a video. Use position=last for continuation generation; it resolves the true final decoded video frame.", "local.service", "image", alias="media-extract-frame", service_target="media_extract_frame", required=["video"], parameters_schema={"type": "object", "properties": {"timestamp": {"type": "number", "minimum": 0}, "position": {"type": "string", "enum": ["timestamp", "last"], "default": "timestamp"}, "video_url": {"type": "string"}, "format": {"type": "string", "enum": ["jpeg", "png"], "default": "jpeg"}, "run_id": {"type": "string"}}, "additionalProperties": False}),
        _manifest(
            "media.audio_trim",
            "Trim an audio URL to [start, start+duration). Optional fade_in_sec/fade_out_sec use trim-with-fade. Required for Seedance MV reference clips (total audio ≤15s).",
            "local.service",
            "music",
            alias="media-audio-trim",
            service_target="media_audio_trim",
            optional=["music"],
            parameters_schema={
                "type": "object",
                "required": ["audio_url", "duration"],
                "properties": {
                    "audio_url": {"type": "string"},
                    "start": {"type": "number", "minimum": 0, "default": 0},
                    "start_sec": {"type": "number", "minimum": 0},
                    "duration": {"type": "number", "exclusiveMinimum": 0},
                    "duration_sec": {"type": "number", "exclusiveMinimum": 0},
                    "fade_in_sec": {"type": "number", "minimum": 0},
                    "fade_out_sec": {"type": "number", "minimum": 0},
                    "run_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        ),
        _manifest(
            "media.audio_analyze",
            "Analyze a music track into a master window + Seedance-safe segments (≤15s). Optional target_duration_sec center-crops or uses smart_clip when transcription is provided. Music is the timeline spine for MV.",
            "local.service",
            "json",
            alias="media-audio-analyze",
            service_target="media_audio_analyze",
            optional=["music"],
            parameters_schema={
                "type": "object",
                "required": ["audio_url"],
                "properties": {
                    "audio_url": {"type": "string"},
                    "target_duration_sec": {"type": "number", "exclusiveMinimum": 0},
                    "start_sec": {"type": "number", "minimum": 0},
                    "end_sec": {"type": "number", "exclusiveMinimum": 0},
                    "max_segment_sec": {"type": "number", "minimum": 1, "maximum": 15, "default": 15},
                    "transcription": {"type": "object"},
                    "run_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        ),
        _manifest(
            "media.mix_audio",
            "Mix a master audio track onto a video. mode=replace (default, MV) swaps in the track; mode=overlay ducks/mixes with in-clip audio.",
            "local.service",
            "video",
            alias="media-mix-audio",
            service_target="media_mix_audio",
            required=["video"],
            optional=["music"],
            parameters_schema={
                "type": "object",
                "required": ["video_url", "audio_url"],
                "properties": {
                    "video_url": {"type": "string"},
                    "audio_url": {"type": "string"},
                    "music_url": {"type": "string"},
                    "mode": {"type": "string", "enum": ["replace", "overlay"], "default": "replace"},
                    "audio_volume": {"type": "number", "minimum": 0, "maximum": 2, "default": 0.35},
                    "loop_audio": {"type": "boolean", "default": False},
                    "run_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        ),
        _manifest(
            "media.transcribe",
            "Transcribe the selected video's audio into a persisted timestamped transcript. Use as the first subtitle stage.",
            "local.service",
            "transcript",
            alias="media-transcribe",
            service_target="media_transcribe",
            required=["video"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "video_url": {"type": "string"},
                    "language": {"type": "string"},
                    "model": {"type": "string"},
                    "run_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        ),
        _manifest(
            "subtitle.compose",
            "Create and validate a durable SRT, VTT, or ASS subtitle artifact from a timestamped transcript.",
            "local.service",
            "subtitle",
            alias="subtitle-compose",
            service_target="subtitle_compose",
            required=["transcript"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "cues": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["start", "end", "text"],
                            "properties": {
                                "start": {"type": "number", "minimum": 0},
                                "end": {"type": "number", "exclusiveMinimum": 0},
                                "text": {"type": "string", "minLength": 1},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "format": {"type": "string", "enum": ["srt", "vtt", "ass"]},
                    "timing_mode": {
                        "type": "string",
                        "enum": ["audio", "manual"],
                        "default": "audio",
                    },
                    "max_lines": {"type": "integer", "minimum": 1, "maximum": 3},
                    "max_chars_per_line": {"type": "integer", "minimum": 4, "maximum": 100},
                    "max_cps": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
                    "run_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        ),
        _manifest(
            "media.subtitle_burn",
            "Burn a selected subtitle artifact into a selected video and persist a new captioned MP4.",
            "local.service",
            "video",
            alias="media-subtitle-burn",
            service_target="media_subtitle_burn",
            required=["video", "subtitle"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "video_url": {"type": "string"},
                    "subtitle_url": {"type": "string"},
                    "style_preset": {
                        "type": "string",
                        "enum": ["short-video-bold", "clean", "minimal"],
                        "default": "clean",
                    },
                    "position": {
                        "type": "string",
                        "enum": ["bottom-safe", "bottom", "center", "top"],
                        "default": "bottom-safe",
                    },
                    "font_name": {"type": "string", "maxLength": 80},
                    "run_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        ),
        _manifest(
            "media.hyperframes_caption",
            "Render animated, word-synchronized HyperFrames captions over a selected video and persist a new MP4.",
            "local.service",
            "video",
            alias="media-hyperframes-caption",
            service_target="media_hyperframes_caption",
            required=["video", "transcript"],
            parameters_schema={
                "type": "object",
                "properties": {
                    "video_url": {"type": "string"},
                    "style": {
                        "type": "string",
                        "enum": [
                            "caption-highlight", "caption-pill-karaoke",
                            "caption-editorial-emphasis", "caption-glitch-rgb",
                            "caption-kinetic-slam", "caption-neon-glow",
                            "caption-neon-accent", "caption-clip-wipe",
                            "caption-gradient-fill", "caption-matrix-decode",
                            "caption-emoji-pop", "caption-parallax-layers",
                            "caption-particle-burst", "caption-texture",
                            "caption-weight-shift",
                        ],
                        "default": "caption-highlight",
                    },
                    "accent_color": {
                        "type": "string",
                        "pattern": "^#[0-9A-Fa-f]{6}$",
                        "default": "#ff1745",
                    },
                    "position": {
                        "type": "string",
                        "enum": ["bottom-safe", "lower-middle", "center"],
                        "default": "bottom-safe",
                    },
                    "run_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        ),
    ]
