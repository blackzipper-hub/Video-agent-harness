# hyperframes-captions: English Reading Copy

Reference copy for developers. **Documentation only; not a runtime Skill or installable bundle.**

Source: [original SKILL.md](../../../skills/builtin/sound/hyperframes-captions/SKILL.md). Source SHA-256: `b6374b8a60174a698913b2401d32afd9b231b4080dde031a317547e9f55986d4`.

See [reading conventions](../README.md#reading-conventions). The runtime Skill remains authoritative.

## Runtime behavior

HyperFrames captions use the same deterministic flow as Cuti DeepAgent:

1. Transcribe the complete selected video and persist sentence or segment timestamps.
2. Let the Agent choose `style`, `position`, and `accent_color` from the user's request
   and the video's narrative and visual context.
3. Pass the selected video and transcript to `media.hyperframes_caption`.
4. Let Media Service generate the timed HyperFrames HTML, CJK font setup, and subtle
   sentence-level entrance and exit animation.

Ordinary caption generation does not require Agent-authored `caption_html`. Explicit
advanced HTML remains an optional lower-level extension and is rejected when timed
caption elements are permanently hidden.

Do not report success until a durable captioned video exists. Review a rendered frame
before reporting completion. Missing Node, Chrome, FFmpeg, GSAP, or HyperFrames CLI
dependencies must be surfaced rather than silently replaced with static captions.
