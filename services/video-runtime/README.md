# Video Runtime

English | [中文](README.zh.md)

The Python Video Runtime owns long-lived video projects independently of an agent Session or Run. It stores immutable project versions, versioned artifacts, typed artifact dependencies, change requests, rebuild plans, durable builds, validation results, timelines, exports, and monotonically sequenced project events.

## Run

Provider dispatch resolves task input ArtifactVersions into ordered image, video, and audio URI lists for direct and atomic calls. Explicit media parameter order takes precedence; remaining task inputs follow their declared order and are merged without duplicates. Missing referenced media and invalid indexed prompt references fail before submission. Resolved input versions and concrete parameters are recorded on output artifacts. A Seedance model identifier supplied in the legacy `provider` field is separated into the WaveSpeed transport and the exact requested model; conflicting version selectors fail before submission. Remote operation IDs and idempotency keys survive dispatch; reference images do not implicitly become first frames.

Failed-task repair accepts new parameters through PlanPatch replacement mappings. Pending descendants are cloned with rewired dependencies and their old steps are cancelled in the same revision transaction. Failed steps retain their original parameters and a `superseded_by` pointer; whole-build retries skip superseded and cancelled steps. Started descendants cannot be rewired. A failed continuous Build remains failed while a repair checkpoint is inspected and resumes only after a valid replacement patch commits against current plan, spec, and project versions.

The Create stop action cancels both the DeepSeek turn and active Runtime builds. Stopped builds retain drafts, pending steps, and remote operation IDs; upstream jobs already submitted may continue and incur charges. Sending a follow-up requires explicit resume confirmation. Resume continues the latest stopped build against its original project version, reuses completed steps, and reconciles existing remote operations instead of creating a replacement build. Refreshing the page does not resume work.

Install the same Python dependencies as the Runtime image, from `pyproject.toml`:

```sh
python -m pip install -e .
python -m uvicorn app.video_runtime.standalone:app --host 127.0.0.1 --port 8001
```

Set `VIDEO_RUNTIME_DATABASE_URL` for Postgres durability. Without it, the standalone service uses the atomic local JSON repository at `VIDEO_RUNTIME_LOCAL_STATE_PATH` (default `./data/video-runtime-state.json`). Set `VIDEO_RUNTIME_IN_MEMORY=true` only for isolated tests. The root `compose.video.yml` applies every ordered migration in `migrations/video_runtime` before starting the service.

For local storage the standalone entrypoint defaults `PUBLIC_BASE_URL` to `http://127.0.0.1:8001`, matching its documented port and `/files/*` mount. Set it explicitly when the Runtime is exposed on another origin. Provider-bound media is still uploaded through the media-egress adapter, so loopback URLs are never sent to remote generation services.

## Runtime APIs

The versioned API under `/api/video` creates and inspects projects, returns one aggregate editing workspace, previews structured edits and deterministic dependency impact, submits or cancels builds, selects artifact versions, restores project versions, exports a fixed project version, manages configured plugins, and streams project events. Every modifying call has an idempotency key where replay can cause duplicate work. Build, selection, restore, and export operations compare their base project version before changing authoritative state.

## Plugin execution

With staged planning and `VIDEO_CONTINUOUS_PLAN_PATCH_ENABLED` enabled, one Runtime worker owns each Build. It admits ready tasks up to the configured concurrency limit and reloads the persisted plan on task completion, terminal failure, or PlanPatch submission. A finished task requests Agent planning without waiting for unrelated Provider operations. One outstanding checkpoint per Build coalesces subsequent terminal updates into the next snapshot; delivery uses the bound DeepSeek Session, not a second planner. Failed tasks remain recorded and require an Agent-authored replacement with a new id.

During execution, `POST /api/video/projects/{project_id}/builds/{build_id}/checkpoints/live` opens or reuses an active planning snapshot. Resolve its returned id with exact plan/spec revisions and an idempotency key. A patch can append tasks with dependencies on existing or newly appended tasks, or cancel pending tasks together with their pending dependants. Admission and cancellation compare the stored Step status atomically; running tasks cannot be cancelled through a pending-task patch. Goal completion rejects remaining active work. Shutdown preserves remote-operation ids for reconciliation, and recovery resumes continuous Builds even when Agent planning is outstanding. A single Runtime process must own execution for a repository; distributed worker leasing is not provided.

`video-plugin.yaml` declares provider, workflow, style, validator, and media contributions plus dependencies and permissions. `VIDEO_PLUGIN_PATHS` may replace the bundled `plugins` root with platform-separated directories whose immediate child directories contain manifests. A plugin may extend `BaseVideoPlugin`; its `capability_handlers()` maps declared capability ids to executable handlers. Trusted built-ins can load in-process. An untrusted plugin declares `sandbox_runtime` (image, entrypoint, and timeout); the runtime never imports its module and executes the bundle through `services/sandbox-worker` with the signed grant's limits.

`VIDEO_SKILL_PATHS` may replace the process-wide Skill roots. Without it, one `VideoSkillRuntime` discovers the bundled system, built-in, external, creative, stage, and video-edit Skills. Its shared catalog validates executable contracts and Workflow declarations, freezes the Director and helper Skill context on each planned Build step, and registers Workflow contributions through `cuti.skill-workflows`. `GET /api/video/workflows` returns manifest and effective Skill workflows.

Every selectable Workflow must expose a named dedicated compiler contract. Unknown Skill modes and Workflow plugins without an explicit runtime description are reported as unavailable; there is no generic/default Workflow compiler fallback. Original Cuti Workflow instruction bodies are preserved while staged-planning metadata is added to frontmatter.

The built-in adapters reuse Cuti's text, image, music, video, TTS, FFmpeg, subtitle, and lipsync operations. `cuti.seedance-story`, `cuti.music-video`, and `cuti.lipsync-music-video` compile provider-neutral `VideoSpec` values; `cuti.style-presets` applies installed prompt defaults before compilation. Production identity uses either the standalone service bearer token or the combined application's Cuti JWT adapter, and fails closed when neither is configured.

Every new `ProjectIntent` and `VideoSpec` persists one language contract with independent UI, user-visible content, spoken, subtitle, and provider-prompt languages. The BFF injects that contract into initial planning, follow-up edits, and automatic checkpoint turns; Runtime-generated user artifacts record the contract and reject clear text-language mismatches. Legacy documents with only `language` receive matching defaults for all content fields.

All registered capabilities execute through `RuntimeCapabilityRegistry` and `CapabilityExecutionGateway`. The gateway verifies a server-signed grant bound to the project, Session, user, plugin, capability, domains, cost ceiling, timeout, concurrency, retries, idempotency key, audit id, and expiry. Executable in-process plugins require a server-owned `VIDEO_CAPABILITY_GRANT_SECRET` of at least 32 bytes.

Scene-reference isolation is checked both at the provider boundary and against rendered pixels. A failed scene-reference check enters the staged build's single semantic-repair pass before any dependent video segment runs. `VIDEO_SCENE_REFERENCE_VISUAL_VALIDATION_ENABLED` explicitly enables or disables the pixel check; when omitted it is enabled whenever `OPENAI_API_KEY` is configured. `VIDEO_SCENE_REFERENCE_VALIDATOR_MODEL` selects the vision-capable validation model.

## Compatibility API

`app.main:app` retains the imported Cuti HTTP surface for Video Studio clients, but `VIDEO_AGENT_BACKEND` accepts only `deepseek`. Product startup translates the V2 chat and thread-oriented Studio routes to native Session RPC and calls `VideoBuildRuntime`; it does not mount the imported DeepAgents/LangGraph planner. Imported media services resolve their prompt Skills through the same process-owned catalog.

If a pasted Create-Space URL names a DeepSeek Session whose Project binding is absent from the restored Runtime store, the BFF creates a fresh Session and returns its new `thread_id`; it never attaches a new Project to an unbound historical Session.

## Tests

Checkpoint delivery reconciles consumed Session messages with completed turns. A turn that ends without resolving its checkpoint is retried within the existing delivery budget; retries and checkpoint inspection include current task results. Queued messages, active turns, resolved checkpoints, and cancelled Builds are not treated as missing acknowledgements. The lease remains the recovery mechanism for interrupted delivery.

```sh
python -m unittest discover -s tests/video_runtime -v
```

The broader imported Cuti suite retains its original dependencies and service requirements.
