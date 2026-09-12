# `@cuti-ai/video-agent-harness`

English | [中文](README.zh.md)

Local launcher for Video Agent Harness. It starts DeepSeek Harness, the Python Video Runtime, Media Service, local Sandbox Worker, and Video Studio without Docker. Project state and generated media live under one user data directory.

```sh
npx @cuti-ai/video-agent-harness setup
npx @cuti-ai/video-agent-harness web
```

The default path requires an existing Python 3.11 environment. `setup` installs the pinned Python service dependencies into that environment and installs separately licensed FFmpeg and FFprobe tools in the user-data directory. `web` only verifies the prepared environment and starts services; it does not download Python or install dependencies.

Portable Python remains available as an explicit alternative. Prepare it once and pass the same option when starting:

```sh
npx @cuti-ai/video-agent-harness setup --portable-python
npx @cuti-ai/video-agent-harness web --portable-python
```

Run diagnostics without starting services:

```sh
npx @cuti-ai/video-agent-harness doctor
```

Pass `--portable-python` to diagnostics when that environment was selected during setup.

## Known Limitations and Deferred Work

Local Sandbox execution runs as an ordinary child process and is not a security boundary. Use the Docker deployment when executing untrusted third-party plugins. Optional portable Python supports Windows x64/arm64, macOS x64/arm64, and glibc Linux x64/arm64; other platforms require a configured Python 3.11 environment.
