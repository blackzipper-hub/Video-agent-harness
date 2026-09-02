# OpenMontage — Filtered Skill Index (Cuti)

> Full compatibility matrix: [`COMPAT.md`](COMPAT.md)

Only **video-generation-compatible** Layer-2 skills remain active. Filtered paths are under `.filtered/`.

## Architecture on Cuti

```
Layer 1 (tools):  open_montage.tool.invoke  →  Cuti Media / provider.generate
Layer 2 (skills): this directory (filtered conventions + directors)
Layer 3:          Cuti system skills (outline, shots, seedance2, media.concat, …)
```

Do **not** run upstream `tools/tool_registry.py`. Use `scripts/om_tools.py list` or
capability `open_montage.tool.invoke` with `tool=list`.

## Callable tools

| Tool | Bridge | Notes |
|------|--------|-------|
| `video_trimmer` | Media | cut from 0 → duration; speed via target duration; concat |
| `video_stitch` | `media.concat` | cut transition only |
| `frame_sampler` | `media.extract_frame` | timestamps strategy |
| `seedance_video` | `api.provider.generate` | preferred video gen |
| `video_selector` | `api.provider.generate` | Cuti picks live provider |

Everything else from upstream OpenMontage tools is filtered — see COMPAT.md.

## Core

| Skill | File |
|-------|------|
| FFmpeg conventions | `core/ffmpeg.md` |
| Color grading notes | `core/color-grading.md` |

## Creative

| Skill | File |
|-------|------|
| Video gen prompting | `creative/video-gen-prompting.md` |
| Seedance prompting | `creative/prompting/seedance-prompting.md` |
| Other model prompting (style only) | `creative/prompting/*-prompting.md` |
| Editing / stitching | `creative/video-editing.md`, `creative/video-stitching.md` |
| Form / story | `creative/short-form.md`, `creative/long-form.md`, `creative/cinematic.md`, `creative/storytelling.md` |
| B-roll / images / sound | `creative/broll-planning.md`, `creative/image-*-usage.md`, `creative/sound-design.md` |

## Meta

| Skill | File |
|-------|------|
| Intake / checkpoints / review | `meta/creative-intake.md`, `meta/checkpoint-protocol.md`, `meta/reviewer.md` |
| Taste / onboarding / reference | `meta/taste-direction.md`, `meta/onboarding.md`, `meta/video-reference-analyst.md` |

## Pipelines (directors only)

Map stages to Cuti capabilities (`outline.generate`, `characters.generate`, … `media.concat`).
Do not invoke Remotion/HyperFrames from these docs.

| Pipeline | Path |
|----------|------|
| Character animation | `pipelines/character-animation/` |
| Cinematic | `pipelines/cinematic/` |

## Filtered (examples)

Avatar, talking-head, podcast, explainer (Remotion), clip-factory, localization,
WhisperX, lip-sync, ComfyUI, stock scrapers — see COMPAT.md and `.filtered/`.
