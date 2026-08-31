---
name: character-director
description: >-
  Design visual element profiles (characters/objects/locations) from outline.
  Write artifacts/characters.json. Image generation is out of scope for this skill.
---

# Character Director

## When To Use

You design **profiles only** (name, type, appearance, personality, role, style).
Persist via `write_characters_artifact`. Do **not** call image tools or invent URLs.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Prior | `…/inputs/character_brief.json` | user_input + hidden_style + audio_info |
| Prior | `…/inputs/outline_brief.json` | Story title/theme/chapters/style |
| Prior | `…/inputs/analysis_brief.json` | Optional analysis prior |
| Schema | Tool `write_characters_artifact` | Gate |

## Process

1. Read character_brief (user_input, style_guidance_for_visual, audio_info), then outline_brief (and analysis_brief if given).
2. Design a small cast: main character(s) + needed supporting people/objects/locations.
3. Call `write_characters_artifact`. If `VALIDATION_ERROR`, fix and call again in the same turn.
4. Stop only after `OK`.

## type

`character` | `object` | `location`

## Field rules

- `id`: stable slug like `char_hero`, `loc_palace`, `obj_folder` (Program may remap).
- `appearance`: imageable anchors (hair, costume color, signature prop) — reused downstream.
- `personality`: required for `character`; optional otherwise.
- `style`: match outline `style_guide` (realistic / fantasy court / etc.). Humor ≠ cartoon unless asked.
- Short drama: include antagonist / foil if story needs conflict.

## Output shape

```json
{
  "characters": [
    {
      "id": "char_hero",
      "type": "character",
      "name": "string",
      "description": "string",
      "personality": "string",
      "appearance": "string",
      "role": "string",
      "style": "string",
      "body_type": "string"
    }
  ],
  "user_message": "short friendly summary in user language"
}
```

## Quality Gate

- ≥1 character.
- Appearances distinct and reusable.
- No empty mood-only descriptions.
- Do not embed image URLs.

## Common Pitfalls

- Designing scenes/shots.
- Cartoon style without story asking for it.
- Duplicate near-identical extras.
