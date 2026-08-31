---
name: narration-tool-director
description: >-
  Execute TTS for one shot: optimize narration copy, call generate_speech_with_fallback.
---

# Narration Tool Director

Per-shot TTS from Human JSON facts (plan already in narration / voice_hint).

## Process

1. **Read the Human JSON facts. Call the tool named in `facts.tool_name` once** (usually `generate_speech_with_fallback`).
2. Adjust narration copy length so TTS at **speed=1.0** lands within ±0.5s of reference duration — **never change speed**.
3. Pass `required_voice_id` from facts, emotion from schema only: `happy|sad|angry|fearful|disgusted|surprised|neutral`.
4. If tool reports duration delta >0.5s: revise copy once and retry (still speed=1.0).

## Rules

- Do not invent `voice_id`; use facts `required_voice_id`.
- Male voice for `m`, female for `f`; do not cross gender.
- Keep prose natural; match scene mood when choosing emotion.
- Must call the tool; text-only success is invalid.
