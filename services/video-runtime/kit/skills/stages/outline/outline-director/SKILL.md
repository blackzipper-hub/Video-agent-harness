---
name: outline-director
description: >-
  Coarse story chapters + style_guide from analysis_brief (incl. proposal gates).
  Fine wall-clock belongs to Script.
---

# Outline Director

Coarse chapter buckets only. Beat truth and wall-clock → Script. Gates → `extra.proposal`.

## When To Use

Write outline via `write_outline_artifact`. Do not design scenes/shots.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Prior | `analysis_brief.json` | Need, duration, category, `extra.proposal` |
| Prior (audio) | `audio_context.json` | When `mode=audio_driven` |
| Schema | `write_outline_artifact` | Gate |

## Process

1. `read_file` analysis brief.
2. Honor `extra.proposal` (`must_keep_beats`, `quality_gates`, `emotional_arc`).
3. For story narrative (`Default` / `Short Drama`), follow this skill only — do **not** activate Explainer `/kit/skills/creative/storytelling` templates. Product Launch / Lip-Sync may use short-form craft if mounted.
4. If audio-driven, also read audio_context.
5. Plan chapters; `write_outline_artifact`; stop after `OK`.

## Mode: video_driven

- `total_duration` == sum of chapter `duration` == `target_duration_seconds`.
- Chapter count is a creative choice from the proposal spine — do **not** pad/compress to hit a recommended chapter count when the brief omits hints.
- `order` is **0-based**.
- **Uneven** durations OK; do not default to even halves (e.g. 15+15) when proposal wants a short cold-open.
- Outline is a **coarse beat bucket**. Script owns `start_seconds`/`end_seconds`.

### content_category hints

- `Short Drama` / story Default: chapters map to overall arc **hook → escalation → reveal → landing** (roles in `description`, not a fixed franchise plot). Cover every proposal `must_keep_beats` item across chapters. Name cast once; leave dialogue density for Script. `style_guide`: one concrete look sentence (light/material/contrast) — ban empty “华丽电影感”.
- `product_launch`: Hook → features → CTA.
- `default`: coherent story; avoid one static talking-head chapter for 30s pieces.

## Mode: audio_driven

- Chapters 1:1 with sections in `sections_info`.
- Fill title/description/order/cues; duration may be filled by Program later.

## enhancement_cues

Optional visual hints (`broll` \| `overlay` \| `animation` \| `hard_event`) with `timestamp_hint` and imageable description. Prefer story clarity over cue count — omit when the chapter description already carries the beat.

## Quality Gate

- Chapters non-empty; contiguous 0-based orders preferred.
- Video: duration sum == target.
- Distinct narrative function per chapter.
- Must-keep from proposal covered.

## Common Pitfalls

- Writing scene/shot lists.
- Hardcoding props from unrelated example stories.
- Even duration splits that fight a short-hook quality_gate.
