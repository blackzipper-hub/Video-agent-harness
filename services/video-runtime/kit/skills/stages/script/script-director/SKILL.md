---
name: script-director
description: >-
  Cinematic script: beat map, dialogue/title cards, metadata, reveal/landing.
---

# Script Director - Cinematic Pipeline

## When To Use

This stage builds the beat map, dialogue/title-card copy, and reveal structure. Shape rhythm and shootable lines — not a dense YouTube explainer essay.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `/kit/schemas/artifacts/script.schema.json` | Artifact validation |
| Prior artifact | `analysis_brief` / `extra.proposal` | Emotional arc and selected concept spine |
| Tools | `transcriber`, `scene_detect` | Optional dialogue mining and source review |

## Process

### 1. Build A Beat Map First

Use a simple structure:

- hook,
- escalation,
- reveal,
- landing.

If the piece is longer, add one midpoint turn. Do not let it become essay-shaped.

### 2. Dialogue Rules (by content_category)

**Story narrative (`Default` / `Short Drama`):** write **dense, shootable dialogue** that covers every `selected_concept` key_point / visual_approach beat. Do **not** thin into title-led or narration-led. Punchy lines OK — no hard per-line character cap. Prefer `角色（情绪）：「…」` when the brief has character speech / comedy attitude.

**Product Launch / Lip-Sync MV / title-card trailers:** use dialogue sparingly (or lipsync lines from source). If no useful speech, keep title-led or narration-led and say so in metadata.

### 3. Keep Title Cards Short

When using title cards, keep them trailer-like:

- fewer words,
- more contrast,
- more whitespace,
- more timing precision.

### 4. Store Beat Truth In Metadata

Recommended metadata keys:

- `beat_map`
- `dialogue_selects`
- `title_card_copy`
- `music_turns`
- `silence_windows`

### 5. Quality Gate

- the beat map escalates cleanly,
- dialogue and title cards do not explain the same thing twice,
- the reveal lands distinctly,
- the landing gives the viewer a final feeling or action,
- Story narrative: every must-keep / selected key_point appears in shootable text.

### Mid-Production Fact Verification

If you encounter uncertainty during script writing:
- Use `web_search` to verify factual claims before committing them to the script
- Use `web_search` to find reference images for visual accuracy
- Log verification in the decision log: `category="visual_accuracy_check"`

Every factual claim in the script should be traceable to the `research_brief`.
If you make a claim that isn't in the research, do additional research and
add the source. Do not invent statistics, dates, or attributions.

## Common Pitfalls

- Writing full explanatory paragraphs instead of beats.
- Using too many title cards on story narrative (prefer dialogue + action).
- Revealing the best moment too early.
- Softening comedy payoffs from `selected_concept` (e.g. “拿捏甲方” → meek compliance).
- Activating Explainer storytelling templates for story narrative.

---

## Cuti Script Persistence (binding)

After following the beat-map / dialogue / metadata process above:

1. Read `outline_brief.json` + `analysis_brief.json` (`read_file` limit=2000).
2. **Lock `selected_concept` first** (top-level on analysis_brief, or `extra.proposal.selected_concept`):
   - Treat `hook` / `key_points` / `visual_approach` as non-negotiable story truth
   - Also cover every `must_keep_beats` / honor `quality_gates` + `emotional_arc`
   - Do **not** soften comedy payoffs
3. Craft in this skill (+ short-form only if already mounted). Do **not** force Explainer `/kit/skills/creative/storytelling` for story narrative.
4. Own wall-clock: sections `start_seconds`/`end_seconds` cover `0 … total_duration_seconds` (gaps ≤0.5s).
5. Section `beat_role` ∈ `hook|escalation|reveal|landing|midpoint|narration|dialogue`.
6. Shootable `text` with `【画面】`/`【动作】` when needed; dialogue `角色（情绪）：「…」` (punchy; no ≤12-汉字 hard cap).
7. Every section: `delivery_cues` + non-empty `source_ref`.
8. Persist with `write_script_artifact`. Fix `VALIDATION_ERROR` in the same turn.
