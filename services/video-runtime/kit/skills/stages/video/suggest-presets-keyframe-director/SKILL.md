---
name: suggest-presets-keyframe-director
description: >-
  Suggest edit-instruction chips for keyframe t2i artifacts.
---

# Suggest Presets Keyframe

Suggest quick edit chips for a keyframe t2i prompt.

## Process

1. **Read the Human JSON facts** (`prompt_excerpt` / prompt, optional keyframe image).
2. Produce ~6 presets (max 8): `label` (≤10 chars), `emoji`, `instruction` (one executable edit).
3. Focus: light, grade, composition, shot size, subject–background, mood. No full prompt restatement.
4. Structured schema output only. If no image, infer from prompt alone.
