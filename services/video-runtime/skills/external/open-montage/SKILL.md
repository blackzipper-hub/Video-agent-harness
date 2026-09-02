---
name: open-montage
description: >-
  Workflow: Filtered OpenMontage video-production skills for Cuti (Seedance/provider
  prompting, FFmpeg-style edits, character-animation / cinematic directors).
  Bridges OM tools via open_montage.tool.invoke. Trigger: $open-montage.
metadata:
  kind: workflow
  version: "1.0.0"
  workflow:
    title: OpenMontage conventions and bridged tools
    mode: open_montage
    planning:
      mode: staged
      checkpoints:
        - id: story_ready
          after_phase: story_intent
          next_phase: visual_production
          required_artifacts: [story_draft]
          resolves: [characters, shots]
          instruction: Continue only when the OpenMontage runtime and declared capabilities are available.
    entrypoints: [text, image, audio, video]
    parameters:
      shot_workflow_mode: open_montage
    pipeline:
      - outline.generate
      - character.generate
      - scene.generate
      - shot.generate
      - keyframe.generate
      - shot.video.generate
      - video.assemble
      - open_montage.tool.invoke
      - api.provider.generate
      - media.concat
      - media.extract_frame
    requires_keyframe: true
    allowed_capabilities:
      - actions.suggest
      - atomic.text.generate
      - atomic.image.generate
      - atomic.music.generate
      - atomic.video.generate
      - outline.generate
      - character.generate
      - character.regenerate
      - scene.generate
      - shot.generate
      - keyframe.generate
      - keyframe.regenerate
      - shot.video.generate
      - shot.video.regenerate
      - video.assemble
      - open_montage.tool.invoke
      - api.provider.generate
      - media.concat
      - media.extract_frame
---

# OpenMontage (Cuti-filtered)

Upstream: https://github.com/calesthio/OpenMontage/tree/main/skills

This is **not** the full OpenMontage runtime. See **COMPAT.md** for allowlist, bridges,
and why Remotion / WhisperX / avatar / ComfyUI / stock / TTS tools are filtered.

## How to use

0. This is a **workflow skill**. Stages may run only after the user confirms with
   `$open-montage` (or 确认 after a `workflow_confirm` pause). Inject
   `workflow_mode=open_montage` on stage tasks.
1. Read `INDEX.md` (filtered catalog) and `COMPAT.md` (tools + filter reasons).
2. Load only the section files needed for the current task.
3. Execute media via Cuti:
   - Stage chain: outline → characters → scenes → shots → keyframes → shot videos → concat
   - Direct gen: `seedance2` scripts and/or `api.provider.generate`
   - OM-shaped tools: capability **`open_montage.tool.invoke`** (skill `open-montage-tools`)
     or `run_skill_script` → `scripts/om_tools.py` with `list` / `invoke <tool>`
4. Never propose master `video.pipeline.generate`. Prefer stepwise Cuti stages.
5. Do not call filtered OM tools; explain using COMPAT.md if the user asks for them.

## Callable tools (bridged)

| tool | maps to |
|------|---------|
| `video_trimmer` | Media trim / speed / concat |
| `video_stitch` | `media.concat` (cut only) |
| `frame_sampler` | `media.extract_frame` |
| `seedance_video` / `video_selector` | `api.provider.generate` |

## Layout (kept)

| Folder | Role |
|--------|------|
| `core/` | FFmpeg + color conventions |
| `creative/` | Prompting + edit / stitch guides |
| `meta/` | Intake, checkpoints, review |
| `pipelines/character-animation/` | Directors over Cuti stages |
| `pipelines/cinematic/` | Directors over Cuti stages |
| `scripts/` | `om_tools.py` list/invoke CLI |
| `.filtered/` | Removed skills (not listed in resources) |
