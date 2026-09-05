# Video Runtime

English | [中文](README.zh.md)

The Python Video Runtime owns long-lived video projects independently of an agent Session or Run. It stores immutable project versions, versioned artifacts, typed artifact dependencies, change requests, rebuild plans, durable builds, validation results, timelines, exports, and monotonically sequenced project events.

## Run

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

`video-plugin.yaml` declares provider, workflow, style, validator, and media contributions plus dependencies and permissions. `VIDEO_PLUGIN_PATHS` may replace the bundled `plugins` root with platform-separated directories whose immediate child directories contain manifests. A plugin may extend `BaseVideoPlugin`; its `capability_handlers()` maps declared capability ids to executable handlers. Trusted built-ins can load in-process. An untrusted plugin declares `sandbox_runtime` (image, entrypoint, and timeout); the runtime never imports its module and executes the bundle through `services/sandbox-worker` with the signed grant's limits.

`VIDEO_SKILL_PATHS` may replace the process-wide Skill roots. Without it, one `VideoSkillRuntime` discovers the bundled system, built-in, external, creative, stage, and video-edit Skills. Its shared catalog validates executable contracts and Workflow declarations, freezes the Director and helper Skill context on each planned Build step, and registers Workflow contributions through `cuti.skill-workflows`. `GET /api/video/workflows` returns manifest and effective Skill workflows.

Every selectable Workflow must expose a named dedicated compiler contract. Unknown Skill modes and Workflow plugins without an explicit runtime description are reported as unavailable; there is no generic/default Workflow compiler fallback. Original Cuti Workflow instruction bodies are preserved while staged-planning metadata is added to frontmatter.

The built-in adapters reuse Cuti's text, image, music, video, TTS, FFmpeg, subtitle, and lipsync operations. `cuti.seedance-story`, `cuti.music-video`, and `cuti.lipsync-music-video` compile provider-neutral `VideoSpec` values; `cuti.style-presets` applies installed prompt defaults before compilation. Production identity uses either the standalone service bearer token or the combined application's Cuti JWT adapter, and fails closed when neither is configured.

All registered capabilities execute through `RuntimeCapabilityRegistry` and `CapabilityExecutionGateway`. The gateway verifies a server-signed grant bound to the project, Session, user, plugin, capability, domains, cost ceiling, timeout, concurrency, retries, idempotency key, audit id, and expiry. Executable in-process plugins require a server-owned `VIDEO_CAPABILITY_GRANT_SECRET` of at least 32 bytes.

Scene-reference isolation is checked both at the provider boundary and against rendered pixels. A failed scene-reference check enters the staged build's single semantic-repair pass before any dependent video segment runs. `VIDEO_SCENE_REFERENCE_VISUAL_VALIDATION_ENABLED` explicitly enables or disables the pixel check; when omitted it is enabled whenever `OPENAI_API_KEY` is configured. `VIDEO_SCENE_REFERENCE_VALIDATOR_MODEL` selects the vision-capable validation model.

## Compatibility API

`app.main:app` retains the imported Cuti HTTP surface for Video Studio clients, but `VIDEO_AGENT_BACKEND` accepts only `deepseek`. Product startup translates the V2 chat and thread-oriented Studio routes to native Session RPC and calls `VideoBuildRuntime`; it does not mount the imported DeepAgents/LangGraph planner. Imported media services resolve their prompt Skills through the same process-owned catalog.

If a pasted Create-Space URL names a DeepSeek Session whose Project binding is absent from the restored Runtime store, the BFF creates a fresh Session and returns its new `thread_id`; it never attaches a new Project to an unbound historical Session.

## Tests

```sh
python -m unittest discover -s tests/video_runtime -v
```

The broader imported Cuti suite retains its original dependencies and service requirements.
