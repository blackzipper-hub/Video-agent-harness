# Cuti Video Agent Harness

English | [中文](README.zh.md)

Cuti Video Agent Harness is an open-source, project-oriented video agent harness built on [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness). It combines DeepSeek's conversation and tool-selection runtime with Cuti's video project, timeline, media workflow, and artifact runtime.

It uses DeepSeek Harness's Cordis plugin architecture and adds an incremental media build model: a change invalidates only affected artifacts, retains reusable outputs, validates continuity, and commits one immutable project version.

## Video architecture

DeepSeek Harness owns sessions, prompts, the agent loop, and high-level tool selection. The Python Video Runtime is the authoritative store for projects, artifact dependencies, builds, timelines, validation results, and project versions. Video Studio remains the video-native frontend.

The integration adds `@cuti-ai/video-runtime`, `@cuti-ai/video-runtime-http`, `@cuti-ai/tool-video`, and `@cuti-ai/video-agent-bundle` without changing the DeepSeek `agent-loop`. See the [Video Agent Harness reference](docs/video-agent-harness.md).

## Local video stack

Start Postgres, the incremental Video Runtime, the isolated Sandbox Worker, and Video Studio:

```sh
docker compose -f compose.video.yml up --build
```

Video Studio is available at `http://127.0.0.1:3000/video`, and the Video Runtime is available at `http://127.0.0.1:8001`. Start DeepSeek Web from the checkout with the video bundle when conversational control is required:

```sh
pnpm install
pnpm run build
VIDEO_RUNTIME_URL=http://127.0.0.1:8001 VIDEO_RUNTIME_SERVICE_TOKEN=video-harness-runtime-local pnpm dsh web --patch packages/bundle/video-agent/cordis.patch.yml
```

## Developer preview

DeepSeek Harness is currently in _developer preview_ and is iterating rapidly. **THERE WILL BE COMPATIBILITY-BREAKING CHANGES.**

## Run

### Run from `npm`

Install `Node.js`, then run:

```sh
npx @deepseek-ai/dsh web
```

The command starts the Web UI at `http://127.0.0.1:3080` by default and opens it in the default browser for a local launch. An SSH launch only prints the host URL because the SSH client or editor owns the local forwarded address. Pass `--no-open` to run the server without opening a browser. See [Web UI guide](docs/user/guide/index.md).

### Run from source

To run from a repository checkout:

```sh
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness
pnpm install
pnpm run build
pnpm dsh web
```

`pnpm run build` prepares the repository artifacts. `pnpm dsh web` uses those built artifacts without rebuilding.

## Community and support

- Feel free to submit feedback or bug reports through [GitHub Discussions](https://github.com/deepseek-ai/deepseek-harness/discussions).
- Add the [`dsh-plugin`](https://github.com/topics/dsh-plugin) topic to your plugin repository for discoverability.
- Join <a href="https://discord.gg/Ycq5dCaS4">DeepSeek Harness Discord community</a>.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Development

Start with the [development guide](docs/development.md) and [architecture documentation](docs/architecture.md).

For agents, follow [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)

Third-party dependencies and their licenses are disclosed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
