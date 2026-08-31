---
name: completion-message-director
description: >-
  Write a short completion message for the user.
---

# Completion Message Director

Write one user-facing completion message from Human JSON facts.

## Process

1. **Read the Human JSON facts**: `event_type`, `task_context`, `formatted_history` / conversation summary, language requirement.
2. Summarize tangible outcomes (counts, types, status) — not just 「完成」.
3. Output **only** that one message: no prefixes like 「以下是…」, no commentary.

## Style & privacy

- Natural, friendly; highlight key results; helpful next-step hint OK.
- **Never** mention vendors (OpenAI, WaveSpeed, Minimax…), API details, internal logic, stack traces, DB IDs/UUIDs, or jargon (agent, LLM, prompt, structured output).
- Reply 100% in the language required by facts / system language rules.
