---
name: hyperframes-captions
description: Render sentence-synchronized HyperFrames captions over an existing video. Use when the user asks for HyperFrames, dynamic captions, animated social captions, neon/glitch/editorial caption styles, or invokes $hyperframes-captions.
---

# HyperFrames Captions

Execute two persisted stages so transcription and rendering remain independently observable.

1. Run `media.transcribe` on the complete selected video. Preserve sentence/segment cue
   timestamps and the source language unless translation is explicitly requested.
2. Before rendering, inspect the available video content plus any persisted plot,
   screenplay, storyboard, shot prompts, transcript, and user style instructions. Read
   [references/styles.md](references/styles.md), choose the caption style that best
   supports the video's genre, dramatic function, visual language, pacing, and intended
   platform, and briefly record the reason in the stage parameters or rationale. Do not
   select `caption-highlight` merely because the user omitted a style. Explicit user
   style instructions take precedence when they remain readable and appropriate.
3. Run `media.hyperframes_caption` with the same video and transcript artifacts. Always
   pass the selected `style` explicitly. Select `position` and `accent_color` from the
   same content analysis; normally keep dialogue in `bottom-safe`, preserve adequate
   contrast, and avoid covering faces, actions, or important composition areas.

If the video and all narrative artifacts are unavailable or too ambiguous to support a
meaningful style decision, use `caption-highlight` as a compatibility fallback and state
that the fallback was used. It is not the normal default selection path.

Do not schedule `subtitle.compose` or `media.subtitle_burn` for this workflow: those
produce static FFmpeg/libass captions. Do not claim completion until the HyperFrames
stage produces a durable video artifact.

Display each current cue as a complete sentence, synchronized to its detected start and
end. Fade the sentence in and out subtly; never animate, recolor, enlarge, or flash each
individual word or Chinese character. If sentence cues are unavailable, group word
timestamps into readable short phrases rather than exposing word-level animation. Keep
Chinese text in a CJK-capable font and review a rendered caption frame before reporting
success.

If the runtime reports missing Node 22+, Chrome, FFmpeg, GSAP, or `HYPERFRAMES_CLI`,
surface that exact configuration problem and stop. Never silently fall back to static
captions when the user explicitly requested HyperFrames.
