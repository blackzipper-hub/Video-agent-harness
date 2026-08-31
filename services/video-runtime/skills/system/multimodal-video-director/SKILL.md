---
name: multimodal-video-director
description: >-
  Plan, generate, review, revise, and assemble multimodal videos through iterative
  use of story, outline, character, scene, shot, keyframe, image, music, shot-video,
  video-edit, and assembly capabilities. Use for complete video production,
  controlled stage-by-stage production, creative direction, or revision of an
  existing video project.
metadata:
  short-description: Direct iterative multimodal video production
  version: "1.0.0"
  task_type: open-ended
---

# Multimodal Video Director

Treat video production as an iterative creative workflow, not one tool call.

## Start

1. Inspect the authoritative project snapshot and selected artifact versions.
2. Clarify only decisions that materially change the result. For a vague idea,
   offer focused production choices before generating expensive media.
3. Choose one workflow:
   - **Direct video**: one explicit video from a sufficiently concrete prompt.
   - **Master pipeline**: delegate the complete existing pipeline.
   - **Directed production**: coordinate stages and review artifacts between them.
   - **Revision**: modify selected existing artifacts without restarting the project.
4. List capabilities before committing work. Never invent capability IDs or UUIDs.

Read `references/workflows.md` when selecting stages, dependencies, or revision paths.

## Directed production loop

For each phase:

1. Inspect the latest snapshot.
2. Load only the inputs actually required by the next phase.
3. Submit independent tasks together and dependent tasks only after their real
   upstream artifacts exist.
4. Wait for task completion.
5. Review artifact metadata, user feedback, and selected versions.
6. Revise the plan or continue to the next phase.

Typical dependency chain:

`outline → characters → scenes → shots → keyframes → shot videos → assembly`

Story prose, reference images, and music may support this chain, but must not be
generated unless requested or justified by the activated workflow.

## Creative consistency

Carry forward explicit constraints: aspect ratio, duration, language, visual style,
character identity, camera grammar, pacing, music mood, dialogue, and target platform.
Use selected artifact version IDs rather than summaries when exact continuity matters.

## Completion

Do not report completion when work is merely queued or delegated. Complete only after
the requested final artifact exists, or explicitly pause for a user decision with the
current artifacts and next choices summarized.
