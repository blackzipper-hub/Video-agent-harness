---
name: gemini-video-analysis-director
description: >-
  Analyze uploaded user video with Gemini multimodal.
---

# Gemini Video Analysis Director

Structured analysis of an uploaded video from Human JSON facts + media attachment.

## Process

1. **Read the Human JSON facts** (optional `user_input`) and the attached video.
2. Analyze in order: overall style → characters → storyboard → production techniques → visual elements.
3. Output the structured fields below. Prefer `user_input` when it sets analysis focus.

## Output fields

- `overall_style`, `theme`, `mood`, `color_palette` (strings)
- `characters[]`: `name`, `description`, `role`, optional `appearance_time`
- `storyboard[]`: `time_start`, `time_end` (MM:SS), `action_description`, `production_method`, optional `visual_style`, `camera_angle`, `emotion`
- `production_techniques[]`, `visual_elements[]` (string arrays)
- `narrative_structure` (string)

## Notes

- Timestamps MM:SS; be detailed enough to guide later creation.
- Do not invent footage not present; ground claims in visible/audible content.
