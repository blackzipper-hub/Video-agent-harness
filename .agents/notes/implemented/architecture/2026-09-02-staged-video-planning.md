# Agent Note: Semantic-checkpoint video planning

Status: implemented

English | [中文](2026-09-02-staged-video-planning.zh.md)

## Problem

A complete video DAG cannot be planned honestly before media-dependent facts exist. Music duration and beats, transcribed words, product details, generated character references, and continuity repair decisions become known only after earlier phases finish. Requiring one complete `VideoSpec` up front either invents those facts or moves creative planning into the Runtime.

## Decision

DeepSeek remains the only LLM planner. A new build stores a `ProjectIntent`, an immutable partial `VideoSpecRevision`, and only the first deterministic phase. Workflow Skills declare `full`, `staged`, or `agentic` planning and semantic checkpoints in frontmatter. When a phase completes, Video Runtime commits draft artifacts, creates a durable `PlanCheckpoint`, and changes the Build to `waiting_agent`.

The BFF checkpoint coordinator is only a leased delivery worker. It queues a bounded artifact summary into the same DeepSeek Session with `session.prompt(mode=queue)`. DeepSeek reloads the locked Workflow, inspects the checkpoint, and resolves it through a high-level tool. Runtime validates project, user, Session, Workflow, capability, and base revisions, then atomically appends a `BuildPlanRevision` and resumes deterministic execution. Completed steps are immutable. Provider polling, transcoding, frame extraction, concatenation, upload, and ordinary validation never wake the model.

Legacy schema-version-1 plans continue unchanged. New staged builds use schema version 2 and are controlled by `VIDEO_STAGED_PLANNING_ENABLED`.

## Alternatives considered

- **Call an LLM after every BuildStep** — adapts continuously, but is costly, slow, difficult to recover, and gives technical operations creative authority they do not need.
- **Keep one full DAG** — simplest executor, but forces the initial model call to fabricate unknown media facts.
- **Put a planner inside Video Runtime** — reduces coordination work, but creates a second Agent loop and conflicting sources of creative decisions.

## Consequences

- Plans are append-only and reproducible; stale checkpoint resolutions return conflicts.
- Automatic continuation survives BFF or Runtime restarts through durable leases and idempotency keys.
- Workflow authors choose semantic boundaries without modifying DeepSeek Harness core.
- The Create Space can show auditable phase, artifact, Skill, and decision summaries without exposing hidden chain-of-thought.
- A checkpoint accepts either a complete revised `VideoSpec` or a structured section patch. Runtime merges patches over the immutable previous revision and validates the complete phase input before appending steps.
- The keyframe workflow adds separate story, reference, and first-keyframe semantic boundaries while preserving Cuti's real-tail continuity dependency for later shots.
