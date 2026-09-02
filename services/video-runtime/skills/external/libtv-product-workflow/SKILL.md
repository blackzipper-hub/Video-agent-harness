---
name: libtv-product-workflow
description: >-
  Workflow for producing a polished commercial product video from zero to final master: classify multiple product references, lock product identity, derive strategy and a timed shot plan, generate each shot directly with Seedance 2 multimodal reference-to-video without storyboards or keyframes, validate takes, repair transitions with planned B-roll, design sound, and assemble the final film. Use for product commercials, launch films, premium product promos, technology advertisements, or when explicitly invoked as $libtv-product-workflow.
metadata:
  kind: workflow
  version: "0.2.0"
  roles: [workflow, stage_supervisor]
  scope:
    type: workflow
  selectors:
    capabilities:
      - atomic.text.generate
      - api.provider.generate
      - api.ark_protocol.generate
      - atomic.music.generate
      - media.concat
  hooks: [before_stage, after_stage]
  workflow:
    title: LibTV multireference product commercial
    mode: libtv_product_workflow
    entrypoints: [text, image, audio, video]
    parameters:
      workflow_mode: libtv_product_workflow
      shot_workflow_mode: product_multiref_seedance2
      content_category: product_ad
    pipeline:
      - atomic.text.generate
      - api.provider.generate
      - api.ark_protocol.generate
      - atomic.music.generate
      - media.concat
    requires_keyframe: false
    allowed_capabilities:
      - actions.suggest
      - atomic.text.generate
      - atomic.music.generate
      - music.generate
      - api.provider.generate
      - api.ark_protocol.generate
      - media.concat
      - media.extract_frame
      - open_montage.tool.invoke
---

# LibTV Product Workflow

Produce a product commercial through explicit, reviewable stages. Treat source media as identity and creative references, never as an implicit first frame. Derive visual style from the product, audience, channel, and verified claims.

## Activation and persistence

Use this Skill only after the user invokes `$libtv-product-workflow` or explicitly accepts it. Keep it active through final review. Include these parameters on every task:

```json
{
  "workflow_mode": "libtv_product_workflow",
  "shot_workflow_mode": "product_multiref_seedance2",
  "content_category": "product_ad",
  "activated_workflow": "libtv-product-workflow"
}
```

Persist `applied_skill_ids`, `constraint_coverage`, ordered reference roles, and the final prompt. Refuse generation when required identity constraints are absent.

## Operating rules

- Match all user-visible briefs, plans, artifact titles, summaries, and review reports to
  the Run output language (`zh` for Chinese, `en` for English). Keep machine-readable
  field names unchanged; the Seedance vendor prompt may remain Chinese when required.
- Inspect real artifacts after each generation batch before scheduling dependents.
- Show creative briefs, reference maps, shot prompts, generated takes, music options, and final videos as important results. Hide raw model requests and tool I/O.
- Update the user-visible todo list on stage changes, retries, failures, and required input.
- Never invent product specifications, claims, prices, awards, certifications, logos, testimonials, or readable copy.
- Label references as `product_identity`, `brand_asset`, `detail`, `style`, `setting`, `composition`, `motion`, or `sound`.
- Style and setting references must never redefine product identity.
- Do not generate storyboards or keyframes. Do not use a reference image as an implicit first frame.
- Do not claim a quality gate passed unless the resulting media was actually inspected.

Read [references/stage-contracts.md](references/stage-contracts.md) before planning. Read [references/quality-gates.md](references/quality-gates.md) before accepting reference packages, videos, joins, or the master.

## Stage 0 - Intake and reference map

Collect or infer product media, verified facts, brand assets, audience, channel, duration, aspect ratio, intended action, required copy, prohibited content, and sound preference. Classify every uploaded reference by role and observable coverage. Report identity risk when front/back/side/detail coverage is missing.

Output `product_intake` and `reference_map`.

## Stage 1 - Product identity contract

Analyze protected observable attributes: silhouette, proportions, component count and placement, colors, materials, finish, label geometry, logo placement, and distinctive details. Separate `hard_constraints`, `verified_claims`, `guidance`, `unknowns`, and `forbidden_fabrications`.

Output `product_identity_contract`. Merge it deterministically into downstream prompts.

## Stage 2 - Strategy and creative system

Translate verified selling points into visible product moments. Define audience, promise, memorable idea, emotional arc, proof moments, call to action, visual bible, sound bible, and negative constraints. Do not imitate or imply affiliation with a named brand.

## Stage 3 - Timed sequence and shot plan

For every shot specify duration, framing, camera move, product action, lighting change, required reference roles, product state in/out, voiceover, SFX, transition intent, and measurable acceptance criteria. Plan B-roll so unstable joins can be repaired locally.

Output `sequence_plan` and `shot_plan` before media generation.

## Stage 4 - Multireference shot packages

Do not generate a storyboard or keyframe. Build one `shot_reference_package` per shot directly from uploaded source media and the approved reference map.

1. Select up to nine images and assign each a stable role: `product_identity`, `detail`, `setting`, `style`, `composition`, or `lighting`.
2. Keep numbered ordering stable (`@图片1` ... `@图片9`) and explain each reference's purpose in the Chinese shot prompt.
3. Use complementary product views where available. No image is a mandatory start frame.
4. For product-absent shots, omit all `product_identity` images from that shot package.
5. Persist ordered URLs, roles, identity coverage, and missing coverage. Refuse generation if the required visible surface lacks evidence.

Output `shot_reference_packages`. This stage is text/data preparation only; never call `atomic.image.generate`.

## Stage 5 - Seedance 2 multireference video generation

Generate each shot directly through `api.provider.generate` (preferred) or `api.ark_protocol.generate`, using Seedance 2 multimodal reference-to-video. Never use `atomic.video.generate`, I2V mode, `start_frame`, `first_frame`, `last_frame`, or a generated keyframe.

Required normalized provider profile:

```json
{
  "provider": "auto",
  "model": "doubao-seedance-2-0-260128",
  "mode": "reference_to_video",
  "images": ["ordered reference URLs"],
  "prompt": "Chinese prompt using @图片1...@图片N",
  "duration": 8,
  "aspect_ratio": "16:9",
  "resolution": "1080p",
  "generate_audio": true,
  "workflow_mode": "libtv_product_workflow",
  "shot_workflow_mode": "product_multiref_seedance2"
}
```

`mode` must never be `i2v`, `image_to_video`, or `image-to-video`; those collapse the request to `images[0]` as a start frame. Reference images are semantic constraints, while their stable list order binds `@图片N` in the prompt.

Generate at most two shots concurrently. Use the prompt for narrative change over time: product motion, camera, light, focus, pace, sound, and the intended use of each numbered reference. Generate independent shots; establish continuity through matched product state, composition, motion direction, lighting, and planned B-roll.

For high-risk hero shots, generate multiple candidates when budget permits. Rank by identity fidelity, motion integrity, commercial polish, narrative fitness, and editability.

## Stage 6 - Repair, enhancement, and normalization

Repair joins in this order: trim compatible phases, regenerate the weaker shot, insert planned B-roll, then use a short justified transition. Normalize resolution, aspect ratio, frame rate, codec, pixel format, color, audio format, black frames, and duration. Enhancement cannot repair identity or motion errors.

## Stage 7 - Voice, music, and sound design

Choose `music_led`, `sound_design_led`, or `hybrid`. Align narration and SFX to actual visual events. Add readable copy only in deterministic post-production.

## Stage 8 - Assembly and final master

Create a rough cut, review pacing and continuity, repair joins, synchronize sound, normalize loudness, and export the master. Inspect order, duration, identity, claims, text, motion, joins, color, framing, and playback. Persist and display the final video as a workspace artifact.

## Failure and retry behavior

- Report failures immediately with stage, attempt, reason, and next action.
- Retry transient provider/rate-limit errors with bounded backoff.
- Do not retry deterministic failures unchanged; revise references, constraints, prompt, model, or shot design.
- Stop for missing permissions, credentials, verified facts, or irreplaceable references.
- Preserve successful upstream artifacts and restart from the nearest failed stage.
