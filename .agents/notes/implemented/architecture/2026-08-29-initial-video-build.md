# Agent Note: First-build video work uses the artifact build graph

Status: implemented

English | [中文](2026-08-29-initial-video-build.zh.md)

## Problem

The incremental runtime could rebuild selected artifacts but could not create the first immutable video version. Reusing the legacy Cuti coordinator would have introduced a second LLM planner under DeepSeek, while treating a fifteen-second video as one provider call would lose shot continuity, restart recovery, draft reuse, cost attribution, and atomic publication.

## Decision

DeepSeek produces one validated, provider-neutral `VideoSpec`. A Workflow Plugin compiles it into a persisted `BuildPlan` with `initial`, `incremental`, and `export` kinds and stable `BuildStep` identities. The runtime executes capabilities, saves each successful output as a draft artifact, retries one failed artifact by policy, and atomically publishes a new `ProjectVersion` only after validation passes.

The built-in `cuti.seedance-story` workflow uses the existing Cuti atomic providers and media operations. Each character has a stable reference artifact, and a shot depends only on references for characters it uses. Video clips form a serial continuity chain through extracted real tail frames; independent work is represented by dependency-free graph branches. `cuti.music-video` and `cuti.lipsync-music-video` reuse that graph and add their domain requirements. `cuti.media-core` owns TTS, timeline, concat, mix, subtitle, lipsync, and export-facing media operations. `cuti.continuity-validator` owns structural checks, final media probing, and the optional existing Cuti VLM consistency check.

Provider submission IDs are written to `BuildStep` as soon as the remote operation is accepted. On retry or process restart, the ID is passed back into the provider bridge for result reconciliation before any new submission. The self-host profile runs the imported Cuti Media Service with a shared local-media volume; Video Runtime exposes that volume at `/files`, so S3 is optional rather than an implicit requirement.

DeepSeek gains project-level create, plan, build, inspect, structured edit preview, rebuild, status, cancellation, artifact selection, and export tools. Paid edits produce a preview plan and require a separate apply call; its agent loop and Session implementation do not change.

Incremental plans may also create artifacts that did not exist in the base version. For example, adding music to a silent project materializes `bgm` and `mix-bgm` steps, stages both outputs, and selects them only at the atomic version commit. Timeline edits recompile workflow parameters from the proposed VideoSpec: reordering reuses clips and rebuilds assembly, while duration changes invalidate only the affected clip and its media descendants. The Create workspace invokes these operations through Runtime callbacks and never calls the legacy video-analysis editing APIs.

## Verification

Python tests cover VideoSpec validation, dependency-cycle rejection, initial plan compilation, stable tail-frame chaining, provider-ID reconciliation, per-step persistence, incremental artifact creation, media-edit costs, atomic commit, idempotent replay, and PostgreSQL repository restart. Existing incremental, plugin, security, and API tests remain green. TypeScript checks cover the Service definition, HTTP provider, video-tool composition, and the Create media editor. A real GPT Image + Seedance run completed tail-frame extraction, timeline/final assembly, FFprobe validation, atomic version commit, MP4 export, DeepSeek inspection, and playable Video Studio preview.

## Alternatives considered

**Reuse the legacy LangGraph coordinator for first builds.** Rejected because it would give the runtime a second model-owned plan and make recovery depend on conversational state.

**Expose every provider and FFmpeg operation to DeepSeek.** Rejected because the model would own ordering and could bypass the artifact transaction, continuity chain, and nested capability grant.

**Publish each successful artifact immediately.** Rejected because a failed subtitle or final validation would leave users on a partially updated work.

## Consequences

Initial and incremental work share project versions, artifact dependencies, idempotency, recovery, authorization, and audit state. `/create/:threadId` reads one Runtime workspace projection and keeps chat events separate from project build events. Draft artifacts consume storage after failed builds and need retention policy cleanup. Real VLM continuity validation is optional because it adds model cost; deterministic timeline and final-media checks always run.
