# Multimodal video workflows

## Direct video

Use `video.generate` only for one explicit instant-mode video. Set
`single_video=true`. Use `video_gen.generate` only when the direct video-generation
agent is specifically appropriate.

## Master pipeline (PAUSED)

`video.pipeline.generate` is **disabled**. Never propose it, even if the user
says "直接生成" / "one-shot" / "full pipeline". Decompose the goal into discrete
stage capabilities and revise the plan after each stage succeeds.

## Directed production

Use these capabilities in dependency order:

1. `outline.generate`
2. `character.generate`
3. `scene.generate`
4. `shot.generate`
5. `keyframe.generate`
6. `shot.video.generate`
7. `video.assemble`

After every stage, inspect actual artifacts before planning the next stage. Character,
scene, shot, and keyframe tasks may be parallelized only when their required upstream
artifacts already exist and the capability contract permits the selected inputs.

## Supporting media

- `story.generate`: prose or script requested by the user.
- `image.generate`: standalone or reference image requested by the user.
- `music.generate`: soundtrack requested by the user or explicitly included in the
  chosen production workflow.

Do not expand a narrow request into extra media merely because it could be useful.

## Revisions

- `keyframe.regenerate`: revise selected keyframes using real keyframe UUIDs.
- `character.regenerate`: revise selected characters using real character UUIDs.
- `shot.video.regenerate`: revise selected shot videos using real shot/video UUIDs.
- `video.edit`: edit an existing selected video.
- `video.assemble`: reassemble selected shot videos into a new final version.

Keep prior artifact versions available. Never replace an exact selection with a newly
generated approximation without user intent.

## Decision checkpoints

Pause when:

- A vague request leaves style, format, or production mode materially ambiguous.
- Generated characters or keyframes require approval before expensive video work.
- Multiple artifact variants exist and no selected version is authoritative.
- A failure invalidates downstream dependencies.

Continue automatically when the next stage is unambiguous from confirmed artifacts
and constraints. "直接生成" still means stepwise stage execution—not the paused
master pipeline capability.
