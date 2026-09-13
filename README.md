# Cuti Video Harness

English | [中文](README.zh.md)

Cuti Video Harness is an open-source, project-oriented video agent harness built on [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness). Cuti Harness owns the conversation, agent loop, and high-level tool selection. Cuti's Video Runtime owns durable projects, Skills, artifact dependencies, incremental builds, timelines, validation, and exports.

The primary product UI is Video Studio. Its `/create/:threadId` workspace keeps the Cuti Harness chat on the left and generated scripts, images, clips, audio, build progress, and final video on the right.

## Architecture

```text
Video Studio (:3000)
  |-- /chat-v1 and /api/video
  v
Video Runtime + compatibility BFF (:8001)
  |-- projects, Skills, artifacts, builds, versions
  |-- media service and sandbox worker
  v
Cuti Harness (:3080)
  |-- LLM conversation and video_* tool selection
  v
Provider / Workflow / Validator / Media plugins
```

The integration adds `@cuti-ai/video-runtime`, `@cuti-ai/video-runtime-http`, `@cuti-ai/tool-video`, and `@cuti-ai/video-agent-bundle` without adding a second agent loop. See the [Cuti Video Harness reference](docs/video-agent-harness.md) for the architecture in detail.

Planning follows Cuti V2's continuous `PlanPatch` contract. A Workflow limits the allowed capabilities and creative rules, but it does not precompile the complete production DAG. After each executable task frontier finishes, Video Runtime persists the real Artifacts and queues the same DeepSeek Session. DeepSeek then submits the next `add_tasks` patch, cancels still-pending tasks, or marks the goal satisfied.

<a id="run"></a><a id="run-from-source"></a>

## Clone and run from source (recommended)

This is the release acceptance path for the open-source branch. The setup command prepares dependencies, and the start command runs Video Studio, Cuti Harness, Video Runtime, Media Service, and Sandbox Worker. Docker, PostgreSQL, and Redis are not required.

### Prerequisites

- Git.
- Node.js `22.19+` or `24+`.
- pnpm `11.x`. Run `pnpm --version`; if pnpm is missing, run `corepack enable`.
- Conda (Miniconda, Miniforge, or Anaconda).
- Internet access during environment setup so the setup command can install Python packages and separately licensed FFmpeg/FFprobe tools.

### 1. Clone the open-source branch

```sh
git clone --depth 1 --filter=blob:none --branch deepseek-harness-open --single-branch https://github.com/testcoder-ui/cuti-video-agent.git
cd cuti-video-agent
```

The HTTPS command works anonymously after the repository visibility is set to **Public**. Before public release, an invited collaborator with a configured GitHub SSH key can use:

```sh
git clone --depth 1 --filter=blob:none --branch deepseek-harness-open --single-branch git@github.com:testcoder-ui/cuti-video-agent.git
cd cuti-video-agent
```

The shallow, blob-filtered clone still checks out every file needed to build and run while avoiding unnecessary history transfer. Contributors who later need the complete history can run `git fetch --unshallow`.

### 2. Configure provider keys

macOS or Linux:

```sh
cp config/.env.example .env
```

Windows PowerShell:

```powershell
Copy-Item config/.env.example .env
```

Open `.env` and fill in the keys for the capabilities you intend to use. Never commit this file. The useful minimum for chat plus the default video workflow is:

```dotenv
OPENAI_API_KEY=your-openai-key
WAVESPEED_API_KEY=your-wavespeed-key
```

`OPENAI_API_KEY` powers the Cuti Harness conversation and planning model. For the default video workflow, use `WAVESPEED_API_KEY` or `ARK_API_KEY`. `SUNO_API_KEY` is needed only by music-generating workflows. The UI and health endpoints can start without keys, but the corresponding model or media call will fail when invoked.

### 3. Install and build

Run from the repository root:

```sh
pnpm install --frozen-lockfile
pnpm run build
```

The root build also produces the Video Studio bundle required by the local launcher.

### 4. Prepare the local environment

Create the repository's named Conda environment from the committed environment definition, then activate it. The same commands apply on Windows, macOS, and Linux:

```sh
conda env create --file environment.yml
conda activate cuti-video-agent
pnpm video:setup -- --data-dir .video-agent-harness-data
```

If the environment already exists, synchronize its base Python and pip constraints before rerunning setup:

```sh
conda env update --file environment.yml --prune
conda activate cuti-video-agent
pnpm video:setup -- --data-dir .video-agent-harness-data
```

The Conda definition owns the environment name, Python 3.11, and pip. The setup command verifies that this exact Conda environment is active, installs the pinned Python service dependencies into it, and installs FFmpeg/FFprobe under `.video-agent-harness-data`. It does not start any service. Run it again after the Python requirements change or when using a new data directory.

To let setup download a checksum-verified portable Python instead of using an existing Python environment, pass `--portable-python`. This is an explicit alternative, not the default:

```sh
pnpm video:setup -- --portable-python --data-dir .video-agent-harness-data
```

### 5. Start the complete local stack

Keep the `cuti-video-agent` Conda environment activated, then run:

```sh
pnpm video:local -- --data-dir .video-agent-harness-data
```

The start command only checks the prepared environment and starts services; it does not download Python or install dependencies. Keep this terminal open. The repository-local data directory is ignored by Git and avoids cross-volume rename errors on some Windows installations.

When using the optional portable environment, pass the same option to start:

```sh
pnpm video:local -- --portable-python --data-dir .video-agent-harness-data
```

When the terminal prints `Cuti Video Harness is ready`, open [http://127.0.0.1:3000/#/zh/create](http://127.0.0.1:3000/#/zh/create). Video Studio has no login flow; local project identity is `local-user`.

### 6. Verify the running stack

In a second terminal, run `conda activate cuti-video-agent`, then run from the repository root:

```sh
pnpm video:doctor -- --data-dir .video-agent-harness-data
curl http://127.0.0.1:8001/health
```

Doctor should report every component as `OK`; the health response should be `{"status":"healthy"}`. Port `3000` is Video Studio, `3080` is Cuti Harness, `8001` is Video Runtime, `8090` is Sandbox Worker, and `18080` is Media Service.

Portable-environment users also pass `--portable-python` to `video:doctor`.

Stop the stack with `Ctrl+C` in its terminal. Local npm mode is intended for a trusted single-user machine: its subprocess worker is not a security isolation boundary. Use Docker Compose deployment for PostgreSQL, multi-user operation, or untrusted executable plugins.

Try a low-cost first prompt such as:

> Create a 10-second, two-shot cinematic video of a robot watering one flower at sunrise. Use the same robot in both shots, no narration, and export the final MP4.

## Development mode

Tool configuration sources live in `config/root/`. `pnpm install` generates the Git-ignored root entrypoints required by TypeScript, editors, tests, and Git hooks. Edit the source files there, and run `node scripts/materialize-root-configs.mjs` after pulling configuration updates or when installing with `--ignore-scripts`.

To run Video Studio with Vite hot reload, keep the complete local stack running, then use another terminal:

```sh
cd apps/video-studio
cp ../../config/.env.example .env.local
```

Set these values in `apps/video-studio/.env.local`:

```dotenv
VITE_VIDEO_RUNTIME_URL=http://127.0.0.1:8001
VITE_VIDEOCHAT_URL=http://127.0.0.1:8001
VITE_CUTI_BACKEND_URL=http://127.0.0.1:8001
VITE_BACKEND_URL=http://127.0.0.1:8001
```

Studio has no login flow. Project identity is `local-user` in Video Runtime.

Return to the repository root and start Vite:

```sh
pnpm --filter @cuti-ai/video-studio run dev
```

Open [http://127.0.0.1:5173/#/zh/create](http://127.0.0.1:5173/#/zh/create).

## Common problems

| Symptom | Check |
| --- | --- |
| The UI opens but sending a prompt has no response | Confirm Cuti Harness is still running on port `3080` and `OPENAI_API_KEY` is present in the repository `.env`. |
| `401`, `NO_AUTH`, or model authentication error | Check `OPENAI_API_KEY` in `.env`, then restart the local stack so the launcher reloads it. |
| Video or image generation fails | Configure the provider key required by the selected workflow; the default Seedance path needs `WAVESPEED_API_KEY` or `ARK_API_KEY`. |
| Build remains queued or the Runtime is unavailable | Run `pnpm video:doctor -- --data-dir .video-agent-harness-data` and inspect the launcher terminal. |
| Port is already in use | Free or remap ports `3000`, `3080`, `8001`, `8090`, or `18080`. Keep URLs and proxy settings consistent. |
| `EXDEV` or `cross-device` appears during first-run installation | Use the documented `--data-dir .video-agent-harness-data` command and do not point the data directory at another drive. |
| Model, provider, or first-run dependency download times out behind a proxy | Export `HTTP_PROXY`, `HTTPS_PROXY`, and `NODE_USE_ENV_PROXY=1` in the launching shell, not in `.env`, then restart the stack. The launcher detects an enabled Windows user proxy automatically. |

## Tests

```sh
pnpm --filter @cuti-ai/video-studio run build
python -m unittest discover -s services/video-runtime/tests/video_runtime -v
```

The repository also retains the broader DeepSeek Harness checks. See [development](docs/development.md) and [contributing](CONTRIBUTING.md).

## Project status

This project is in developer preview and can introduce compatibility-breaking changes. Provider calls may incur real costs. Start with short videos and low-cost test prompts.

## License

[MIT](LICENSE). DeepSeek and imported Cuti provenance is documented in [source provenance](docs/source-provenance.md). Third-party dependencies and licenses are listed in [THIRD_PARTY_NOTICES.md](guides/THIRD_PARTY_NOTICES.md).
