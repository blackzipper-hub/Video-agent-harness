# Agent Note: Video projects use a separate incremental runtime

Status: implemented

English | [中文](2026-08-28-video-project-runtime.zh.md)

## Problem

An agent Session records a conversation, but an editable video is a long-lived project whose artifacts, timeline, remote generation operations, and versions survive several Sessions. Treating the Session or one agent Run as the project makes deletion, retry, concurrent editing, partial rebuild, and provider recovery change the wrong state. Exposing every provider and media operation directly to the model also duplicates authorization and auditing across execution paths.

## Decision

DeepSeek Harness owns the Session, model call, agent loop, and selection among project-level video tools. The separate Python `VideoBuildRuntime` owns projects, immutable project versions, versioned artifacts, typed dependencies, structured change requests, rebuild plans, durable builds and steps, validation, timelines, exports, and monotonically ordered project events.

The `@cuti-ai/video-runtime` Service Definition, `@cuti-ai/video-runtime-http` Service Provider, and `@cuti-ai/tool-video` Consumer form the process-facing capability seam. The `@cuti-ai/video-agent-bundle` Cordis patch composes them without a video branch in DeepSeek's `agent-loop`.

Video Studio combines the DeepSeek Session event stream with the Video Runtime project event stream into one user-visible production status. It reports model identity and usage, agent decision phases, named tool inputs and results, durable Build progress, and Build steps without exposing private model reasoning or system instructions. A completed agent turn that did not start a Build is reported as an incomplete production attempt rather than as successful video generation.

Video Studio uses the Create workspace's generic Artifact renderer as the presentation authority. Selected and in-progress Runtime artifacts are rendered directly as documents, stories, images, videos, audio, or fallback artifact cards. Successful draft artifacts remain visible when a Build fails. The specialized video results projection and legacy thread-oriented aggregation requests are not used by the Create workspace; Runtime-aware selection preserves version switching without invoking a Provider.

The incremental engine combines structured edits with the selected `VideoSpec`, computes rebuild, validate, and reuse sets deterministically from artifact dependencies, and persists the resulting capability parameters. A rebuild compares its base project version, executes steps with stable idempotency keys, stores successful outputs as drafts, and publishes all artifact selections in one project-version commit. A failure retains the prior version, and restart recovery reuses completed draft steps.

Video plugins declare executable contributions and permissions in `video-plugin.yaml`. Registered nested capabilities pass through a signed execution grant and one authorization, timeout, concurrency, retry, cancellation, and audit gateway. Untrusted executable plugins do not load in-process.

Declarative workflow Skills remain `SKILL.md` packages. The Video Runtime loads their existing Workflow Registry metadata through the trusted `cuti.skill-workflows` adapter, validates declared capabilities and Skill dependencies, and contributes each enabled Skill name as a workflow id. The adapter compiles workflow modes into the normalized BuildPlan instead of restoring the legacy coordinator. DeepSeek's `workflow_id` schema accepts installed ids and the Runtime remains the authority that rejects unknown workflows.

## Verification

The TypeScript composition test mounts the real Cordis tool runtime and dispatches the project tools through the Video Runtime Service. Python tests pin dependency invalidation, executable edit parameters, untouched-shot reuse, stale-version conflict, idempotent commit, plugin lifecycle and disposal, untrusted plugin rejection, signed identity and domain grants, registry dispatch through execution hooks, compatibility-event projection, missing-Session run listing, and the automatic create contract for empty-project follow-ups. The Video Studio production build exercises the aggregate workspace and user-visible production status. Browser acceptance verifies that a failed Build still renders its draft story, script, structured project data, images, videos, audio, and fallback artifacts through the generic renderer.

## Alternatives considered

**Implement video semantics in the DeepSeek agent loop.** Rejected because artifact dependency analysis, project transactions, provider recovery, and timelines are domain state, not language-model control flow. The change would also make upstream synchronization continuously conflict.

**Represent the Video Runtime as one large model-facing plugin with provider-level tools.** Rejected because it exposes unstable internal operations to the model, spends context on execution detail, and permits alternate paths around project-version and authorization checks.

**Keep AgentRun as Project.** Rejected because one project must bind to multiple Sessions and Builds, while deleting or compacting a Session must not delete project artifacts.

**Rewrite Cuti on the TypeScript runtime.** Rejected because Cuti already owns the media providers, workflow compilation, task attempts, remote polling, artifact persistence, validators, and editing pipeline. The process seam preserves that code and isolates incremental migration.

## Consequences

The system has two processes and requires an HTTP identity and cancellation adapter between them. Project correctness becomes independent of model retries and Session lifecycle, and DeepSeek upstream updates remain mergeable because the core loop is unchanged.

Provider, workflow, style, validator, and media implementations can evolve as video plugins. Plugins cannot replace normalized project transactions or publish partial project state. A compatibility BFF translates core Cuti V2 chat routes and thread-oriented Studio project routes to native DeepSeek Session RPC; production deployments still provide authenticated user identity, a Sandbox Worker transport, and concrete provider bundles.

An invalid workflow Skill prevents the adapter from loading rather than leaving a selectable workflow that fails after paid work begins. The adapter preserves static workflow modes and pipeline policy; instructions that require their own executable lifecycle still belong in a full Video Plugin bundle.
