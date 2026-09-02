---
name: workflow-direct-video
description: >-
  Workflow: one-shot / few-clip direct video without the full stage chain.
  Use for 直接出片、单段视频、atomic video、video_gen.
  Trigger: $workflow-direct-video.
metadata:
  kind: workflow
  version: "1.0.0"
  workflow:
    title: Direct single-clip video
    mode: direct_video
    planning:
      mode: staged
      checkpoints:
        - id: creative_ready
          after_phase: creative_intent
          next_phase: visual_production
          required_artifacts: [project_intent]
          resolves: [shots, references]
          instruction: Complete only the direct-video fields that depend on the supplied reference material.
    entrypoints: [text, image, audio, video]
    parameters:
      shot_workflow_mode: direct
    pipeline:
      - atomic.video.generate
      - video_gen.generate
      - video.generate
    requires_keyframe: false
    allowed_capabilities:
      - actions.suggest
      - atomic.text.generate
      - atomic.image.generate
      - atomic.video.generate
      - video_gen.generate
      - video.generate
      - api.provider.generate
      - api.ark_protocol.generate
      - media.concat
      - media.extract_frame
---

# Workflow: Direct Video

This Skill is a **user-selectable workflow**. Do not start generation until the
user has confirmed this workflow (`$workflow-direct-video` or an explicit yes
after you proposed it).

## Engine mode

```json
{
  "workflow_mode": "direct_video",
  "shot_workflow_mode": "direct",
  "activated_workflow": "workflow-direct-video"
}
```

## Pipeline / capabilities

Prefer one of:

- `atomic.video.generate` (platform direct video_gen)
- `video_gen.generate` when that agent is specifically appropriate
- `api.provider.generate` / Seedance bridge for provider clips

Optional: `atomic.text.generate` / `atomic.image.generate` for prompts or refs,
then `media.concat` if multiple clips must be joined.

## Rules

- Do **not** enter outline→character→scene→keyframe unless the user switches workflow.
- Master `video.pipeline.generate` remains paused.
