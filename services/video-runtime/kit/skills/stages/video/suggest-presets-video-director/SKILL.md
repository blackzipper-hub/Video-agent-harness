---
name: suggest-presets-video-director
description: >-
  Suggest edit-instruction chips for video i2v artifacts.
---

# Suggest Presets Video

Suggest quick edit chips for a shot’s motion / I2V prompt.

## Process

1. **Read the Human JSON facts** (`prompt_excerpt` / prompt, optional keyframe + clip media notes).
2. Produce ~6 presets (max 8): `label` (≤10 chars), `emoji`, `instruction` (one executable edit for the merge model).
3. Focus: motion, rhythm, camera, subject action, lighting/grade, first-frame consistency. Do not restate the whole prompt.
4. Structured schema output only.
