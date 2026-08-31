---
name: suggest-presets-character-director
description: >-
  Suggest edit-instruction chips for character t2i artifacts.
---

# Suggest Presets Character

Suggest quick edit chips for a character main-image t2i prompt.

## Process

1. **Read the Human JSON facts** (`prompt_excerpt` / prompt, optional character/main image).
2. Produce ~6 presets (max 8): `label` (≤10 chars), `emoji`, `instruction` (one executable edit for merge).
3. Focus: light, grade, framing, subject detail, costume/accessories, mood. No full prompt restatement.
4. Structured schema output only. If no image, infer from prompt alone.
