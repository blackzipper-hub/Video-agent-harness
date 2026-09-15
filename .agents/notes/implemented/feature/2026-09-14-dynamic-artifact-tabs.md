# Agent Note: Dynamic artifact tabs in the creation workspace

Status: implemented

English | [中文](2026-09-14-dynamic-artifact-tabs.zh.md)

## Problem

The creation workspace rendered production state, final delivery, intermediate media, and planning documents in one long stream. A growing project made its current result difficult to find, while Workflow-specific panels would make every new output type require another frontend integration.

## Decision

Studio derives four stable tabs from persisted task, Build, selection, and Artifact fields. Process owns execution state. Final video prefers the persisted selected video, then explicitly named assembled, master, exported, or final video Artifacts, and uses the latest video only after production completes. Assets owns remaining uploaded and generated material, with derived All, Images, Videos, Audio, Text, and Other filters. Text documents and research remain available in Script as part of the authoring view while also being discoverable by media type in Assets.

The tab counts and final-video availability are derived during render from current data. Workflows do not emit UI tab names, and the frontend does not duplicate model planning state.

Image, video, audio, and script cards share one native disclosure component for the persisted final generation prompt. The control remains present when the prompt is absent and explains that condition, so old and uploaded assets do not silently appear broken.

## Alternatives considered

**Ask every Workflow or model prompt to assign a UI category.** Rejected because presentation metadata would enter model contracts, drift across Skills, and leave older Artifacts unclassified.

**Keep one chronological result stream.** Rejected because execution telemetry and intermediate media obscure the selected deliverable as project histories grow.

## Consequences

New Artifact types remain visible through the generic Assets fallback. A misleading final-like title can influence explicit final detection when no delivery is selected, while the persisted selection remains authoritative for the current delivery. The built-browser scenario covers text filtering and expandable prompts for image, clip, audio, script, and selected final-video routing together with live conversation events.
