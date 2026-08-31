---
name: content-category-director
description: >-
  Early-infer content_category from user input.
---

# Content Category Director

Infer `content_category` before music generation from Human JSON facts.

## Process

1. **Read the Human JSON facts** (`user_input`, optional `panel_content_category`).
2. Choose exactly one category string below.
3. Output structured `content_category` only.

## Categories (exact strings)

- `"Default"` — story MV: plot, multi-shot narrative; not product talk, not lip-sync MV, not short-drama dialogue.
- `"Lip-Sync MV"` — lip-sync / singing / mic / multi-scene follow-along MV.
- `"Product Launch"` — product/App demo talking-head, features, CTA; TTS-driven; usually no BGM lip-sync.
- `"Short Drama"` — multi-character dialogue drama, in-picture lip lines; not product pitch, not singing MV.

## Rules

1. User copy beats panel card; if they conflict, follow `user_input`.
2. Explicit refusals (“不要产品发布/对嘴/短剧…”) ban those categories.
3. Infer from content (user need not name the template):
   - product/demo/CTA/to-camera explain → Product Launch
   - lip-sync / sing / MV mic → Lip-Sync MV
   - short drama / dialogue / 霸总甜宠对白 → Short Drama
   - otherwise story narrative → Default
4. Conservative: no clear sing intent → not Lip-Sync MV; mood-only brand film → not Product Launch; mood story without dialogue drama → not Short Drama.
