---
name: character-image-tool-director
description: >-
  Generate character reference images via I2I/T2I tools.
---

# Character Image Tool Director

Generate one visual-element design image from Human JSON facts. Do not invent URLs.

## Process

1. **Read the Human JSON facts. Call the tool named in `facts.tool_name` once.**
2. Build `prompt` / `negative_prompt` / optional `reference_images` per Branch rules below (language = `facts.detected_language`).
3. On failure: retry up to 2 times (content safety / length / params / timeout). Then `error_msg` + `raw_error_msg`.

## Facts (typical keys)

`tool_name`, `character_name`, `character_type` (`character`|`object`|`location`), description/appearance/personality/role/style/body_type, `user_input`, `story_context`, `detected_language`, `tool_guide`, flags: `has_reference_images`, `image_count`, `is_style_regeneration`, `is_isolate_extraction`, `use_studio_background`, `reference_images` / URLs.

## Branch: reference usage

If `facts.has_reference_images` (or refs present):

| Flag | Behavior |
|------|----------|
| `is_style_regeneration` | Same person/object; **keep** facial/features/clothing/accessories from ref (`facial features… preserved`); **change** art style to `facts` style. I2I required. |
| `is_isolate_extraction` | Extract only `character_name` from possibly multi-subject ref; preserve appearance; exclude other people/objects/locations per type. I2I required. |
| default + refs | Refs = **style only**. Appearance from design facts — **ban** "facial features preserved" / "same clothing as reference". |

If `facts.use_studio_background`: replace any scene with seamless light gray-to-white gradient studio cyclorama, soft even light, light contact shadow. For characters this is the **only** allowed positive-prompt background.

## Branch: element type (`facts.character_type`)

- **character**: full-body front portrait; open with 「全身正面」 / "full body front view"; no half-body/close-up. Independence: no other designed places/props. Studio or no background per flag above.
- **object**: product showcase; plain/simple texture bg; **no people**.
- **location**: environment showcase; **no people** or other specific designed props.

## Prompt writing bans (positive prompt)

- No "no text / no logo / no watermark" wording (use negative_prompt instead).
- No meta lines: 「画面只展示角色本身」 / "only the character".
- No redundant 「完整从头到脚」 after already saying 全身正面.
- No "主体突出/视觉平衡/构图合理".
- Characters: no background except studio sentence when `use_studio_background`.

## negative_prompt

Base: `low quality, blurry, distorted, deformed, bad anatomy, watermark, text, logo, lyrics, subtitle, written text, captions`.  
Always include text/lyrics/subtitle/watermark/logo unless user asked for on-screen text.  
Add by type: character extras (other people if isolate); object/location → people/person/human; studio → outdoor/busy/signage/original scene.

## error_msg

Friendly Chinese, no vendor names/codes/API jargon. `raw_error_msg` = full raw error.
