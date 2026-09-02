# OpenMontage × Cuti compatibility

This bundle is a **filtered** OpenMontage Layer-2 skill set for Cuti video generation.
Upstream: https://github.com/calesthio/OpenMontage

Raw OpenMontage Python tools are **not** executed inside the slim sandbox (they need
FFmpeg locally, fal.ai, Remotion/Node, WhisperX, ComfyUI, GPU avatars, etc.).
Callable tools are **bridged** to Cuti host/Media/provider capabilities.

Filtered instruction files live under `.filtered/` (hidden from `list_resources`).

## Callable tools (bridged)

| OpenMontage tool | Cuti target | Supported ops |
|------------------|-------------|-----------------|
| `video_trimmer` | `media.trim` / `media.speed_adjust` / `media.concat` | `cut` (from t=0 to `target_duration` only), `speed` (via `target_duration`), `concat` |
| `video_stitch` | `media.concat` | `stitch` with `transition=cut` only |
| `frame_sampler` | `media.extract_frame` | `timestamps` (one or more), single-frame convenience |
| `seedance_video` | `api.provider.generate` / `provider.generate` | text/image-to-video via Ark→WaveSpeed bridge |
| `video_selector` | `api.provider.generate` | same as seedance; Cuti selects live provider |

Invoke via capability `open_montage.tool.invoke` (system skill `open-montage-tools`)
or `run_skill_script` → `scripts/om_tools.py`.

## Filtered tools (not callable) — why

| Family / tool | Reason |
|---------------|--------|
| Remotion / HyperFrames / `video_compose` spatial / caption burn | Need Node remotion runtime + project scaffolding; Cuti has no Remotion host |
| WhisperX / Azure STT / DashScope ASR / subtitle-sync | Local GPU/STT stack not shipped; Cuti Media has no transcription API |
| Avatar / lip-sync / talking-head / HeyGen | Local GPU or third-party avatar APIs not wired into Cuti host |
| ComfyUI / local diffusion / LTX local / WAN local | Require ComfyUI/GPU workers absent from product Media service |
| Stock scrapers (Pexels/Pixabay video/image) | No stock API keys + policy; use user uploads / generated media |
| TTS / music selectors (ElevenLabs, Suno, …) | Use Cuti stage/music capabilities instead; OM audio keys not configured |
| Enhancement (upscale, face restore, bg remove, eye enhance) | Need local enhancement models / paid APIs not in Media |
| Scene detect / face tracker / visual QA / video understand | Analysis stack not exposed as host capabilities |
| Other cloud video APIs (Kling/Sora/Veo/Runway direct) | Prompting guides kept; execution goes through Cuti `provider.generate` / Seedance bridge only |
| Screen capture / publishers / corpus builders | Outside Cuti create-video product surface |

## Filtered skills (instruction) — why

Moved to `.filtered/`:

| Path | Reason |
|------|--------|
| `core/remotion.md`, `hyperframes.md`, `whisperx.md`, `subtitle-sync.md` | Runtimes/APIs not available on Cuti |
| `creative/*-usage` for lip-sync, talking-head, manim, bg-remove, upscale, face-restore, stock, diagram, data-viz, screen-recording, scene-detect, video-understand, music-gen, typography, ink-theater, animated-drawing | Depend on filtered tools |
| `pipelines/avatar-*`, `talking-head`, `podcast-*`, `clip-factory`, `localization-*`, `screen-demo`, `explainer`, `animation`, `hybrid`, `documentary-*` | Remotion/avatar/podcast paths; not Cuti stage chain |
| Meta: skill-creator, capability-extension, animation-runtime-selector, bespoke-composition, voice-performance-director | Authoring / Remotion atelier; not needed for Cuti execution |

## Kept instruction skills

- `core/ffmpeg.md`, `core/color-grading.md` (conventions; execution via Media)
- Creative video-gen / editing / stitching / cinematic / short-form / storytelling / prompting (Seedance primary)
- Meta: intake, checkpoint, onboarding, taste, reviewer, video-reference-analyst
- Pipelines: `character-animation/*`, `cinematic/*` as **directors** over Cuti stage skills
  (`outline` → `characters` → `scenes` → `shots` → `keyframes` → `shot-videos` → assembly)
