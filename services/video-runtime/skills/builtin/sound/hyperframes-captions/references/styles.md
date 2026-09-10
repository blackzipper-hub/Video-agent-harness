# HyperFrames caption styles

| Style | Use |
|---|---|
| `caption-highlight` | Clean social captions with an accent treatment |
| `caption-pill-karaoke` | Pill-shaped captions for music and lyrics |
| `caption-editorial-emphasis` | Documentary and cinematic dialogue |
| `caption-glitch-rgb` | Gaming, cyber, technical content |
| `caption-kinetic-slam` | Hype and announcement moments |
| `caption-neon-glow` | Night and neon visuals |
| `caption-neon-accent` | Playful multi-color content |
| `caption-clip-wipe` | Restrained modern reveal |
| `caption-gradient-fill` | Vibrant promotional content |
| `caption-matrix-decode` | Sci-fi and code reveals |
| `caption-emoji-pop` | Casual social content |
| `caption-parallax-layers` | Cinematic depth |
| `caption-particle-burst` | Celebration and impact words |
| `caption-texture` | Bold dramatic typography |
| `caption-weight-shift` | Elegant typographic motion |

## Content-aware selection

Choose from evidence in this order: explicit user direction, plot/script and scene
function, visible video language, dialogue pacing, then publishing context. Always pass
the chosen style explicitly.

All styles are sentence-level visual treatments. They must not introduce per-word or
per-character highlighting; each complete cue fades in, remains stable, and fades out.

- Use `caption-editorial-emphasis` for cinematic dialogue, drama, documentary, mystery,
  or narrative scenes where subtitles should support rather than dominate the image.
- Use `caption-highlight` for general social speech, explainers, interviews, and other
  content where word-by-word legibility is the main goal.
- Use `caption-pill-karaoke` for songs, lyrics, rhythmic recitation, or deliberate
  karaoke treatment.
- Use `caption-glitch-rgb` or `caption-matrix-decode` for cyberpunk, hacking, gaming, or
  technological disruption; prefer `matrix-decode` for code/reveal moments and
  `glitch-rgb` for unstable/high-energy scenes.
- Use `caption-neon-glow` or `caption-neon-accent` for neon/nightlife visuals; prefer
  `neon-glow` for atmospheric work and `neon-accent` for playful multi-color work.
- Use `caption-kinetic-slam`, `caption-particle-burst`, or `caption-texture` selectively
  for trailers, announcements, celebrations, impact words, or dramatic peaks. Avoid
  applying these aggressive treatments uniformly to quiet dialogue.
- Use `caption-clip-wipe`, `caption-gradient-fill`, or `caption-weight-shift` for modern
  product, fashion, brand, or polished promotional work; match intensity to pacing.
- Use `caption-parallax-layers` for cinematic depth when the frame has enough negative
  space, and `caption-emoji-pop` only for deliberately casual or comedic social content.

When several styles fit, choose the least visually intrusive one that still matches the
story. Evaluate the dominant purpose of the complete video, not an isolated keyword. If
the story contains sharply different movements, select one coherent base style rather
than changing style scene by scene unless the user explicitly requests mixed styling.

Use `bottom-safe` for normal dialogue, `lower-middle` for social video when the lower UI
safe area is crowded, and `center` only for title-like or explicitly requested captions.
Use a six-digit hex `accent_color`; preserve adequate contrast against the source video.
