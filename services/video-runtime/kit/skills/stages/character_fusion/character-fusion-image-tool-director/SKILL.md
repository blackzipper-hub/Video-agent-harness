---
name: character-fusion-image-tool-director
description: >-
  Generate fused character+scene images via I2I tools.
---

# Character Fusion Image Tool Director

Fuse multiple character reference images into one combination sheet. Do not invent URLs.

## Process

1. **Read the Human JSON facts. Call the tool named in `facts.tool_name` once.**
2. Build English (or facts language) fusion prompt; pass **all** reference images (I2I). Use `facts.tool_guide` for extra params.
3. On failure: retry up to 2×; then `error_msg` + `raw_error_msg`.

## Facts (typical)

`tool_name`, character list (`character_list` / names), `image_type_name` (e.g. main / multiview), `image_count`, `reference_images`, optional `story_context`, `character_count`.

## Fusion requirements

- Preserve each character’s face, details, accessories, clothing (may require "facial features, facial details, accessories, and clothing preserved").
- Arrange all characters reasonably in one frame (side-by-side / staggered); no heavy overlap; style-consistent.
- Clean simple background; usable as later keyframe reference.
- If `facts.story_context` present, use for overall palette/style.

## Hard rules

- **No on-screen text** unless user asked — ban text/lyrics/subtitle/watermark/logo in prompt implication; put in negative_prompt.
- Priority: reference images > character info > story_context.
- negative_prompt base: quality issues + text/logo/lyrics/subtitle + overlapping characters, unclear features.

## error_msg

Friendly Chinese, no vendors/codes; `raw_error_msg` = full raw error.
