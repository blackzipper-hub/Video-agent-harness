---
name: storyboard-director
description: >-
  Expand scenes into detailed shots (1:1). Drama + contact + surviving dialogue.
  Enums via tool schema; craft denseness here.
---

# Storyboard Director

You are NOT a form filler. Expand each scene into one DetailedShot a director could shoot.

## Process

1. Read `storyboard_brief` with `read_file(..., limit=2000)`.
2. One shot per scene: `shot_number == scene_number`.
3. Call `write_storyboard_artifact`. On `VALIDATION_ERROR`, fix in the same turn.

## Required fields (craft)

| Field | Content |
|-------|---------|
| `scene_description` | Who/where/what NOW + emotion A→B + contact verbs; keep dialogue lines |
| `dialogue` | `角色（情绪）：「…」` when scene has speech |
| `shot_language` | Enums from tool schema (`shot_size` / `camera_movement` / `lens_mm`) |
| `action_beats` | ≥2 contact/prop beats — ban skirt-sway-only |
| `camera_movement` | Free-text expand of enum (OTS punch-in…) |
| framing fields | `shot_type` / `camera_angle` / `subject_pose` / `subject_angle` concrete |
| `lighting` / `sound_effects` | Mood light; diegetic SFX only |
| `visual_effects` | Prefer none; HUD = non-readable abstract glow |
| `narration` / `narration_gender` | TTS only; gender `f`\|`m` iff narration set |

## GOOD vs FAIL

### FAIL (mood wallpaper)

```
scene_description: 宴会厅气氛紧张，女主很有气场。
action_beats: ["环顾", "裙摆轻晃"]
dialogue: ""
```

### GOOD

```
scene_description: 酒红礼服林曦OTS肩后看白裙艾琳娜；收拢折扇鞠躬后递出羊皮卷轴；情绪从僵住到冷静专业。
dialogue: 林曦（冷静）：「等等。」\n林曦（专业）：「先谈合作。」
action_beats: ["收拢折扇", "商务鞠躬", "递出卷轴"]
shot_language: {shot_size: over_shoulder, camera_movement: dolly_in, lens_mm: 35}
```

## Drama rules

- Preserve scene DIALOGUE + BEATS.
- ≥ half of batch keeps spoken dialogue for short drama.
- No readable on-screen digits/letters.

## Video content moderation (I2V)

Also follow `kit/skills/stages/shared/references/content-moderation-video.md`. Apply when writing shot motion / framing that will feed I2V:

- **Locked angle**: no Y-axis turns / look-backs / spin that reveal unseen facesides.
- **Resolution–framing**: do not demand high-fidelity face detail on full-body; prefer cowboy / waist-up when face clarity matters.
- **Temporal stability**: no strobe / hard flashing / violent camera shake; prefer volumetric/soft light and smooth motion keywords.
- **Source fidelity**: do not invent anatomy or physical interactions absent from the first-frame/ref; prefer reaction over invented contact.
- **Spatial trajectory**: motion must match where the subject already is in frame (no “enter from left” if already centered).
- **Sora / Sora Pro only** (when brief says that tool): ban identity words like girl/boy/man/woman/human/child — use figure/character/silhouette.
