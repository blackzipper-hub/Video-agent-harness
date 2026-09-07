# `@cuti-ai/video-agent-harness`

Portable local launcher for Video Agent Harness. It starts DeepSeek Harness, the Python Video Runtime, Media Service, local Sandbox Worker, and Video Studio without Docker. Project state and generated media live under one user data directory.

```sh
npx @cuti-ai/video-agent-harness web
```

The first run downloads a pinned, checksum-verified Python 3.11 distribution and installs the Python service dependencies. It also installs separately licensed FFmpeg and FFprobe tools in the user-data directory instead of redistributing those binaries in this package. Later starts reuse the local installation.

Use `--use-system-python` to use an existing Python 3.11 interpreter. This mode is intended for repository development:

```sh
pnpm video:local -- --use-system-python
```

Run diagnostics without starting services:

```sh
npx @cuti-ai/video-agent-harness doctor
```

## Known Limitations and Deferred Work

Local Sandbox execution runs as an ordinary child process and is not a security boundary. Use the Docker deployment when executing untrusted third-party plugins. The portable npm release supports Windows x64/arm64, macOS x64/arm64, and glibc Linux x64/arm64; other platforms require `--use-system-python`.
