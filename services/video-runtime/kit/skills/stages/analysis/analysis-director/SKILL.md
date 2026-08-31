---
name: analysis-director
description: >-
  Orchestrate analysis core fields plus full cinematic research-director and
  proposal-director into one write_analysis_artifact call.
---

# Analysis Director

## When To Use

Produce `artifacts/analysis.json` via `write_analysis_artifact`.

Video-driven runs must activate and follow (in order):

1. `/kit/skills/stages/analysis/research-director` — gather only
2. `/kit/skills/stages/analysis/proposal-director` — lock direction + gates (thick `selected_concept`; no approval wait)

For **story narrative** (`Default` / `Short Drama`), stay on research + proposal directors — do **not** pull Explainer storytelling arc templates. `Product Launch` / `Lip-Sync MV` may use `/kit/skills/creative/cinematic` or `short-form` when useful.

Then persist **once** with analysis core + `research` + `proposal`.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Prior | `…/inputs/user_brief.json` | User text, duration, category |
| Prior (audio) | `…/inputs/audio_context.json` | Prefer audio duration; research/proposal optional |
| Schema | `write_analysis_artifact` | Gate |
| Search | Provider web search | Required video-driven |

## Process

1. `read_file` user brief (+ audio_context if present), limit=2000.
2. Fill analysis core fields (below).
3. **Video-driven:** run research-director process (web search batches), then proposal-director process (auto-select; no human wait).
4. `write_analysis_artifact` with analysis fields + `research` + `proposal`.
5. Stop after `OK`. Do not write outline/script.

## content_category

Only: `"Default"` | `"Lip-Sync MV"` | `"Product Launch"` | `"Short Drama"`.

短剧 / short drama / 穿越 + 角色对白 → `"Short Drama"`.

## Analysis core fields

- `main_character`: imageable look + role.
- `duration`: seconds; audio wins when present.
- `key_elements`: 3–7 concrete beats from **this** brief.
- `style_preferences`: short concrete look tags (ban empty “cinematic”).
  **Language (hard):** write every tag in the run’s `detected_language` / human instruction language
  (e.g. `zh` → `高反差冷暖光` / `手持动作运镜` — **not** English like `High contrast visual tone`).
  Mixed brief (中文需求 + English title / “海外”) still follows **user language**, not the overseas market language.
- `next_action`: usually `"outline"`.
- `video_type` / `purpose` / `target_audience`: required strings — same language rule as `style_preferences`
  (user-facing; FE StyleSection shows `style_preferences` preferentially).

## Quality Gate

- Required analysis strings; `duration` > 0; `content_category` enum.
- Video-driven: `research` + `proposal` both present; proposal has `must_keep_beats` + `quality_gates` + `emotional_arc`.
- Research followed cinematic batches (not explainer stats dump for story narrative).

## Common Pitfalls

- Skipping research-director / proposal-director and only filling thin fields.
- Waiting for human approval (not available — auto-select and write).
- Inventing must_keep beats not in the user brief.
- English `style_preferences` when `detected_language=zh` (often triggered by “海外” / English script titles in the brief).
