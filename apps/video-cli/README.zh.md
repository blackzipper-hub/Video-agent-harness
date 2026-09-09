# `@cuti-ai/video-agent-harness`

[English](README.md) | 中文

Video Agent Harness 的便携式本地启动器。它不依赖 Docker 即可启动 DeepSeek Harness、Python Video Runtime、Media Service、本地 Sandbox Worker 和 Video Studio。项目状态与生成媒体保存在同一个用户数据目录中。

```sh
npx @cuti-ai/video-agent-harness web
```

首次运行会下载固定版本、经过校验和验证的 Python 3.11 发行版，并安装 Python 服务依赖。它还会在用户数据目录安装单独授权的 FFmpeg 和 FFprobe，而不是在此包中重新分发这些二进制文件。后续启动会复用本地安装。

使用 `--use-system-python` 复用已有的 Python 3.11 解释器。该模式面向仓库开发：

```sh
pnpm video:local -- --use-system-python
```

不启动服务，仅运行诊断：

```sh
npx @cuti-ai/video-agent-harness doctor
```

## Known Limitations and Deferred Work

本地 Sandbox 作为普通子进程运行，不构成安全边界。执行不受信任的第三方插件时，请使用 Docker 部署。便携式 npm 发行版支持 Windows x64/arm64、macOS x64/arm64 和 glibc Linux x64/arm64；其他平台需要使用 `--use-system-python`。
