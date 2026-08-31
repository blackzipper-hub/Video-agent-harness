---
name: instruction-merge-director
description: >-
  Merge user regenerate instruction into a full prompt.
---

# Instruction Merge Director

Fuse a natural-language edit into a full generation prompt.

## Process

1. **Read the Human JSON facts**: `asset_kind`, `base_prompt` (current full prompt), `instruction`, `detected_language`.
2. Merge instruction into the prompt: keep details not contradicted; add/replace/delete per instruction. Conflicts → instruction wins.
3. Output **only** the merged text — no explanation, Markdown headers, or lists.

## Output shape by `facts.asset_kind`

- **故事章节标题** (chapter title): one short title line ≤30 chars; no description.
- **Other** (keyframe / character / story description / motion): one complete body paragraph in the instruction’s language (align with `detected_language` when set).
