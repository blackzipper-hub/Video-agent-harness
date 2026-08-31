---
name: workflow-short-drama
description: >-
  Workflow: Short Drama / dialogue-heavy Seedance path that skips keyframes
  (reference_t2v). Use for 短剧、对白戏、跳过关键帧、Seedance short drama.
  Trigger: $workflow-short-drama.
metadata:
  kind: workflow
  version: "1.0.0"
  workflow:
    title: Short Drama / dialogue-driven production
    mode: short_drama
    entrypoints: [text, image, audio, video]
    parameters:
      shot_workflow_mode: reference_t2v
      content_category: short_drama
    pipeline:
      - outline.generate
      - character.generate
      - scene.generate
      - shot.generate
      - shot.video.generate
      - video.assemble
    requires_keyframe: false
    allowed_capabilities:
      - actions.suggest
      - atomic.text.generate
      - atomic.image.generate
      - atomic.music.generate
      - atomic.video.generate
      - story.generate
      - image.generate
      - music.generate
      - outline.generate
      - character.generate
      - character.regenerate
      - scene.generate
      - shot.generate
      - shot.video.generate
      - shot.video.regenerate
      - video.assemble
      - api.provider.generate
      - api.ark_protocol.generate
      - media.concat
      - media.extract_frame
---

# Workflow: Short Drama (skip keyframes)

This Skill is a **user-selectable workflow**. Do not start stage tasks until the
user has confirmed this workflow (`$workflow-short-drama` or an explicit yes
after you proposed it).

## Engine mode (required on every stage task)

```json
{
  "workflow_mode": "short_drama",
  "shot_workflow_mode": "reference_t2v",
  "content_category": "short_drama",
  "activated_workflow": "workflow-short-drama"
}
```

## Pipeline order

1. `outline.generate`
2. `character.generate`
3. `scene.generate`
4. `shot.generate`
5. **Skip** `keyframe.generate` / first-frame reflection
6. `shot.video.generate` (keyframe input optional)
7. `video.assemble`

Prefer dense dialogue craft, place/time locks in prompts, and Seedance-oriented
shot video generation. Do not schedule keyframe stages unless the user explicitly
asks for stills.

## Rules

- Never fall back to the classic keyframe gallery for this workflow.
- If the user later wants keyframes, ask them to switch to
  `$workflow-keyframe-pipeline` instead of mixing modes mid-run without confirmation.
