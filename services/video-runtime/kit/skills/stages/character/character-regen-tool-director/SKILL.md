---
name: character-regen-tool-director
description: >-
  Regenerate a single character image from user instruction + base prompt.
---

# Character Regen Tool Director

Merge optional user instruction into the base prompt, then call the image tool once.

## Process

1. **Read the Human JSON facts. Call the tool named in `facts.tool_name` once.**
2. Prepare final `t2i_prompt` (see Merge), then call with `reference_image_urls` from facts.
3. Retry only on failure (length / safety / params / timeout). Then `error_msg` + `raw_error_msg`.

## Merge

If `facts.user_regenerate_instruction` is non-empty:
- Read base `facts.t2i_prompt` + instruction → **one** final prompt that implements the user change.
- Conflicts (color, material, objects): **user wins**; delete/rewrite conflicting phrases.
- Keep professional, executable; preserve negative constraints if present.

Otherwise: use `facts.t2i_prompt` unchanged (already optimized).

## Language (highest priority)

Final tool `t2i_prompt` must be **100%** in `facts.detected_language`.  
Do not keep foreign lock sentences (e.g. English "facial features… preserved", "remains completely unchanged") — restate equivalently in `detected_language`.  
Allowed as-is: model/brand names, resolution/ratio tokens (4K, 16:9), pure numeric time tokens.

## Hard rules

- Must call the real tool; never fabricate image URLs.
- Refs keep identity continuity (face/details/accessories) unless user instruction overrides clothing/look.
- On retry: shorten or neutralize wording; keep core visual intent.
