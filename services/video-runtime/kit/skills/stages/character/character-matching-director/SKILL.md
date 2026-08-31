---
name: character-matching-director
description: >-
  Match user-uploaded images to character profiles.
---

# Character Matching Director

Match uploaded reference images to designed visual elements. Read Human JSON facts; output structured matches for **every** element.

## Process

1. **Read the Human JSON facts** (`character_designs` / characters list, `reference_images` or attached media, optional `user_input`, `image_count`).
2. Analyze each image vs each design; apply criteria below.
3. Return one result row per designed element (including unmatched).

## Matching criteria

1. **Type**: person/animal/object/location aligns with design `type`.
2. **Appearance**: visible traits match description/appearance.
3. **Style** (coarse): similar depiction mode = match; obvious photo↔cartoon / 2D↔3D = mismatch. Ignore phone-vs-pro quality.
4. **Quality**: clear enough to use as reference.

## Many-to-many

- One image can match multiple elements (group photo → add index to each).
- One element can match multiple images.
- Prefer primary cast when ambiguous.
- No suitable image → `matched_image_indices=[]`, `confidence=0`.

## Decisions

- **Usable directly**: appearance highly consistent **and** `style_match=true` **and** quality OK.
- **Must regenerate**: wrong type / large appearance gap / bad quality → empty indices; style mismatch may keep indices with `style_match=false`.

## Output per element

- `character_id`, `character_name`
- `matched_image_indices`: 1-based integers (e.g. `[1,3]`), never URLs
- `match_reason`, `confidence` (0 / 1–40 / 41–70 / 71–100)
- `style_match`: boolean
