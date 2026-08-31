---
name: music-lyrics-director
description: >-
  Package Suno lyrics + prompt for vocal tracks.
---

# Music Lyrics Director

Call Suno music generation from Human JSON facts. Never fabricate clip URLs.

## Process

1. **Read the Human JSON facts. Call the tool named in `facts.tool_name` once** (expect `generate_music_with_suno`).
2. Prepare `prompt`, `has_lyrics`, `target_duration`, `tags`, optional `vocal_gender` per rules below.
3. On `success=true`: accept result even if duration ≠ target — **do not** recall for duration alone.
4. On `success=false`: adjust and call **at most one** more time.

## Language

If `facts.detected_language` is set: prompt/lyrics body must be 100% that language (unless user_input explicitly requests another singing language).  
Keep structure tags (`[Intro]`…), genre tokens (jazz/pop/…), duration numbers as-is.

## Modes

### Instrumental (`facts.needs_lyrics` / `has_lyrics` false)
- `prompt` = style/mood/tempo description in the user’s language; `has_lyrics=False`.

### Song with lyrics (`needs_lyrics` true)
- If user already supplied lyrics: use them (same language); structure with `[Intro]/[Verse]/[Chorus]/[Bridge]/[Outro]`.
- If `facts.design_lyrics` (or no lyrics provided): **author** singable lyrics sized for `target_duration` — control length via word count/structure, **not** by writing “intro 10s” in the prompt. Do **not** use auto_lyrics.
- Lyrics length drives duration (shorter → shorter song).

## tags (duration lever — stronger than word count)

- Under 30s: required `Ultra short` / `Bumper` / `Jingle`
- ~30–90s: `Short version` (only ≤45s consider `Jingle`)
- Over 100s: **leave tags empty** (short tags crush long songs)
- Do not stack multiple short tags; avoid Stinger/Sting (may force instrumental)

## vocal_gender

Songs only: `'f'` / `'m'` when a single clear gender is inferable from user_input or reference images; omit if unclear/multi/BGM. Age/timbre hints go in short English tags if needed.

## Retry (success=false only)

- 400/413/500 / unsupported expression → simpler everyday wording **in the same language** (never zh→en to dodge errors).
- Tag conflicts → fix tags.
- Timeout → simplify prompt; keep language.

## Core principles

Consistency of instrumental vs lyrics mode across retries; accept probabilistic duration; max one retry after first failure.
