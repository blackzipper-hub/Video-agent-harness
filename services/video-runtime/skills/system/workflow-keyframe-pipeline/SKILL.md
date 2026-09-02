---
name: workflow-keyframe-pipeline
description: >-
  Workflow: classic directed video production with keyframes.
  Use for story→outline→characters→scenes→shots→keyframes→shot videos→assemble.
  Trigger: 分镜成片、关键帧、完整制片、keyframe pipeline、$workflow-keyframe-pipeline.
metadata:
  kind: workflow
  version: "1.0.0"
  workflow:
    title: Keyframe-centric directed production
    mode: keyframe_pipeline
    planning:
      mode: staged
      checkpoints:
        - id: story_ready
          after_phase: story_intent
          next_phase: reference_production
          required_artifacts: [story_draft]
          resolves: [characters, shots, audio]
          instruction: Complete the production VideoSpec from the generated story draft before references and keyframes.
        - id: references_ready
          after_phase: reference_production
          next_phase: keyframe_production
          required_artifacts: [character_reference]
          resolves: [keyframe_prompts]
          instruction: Inspect the real character references and refine only keyframe composition prompts without changing locked identities.
        - id: keyframes_ready
          after_phase: keyframe_production
          next_phase: video_production
          required_artifacts: [keyframe]
          resolves: [video_motion, transitions, timeline]
          instruction: Plan video motion and transitions from the generated keyframes without replacing completed references or keyframes.
    entrypoints: [text, image, audio, video]
    parameters:
      shot_workflow_mode: keyframe_i2v
    pipeline:
      - outline.generate
      - character.generate
      - scene.generate
      - shot.generate
      - keyframe.generate
      - shot.video.generate
      - video.assemble
    requires_keyframe: true
    allowed_capabilities:
      - actions.suggest
      - atomic.text.generate
      - atomic.image.generate
      - atomic.music.generate
      - story.generate
      - image.generate
      - music.generate
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
      - api.provider.generate
      - media.concat
      - media.extract_frame
---

# Workflow: Keyframe Pipeline

This Skill is a **user-selectable workflow**. Do not start stage tasks until the
user has confirmed this workflow (`$workflow-keyframe-pipeline` or an explicit yes
after you proposed it).

## Engine mode (required on every stage task)

```json
{
  "workflow_mode": "keyframe_pipeline",
  "shot_workflow_mode": "keyframe_i2v",
  "activated_workflow": "workflow-keyframe-pipeline"
}
```

Host injects these when missing; still include them in `propose_plan_patch` parameters.

## Pipeline order

1. `outline.generate`
2. `character.generate`
3. `scene.generate`
4. `shot.generate`
5. `keyframe.generate`  ← required before video
6. `shot.video.generate`
7. `video.assemble`

After each stage, inspect real artifacts before planning the next. Parallelize only
when upstream artifacts already exist.

## Rules

- Master `video.pipeline.generate` stays paused — never propose it.
- Pause for user approval when characters/keyframes need confirmation before expensive video.
- Revisions: `character.regenerate` / `keyframe.regenerate` / `shot.video.regenerate`.
