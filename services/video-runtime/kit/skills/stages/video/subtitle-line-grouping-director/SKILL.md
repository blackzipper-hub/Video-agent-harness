---
name: subtitle-line-grouping-director
description: >-
  Group subtitle lines for display.
---

# Subtitle Line Grouping Director

Group words into subtitle lines from Human JSON facts. Timing is computed elsewhere.

## Process

1. **Read the Human JSON facts** (`words_list` / `words_text`: lines like `ID:x | Word:'y'`).
2. Group by semantics/grammar only — no external info.
3. Return `subtitle_groups[]` with `text` and `word_ids` (original order).

## Grouping rules

- Prefer sentence ends (。？！); then commas/semicolons.
- Avoid breaking after prepositions/conjunctions/articles; keep fixed phrases, proper names, verb/adj phrases.
- Target ~6–12 words per line.
- Include every word; preserve order.
