# Agent Note: Seedance2 keeps its original Cuti workflow semantics

Status: implemented

English | [中文](2026-08-31-seedance2-workflow-preservation.zh.md)

## Problem

The migrated `seedance2` adapter replaced the selected Skill's autonomous creative instructions with a fixed story graph. It also declared `character-director`, `character-image-tool-director`, `video-director`, and `video-tool-director` on generated steps even though the original Cuti `seedance2/SKILL.md` declares none of those dependencies. A request could therefore display `workflow_id=seedance2` while executing a different creative workflow.

## Decision

DeepSeek remains the only planning loop. Explicit `$seedance2` syntax selects the installed Workflow, and the compatibility BFF places the original `seedance2/SKILL.md` plus its `reference.md` in the logged planning prompt. DeepSeek translates those instructions into `VideoSpec`; the Video Runtime only persists the specification and production records, generates the authored clips with native audio, extracts each real tail frame for the next clip, concatenates the clips, validates the result, and records recovery and audit state.

The adapter no longer creates character reference images or applies generic Cuti Director Skills implicitly. Users may still activate helper Skills explicitly. Text duration in the Create composer overrides the panel default before the request is submitted.

## Verification

Focused Python tests prove `$seedance2` loads the original instructions and reference, persists the resolved Workflow, emits no image-generation or narration pipeline, applies no implicit Director Skills, and preserves serial tail-frame chaining. The Video Studio production build and focused ESLint check pass.

## Alternatives considered

**Keep the fixed graph and rename it.** Rejected because the user selected the installed Cuti Workflow and expects its instructions to govern creative planning.

**Restore Cuti's former coordinator.** Rejected because it would add a second model-planning loop beside DeepSeek; loading the original instructions into DeepSeek preserves one planner.

## Consequences

Selecting `seedance2` now changes both routing and creative behavior. Runtime durability remains independent of the Skill's creative choices. Existing builds keep their persisted plans; only newly planned builds use the restored behavior.
