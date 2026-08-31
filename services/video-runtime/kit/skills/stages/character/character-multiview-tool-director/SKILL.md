---
name: character-multiview-tool-director
description: >-
  Generate multi-view character sheets via image tools.
---

# Character Multiview Tool Director

Generate **one** multi-panel reference sheet from Human JSON facts (I2I from main image).

## Process

1. **Read the Human JSON facts. Call the tool named in `facts.tool_name` once** (single image with all panels — never multiple tool calls).
2. Build prompt in `facts.detected_language`; pass main/ref images, `aspect_ratio=16:9`, strong i2i strength (~0.8).
3. On failure: retry 1–2× then `error_msg` + `raw_error_msg`.

## Layout by `facts.element_type` (or character_type)

- **character**: 4 panels L→R — Front, Back, Side (90°), Three-Quarter (45°).
- **object**: 5 panels — Front, Back, Side, Three-Quarter, Top.
- **location**: 4 panels — Front entrance, Wide, Entrance detail, Characteristic detail.

## Consistency (critical)

Same subject across panels; only camera angle changes. Match hair/clothing/props/proportions/colors (character), material/pattern/scale (object), architecture/palette (location). Align with main image style, lighting, render quality. If `facts.story_style` is set, reflect it.

## Prompt construction

- State: character/model/turnaround sheet; **single image**; multiple views in one frame; 16:9 horizontal equal panels; subjects centered.
- Emphasize identical features across views.
- Must include **concrete style words** (anime, 3D render, Pixar, watercolor, realistic, etc.) **and** match reference — not only "match reference".
- Describe views via pose/orientation — **no** on-image labels like "front view".

## Hard rules

- **No on-screen text** unless user asked: ban text/lyrics/subtitle/watermark/logo/view labels in image; put those in negative_prompt.
- Clean solid/subtle gradient background; no clutter.
- Objects/locations: no people in frame.
- negative_prompt: quality issues + text/labels + inconsistent features + complex background; type-specific extras (multiple people / hands holding / branded items).
