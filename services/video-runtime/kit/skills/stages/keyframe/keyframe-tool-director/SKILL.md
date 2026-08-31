---
name: keyframe-tool-director
description: >-
  Call the T2I/I2I keyframe tool with the given prompt and refs.
---

# Keyframe Tool Director

Execute one keyframe image tool call from Human JSON facts. Do not invent image URLs.

## Process

1. **Read the Human JSON facts. Call the tool named in `facts.tool_name` once.**
2. Build args from facts (see Modes). First action must be the tool call.
3. On `success=true`: stop.
4. On `success=false`: adjust 1–2 times (Retry), then fill `error_msg` + `raw_error_msg` if still failing.

## Modes (branch on facts)

### Regenerate merge
If `facts.user_regenerate_instruction` is non-empty:
- Merge base `facts.t2i_prompt` with the user instruction into **one** final prompt string.
- Conflicts (color, material, lighting, costume): **user wins**.
- Outfit / color / style changes: remove or rewrite ref-lock English that contradicts the user (e.g. "facial features, facial details, and clothing preserved", "remains completely unchanged" tied to clothing). Keep minimal facial-identity continuity; **never** keep both “clothing identical to ref” and “change skirt to black”.
- If user did **not** ask to change clothing, keep existing identity/ref consistency wording when no conflict.

### Normal generate
Otherwise: use `facts.t2i_prompt` and `facts.reference_image_urls` as given (mode `facts.mode`: `i2i` | `t2i`).

## Hard rules

- You cannot generate images yourself — only via the named tool.
- Never fabricate `{"success":true,"image_url":"..."}`.
- Priority: `user_input` > `t2i_prompt` > `reference_image_urls` > shot fields.
- Shot fields (shot_number, is_bridge, shot_type, camera_*, scene_description, camera_movement, lighting, visual_effects, dialogue, narration, sound_effects, transition) inform context only.
- **Reference sheet**: if a ref is a character sheet/grid, extract appearance from cells; pass the sheet URL as a normal reference; do **not** invent a multi-panel output prompt.
- **No on-screen text** on retry unless user asked: no text/lyrics/subtitle wording in prompt; lip-facing shots = mouth/performance only.
- Keep length within `facts.prompt_length_range` when trimming.

## Retry (only on failure)

- Content safety → neutralize wording.
- Too long → trim to `facts.prompt_length_range`.
- Bad URL/params → fix `reference_image_urls`.
- Timeout / unavailable → simplify prompt; fewer refs if needed.

## error_msg / raw_error_msg

Same contract as video-tool-director: friendly Chinese `error_msg` (no vendors/codes); full `raw_error_msg` for backend.
