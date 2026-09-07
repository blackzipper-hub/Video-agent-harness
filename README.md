# Video Agent Harness

English | [中文](README.zh.md)

Video Agent Harness is an open-source, project-oriented video agent harness built on [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness). DeepSeek owns the conversation, agent loop, and high-level tool selection. Cuti's Video Runtime owns durable projects, Skills, artifact dependencies, incremental builds, timelines, validation, and exports.

The primary product UI is Video Studio. Its `/create/:threadId` workspace keeps the DeepSeek chat on the left and generated scripts, images, clips, audio, build progress, and final video on the right.

## Architecture

```text
Video Studio (:3000)
  |-- /chat-v1 and /api/video
  v
Video Runtime + compatibility BFF (:8001)
  |-- projects, Skills, artifacts, builds, versions
  |-- media service and sandbox worker
  v
DeepSeek Harness (:3080)
  |-- LLM conversation and video_* tool selection
  v
Provider / Workflow / Validator / Media plugins
```

The integration adds `@cuti-ai/video-runtime`, `@cuti-ai/video-runtime-http`, `@cuti-ai/tool-video`, and `@cuti-ai/video-agent-bundle` without adding a second agent loop. See the [Video Agent Harness reference](docs/video-agent-harness.md) for the architecture in detail.

Planning follows Cuti V2's continuous `PlanPatch` contract. A Workflow limits the
allowed capabilities and creative rules, but it does not precompile the complete
production DAG. After each executable task frontier finishes, Video Runtime persists
the real Artifacts and queues the same DeepSeek Session. DeepSeek then submits the
next `add_tasks` patch, cancels still-pending tasks, or marks the goal satisfied.

## Run

### Run from source

#### Prerequisites

- Git.
- Docker Desktop or Docker Engine with Docker Compose v2. Use Linux containers on Windows.
- Node.js `22.19+` (or `24+`) and Corepack.
- At least one OpenAI API key for conversation and planning.

For a real default video build, also configure either `WAVESPEED_API_KEY` or `ARK_API_KEY`. `SUNO_API_KEY` is needed only by workflows that generate music. Other provider keys are optional.

#### 1. Clone and configure

```sh
git clone https://github.com/blackzipper-hub/Video-agent-harness.git
cd Video-agent-harness
cp .env.example .env
```

On Windows PowerShell, use `Copy-Item .env.example .env` instead of `cp`. Open `.env` and fill in your keys. Never commit that file.

Minimum useful configuration:

```dotenv
OPENAI_API_KEY=your-openai-key
WAVESPEED_API_KEY=your-wavespeed-key
```

The UI and local APIs can start without paid-provider keys, but chat or media generation will fail only when the corresponding capability is invoked.

#### 2. Start the video services

```sh
docker compose --env-file .env -f compose.video.yml up --build -d
docker compose -f compose.video.yml ps
```

This starts Postgres, applies database migrations, and starts Video Runtime, Media Service, Sandbox Worker, and Video Studio. Wait until `video-runtime`, `media-service`, and `sandbox-worker` are healthy.

Verify the Runtime:

```sh
curl http://127.0.0.1:8001/health
```

The expected response is `{"status":"healthy"}`.

#### 3. Build and start DeepSeek Harness

Run this once after cloning:

```sh
corepack enable
pnpm install --frozen-lockfile
pnpm run build
```

The DeepSeek process must receive the OpenAI key from the shell. On macOS or Linux:

```sh
export OPENAI_API_KEY="your-openai-key"
export VIDEO_AGENT_MODEL="gpt-5.6-terra"
export VIDEO_RUNTIME_URL="http://127.0.0.1:8001"
export VIDEO_RUNTIME_SERVICE_TOKEN="video-harness-runtime-local"
pnpm dsh --profile web --patch packages/bundle/video-agent/cordis.patch.yml --no-open
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
# Fill in OPENAI_API_KEY and the required media provider keys in .env
.\scripts\start-video-harness.ps1
```

The Windows launcher reads the repository `.env`. In a migration workspace without that file, it can temporarily reuse keys from the sibling `cuti-video-agent/.env`. It also passes an enabled Windows user proxy to Node so model requests do not time out while browser networking still works.

Keep this terminal running. DeepSeek Harness listens at `http://127.0.0.1:3080` by default.

#### 4. Open the product

Open [http://127.0.0.1:3000/#/zh/create](http://127.0.0.1:3000/#/zh/create).

Video Studio at port `3000` is the main video creation and editing UI. Port `3080` exposes the generic DeepSeek Harness UI, and port `8001` exposes the Runtime API.

Try a low-cost first prompt such as:

> Create a 10-second, two-shot cinematic video of a robot watering one flower at sunrise. Use the same robot in both shots, no narration, and export the final MP4.

## Development mode

To run Video Studio with Vite hot reload, keep the Docker services and DeepSeek Harness running, then use a third terminal:

```sh
cd apps/video-studio
cp .env.example .env.local
```

Set these values in `apps/video-studio/.env.local`:

```dotenv
VITE_LOCAL_SINGLE_USER_MODE=true
VITE_VIDEO_RUNTIME_URL=http://127.0.0.1:8001
VITE_VIDEOCHAT_URL=http://127.0.0.1:8001
VITE_CUTI_BACKEND_URL=http://127.0.0.1:8001
VITE_BACKEND_URL=http://127.0.0.1:8001
```

Keep local login off. `VITE_LOCAL_SINGLE_USER_MODE=true` uses a fake `local@cuti.dev` user and does not call Go. Dev / cluster images build this flag as `false`.

Return to the repository root and start Vite:

```sh
pnpm --filter @cuti-ai/video-studio run dev
```

Open [http://127.0.0.1:5173/#/zh/create](http://127.0.0.1:5173/#/zh/create).

## Common problems

| Symptom | Check |
| --- | --- |
| The UI opens but sending a prompt has no response | Confirm DeepSeek Harness is still running on port `3080` and `OPENAI_API_KEY` is set in that terminal. |
| `401`, `NO_AUTH`, or model authentication error | The `.env` file is used by Docker, but is not automatically loaded into the DeepSeek terminal. Export `OPENAI_API_KEY` there. |
| Video or image generation fails | Configure the provider key required by the selected workflow; the default Seedance path needs `WAVESPEED_API_KEY` or `ARK_API_KEY`. |
| Build remains queued or the Runtime is unavailable | Run `docker compose -f compose.video.yml ps` and inspect `docker compose -f compose.video.yml logs video-runtime`. |
| Port is already in use | Free or remap ports `3000`, `3080`, `8001`, `8090`, or `18080`. Keep URLs and proxy settings consistent. |
| Sandbox Worker is unhealthy on Windows | Ensure Docker Desktop is using Linux containers and allows access to the Docker socket. |
| Model or provider calls time out behind a proxy | Set `HTTP_PROXY` and `HTTPS_PROXY` before starting the stack, plus `NODE_USE_ENV_PROXY=1` for Node. Compose passes the settings consistently to Video Runtime, Media Service, and DeepSeek Harness. |

Stop the Docker stack with:

```sh
docker compose -f compose.video.yml down
```

Add `-v` only when you intentionally want to delete the local Postgres and generated-media volumes.

## Tests

```sh
pnpm --filter @cuti-ai/video-studio run build
python -m unittest discover -s services/video-runtime/tests/video_runtime -v
```

The repository also retains the broader DeepSeek Harness checks. See [development](docs/development.md) and [contributing](CONTRIBUTING.md).

## Project status

This project is in developer preview and can introduce compatibility-breaking changes. Provider calls may incur real costs. Start with short videos and low-cost test prompts.

## License

[MIT](LICENSE). DeepSeek and imported Cuti provenance is documented in [source provenance](docs/source-provenance.md). Third-party dependencies and licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
