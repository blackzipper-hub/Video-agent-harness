---
name: ink-press-product-workflow
description: >-
  Cuti workflow wrapper for adapting the upstream video-shotcraft Ink Press
  Remotion template into a product promotional film. Use when the user asks for
  an Ink Press product video, a paper-and-ink product promo, or explicitly invokes
  $ink-press-product-workflow. It preserves the upstream template's ten-shot
  structure and replaces only product screenshots, verified copy, and branding.
metadata:
  kind: workflow
  version: "0.1.0"
  roles: [workflow, stage_supervisor]
  scope:
    type: workflow
  selectors:
    capabilities:
      - atomic.text.generate
      - media.extract_frame
  hooks: [before_stage, after_stage]
  workflow:
    title: Ink Press product promotional film
    mode: ink_press_product_workflow
    planning:
      mode: staged
      checkpoints:
        - id: product_ready
          after_phase: source_analysis
          next_phase: visual_production
          required_artifacts: [product_analysis, source_image]
          resolves: [shots, product_constraints]
          instruction: Continue only with the verified product analysis and installed Ink Press template runtime.
    entrypoints: [text, image, video]
    dependencies:
      skills: [video-shotcraft]
    parameters:
      workflow_mode: ink_press_product_workflow
      content_category: product_ad
      source_skill: video-shotcraft
      template_name: ink_press
    pipeline:
      - atomic.text.generate
      - media.extract_frame
    requires_keyframe: false
    allowed_capabilities:
      - actions.suggest
      - atomic.text.generate
      - media.extract_frame
---

# Ink Press Product Workflow

Adapt the vendored `video-shotcraft` Ink Press template with minimal changes.
This wrapper defines Cuti stage behavior; the source of truth for creative and
rendering details remains the unmodified sibling Skill.

## Required source material

Before planning, call `load_skill(name="video-shotcraft")`, then load and follow
these `video-shotcraft` resources in order:

1. `template/TEMPLATE.md`
2. `references/aesthetic-rules.md`
3. `references/final-review.md`

Do not silently substitute another visual style, generative-video workflow, or
keyframe pipeline. Preserve the template's code, timing, motion grammar, and
sound design unless a product requirement makes a specific replacement necessary.

## Activation and persistence

Activate this workflow when the user names Ink Press or invokes this Skill. Keep
both `ink-press-product-workflow` and `video-shotcraft` active through final QA.
Persist the following on every stage:

```json
{
  "workflow_mode": "ink_press_product_workflow",
  "content_category": "product_ad",
  "source_skill": "video-shotcraft",
  "template_name": "ink_press"
}
```

Persist `applied_skill_ids`, the product asset manifest, verified copy, the shot
adaptation map, changed template files, render command, and final artifacts.

## Stage sequence

### Stage 0 - Product intake

Collect the real product screenshots or recordings, verified product facts,
brand mark, product name, audience, channel, aspect ratio, language, CTA, and
prohibited claims. Never invent features, metrics, endorsements, or UI states.

Output `product_intake` and `asset_manifest`.

### Stage 1 - Template fit check

Confirm the product can be explained through real interface or product imagery.
Identify only the template elements that must change. Keep the original 36.2
second, 1920x1080, 30 fps, ten-shot structure by default.

Output `template_fit` with `keep`, `replace`, and `risk` sections.

### Stage 2 - Copy and shot adaptation map

Map verified product content onto the existing ten shots documented by
`template/TEMPLATE.md`: brand open, benefit card, product fly-in, detail view,
second benefit card, proof/stack moment, workflow benefit card, report/detail
moment, team benefit card, and branded outro.

For each shot specify source asset, replacement copy, protected product details,
and acceptance criteria. Keep each title within the original layout capacity.
Output `shot_adaptation_map` before any template edit or render.

### Stage 3 - Asset preparation

Use real product captures for real interfaces and readable product text. Crop and
stage assets at the paths required by the template. Preserve aspect ratio and do
not place secrets, private data, or fabricated UI into screenshots.

### Stage 4 - Minimal template adaptation

Copy the `video-shotcraft` resource directory `template/` into a task-scoped
working directory. Change
only product textures, copy, and brand fields required by the approved adaptation
map. Do not rewrite scene choreography or shared components merely to make the
template look different. Record every changed file.

### Stage 5 - Deterministic Remotion render

Run the template's pinned install and render commands from its working directory:

```text
npm install
npx remotion render src/index.ts AiflPromo out/promo.mp4
```

This stage requires a trusted Node/Remotion host capability. Cuti's Python Skill
sandbox must not attempt to install or execute Node. If the host does not expose
Remotion, stop at this stage, retain all planning artifacts, and report the exact
missing capability; never claim the video rendered and never fall back to an AI
video generator without user approval.

### Stage 6 - Final review and delivery

Follow the `video-shotcraft` resource `references/final-review.md`. Verify duration, frame
size, frame rate, product accuracy, copy, safe margins, shot order, motion,
audio, and playback. Persist the MP4 as the important workspace result so the
front end displays it; do not return only a raw URL.

## User-visible behavior

- Match plans, shot maps, artifact titles, and reports to the user's language.
- Show the product brief, shot adaptation map, meaningful previews, and final MP4.
- Hide raw model requests, template build logs, and tool input/output.
- Update the todo list when stages start, complete, retry, or block.
- Report a blocking asset or runtime requirement immediately and precisely.
