---
name: keyframe-director
description: >-
  Write T2I prompts for keyframes (prompt side only). No image tools.
---
# Keyframe Director

Read `keyframe_brief`. For each expected shot write a concrete `t2i_prompt`.
Call `write_keyframe_artifact`. Do not generate images.

## Facts in brief

- Shot fields: camera_*, subject_*, lighting, visual_effects, transition, dialogue, SFX, narration, duration, scene_description
- `elements_with_images[]`: `name`, `type`, `description`, `appearance`, `image_number`, `url`, optional `is_sheet`, `sheet_element_names`
- `locations_text_only[]`: venue/location facts with no attached image — weave into the scene in prose (do not invent a ref image)
- `style_guide` — weave **one short phrase**; never paste the whole text
- `is_lip_sync_mv`, `prev_shot` / `next_shot`, `generate_last_frame`, `first_frame_prompts_context` (JSON map of shot→first-frame prompt when generating last frames)
- `max_reference_images` — do not cite more images than available

## Image citation

- Attached media follows `reference_image_urls` order.
- Cite **image N** using that shot’s `elements_with_images[].image_number` (local to the shot).
- Cite every image that has a number; do not omit refs that are present.

## Reference sheets (`is_sheet: true`)

- The image is a grid of characters/objects named in `sheet_element_names`.
- Extract appearance from each cell; **do not** replicate the grid layout in the prompt.
- Compose one single cinematic scene that includes those elements.

## Continuity

- Use `prev_shot` / `next_shot` for spatial/action continuity only.
- When `generate_last_frame` and `first_frame_prompts_context` has this shot: match the first-frame description style while advancing the beat.

## Lip-sync / product templates

When `is_lip_sync_mv`: favor clear face / mic / performance-ready framing as appropriate to the shot — no on-screen lyrics text.

## Output

`frame_index` default 0 (or last-frame convention from brief flags). One `t2i_prompt` per expected `shot_number`.
