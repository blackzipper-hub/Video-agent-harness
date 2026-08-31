---
name: audio-effect-tool-director
description: >-
  Generate SFX for shots via audio tools.
---

# Audio Effect Tool Director

Generate shot SFX from Human JSON facts.

## Process

1. **Read the Human JSON facts. Call the tool named in `facts.tool_name` once** (expect `generate_audio_with_wavespeed`).
2. Derive SFX description from motion/i2v prompt, scene action, and mood in facts.
3. Match SFX duration to `facts.duration` (video length). Prefer diegetic, non-jarring effects.

## Facts (typical)

`tool_name`, `shot_number`, `is_bridge` / bridge indicator, `shot_type`, `duration`, `video_url`, `motion_prompt` / `prompt`, optional scene fields.

## Quality

- Align with on-screen action and rhythm.
- Clear, natural; fits overall video style.
- Do not invent audio URLs — only tool results.
