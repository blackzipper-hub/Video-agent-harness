# Agent Note: Portable npm local runtime

Status: implemented

English | [中文](2026-09-08-portable-local-runtime.zh.md)

## Problem

The video product previously required Docker Compose for PostgreSQL, Python services, FFmpeg, sandbox execution, and process supervision. That made the first-run experience much heavier than upstream DeepSeek Harness, whose web product can start from an npm CLI.

## Decision

Publish `@cuti-ai/video-agent-harness` as the product CLI. It supervises DeepSeek Harness, Video Runtime, Media Service, Sandbox Worker, and the built Video Studio in one foreground process. The package installs a checksum-pinned portable CPython distribution on first use and keeps isolated Python dependencies in the user data directory. It also asks npm to install separately licensed FFmpeg and FFprobe tools into that directory at first run instead of redistributing those GPL-enabled binaries in this MIT package.

Local projects and build state use the existing crash-safe `LocalJsonVideoProjectRepository`; generated media and sandbox workspaces also live under the per-user data directory. The CLI exposes only one Video Studio port and proxies product API paths to Video Runtime. Docker Compose remains available for production, multi-user, PostgreSQL, and untrusted-plugin isolation.

## Security boundary

The npm local mode is a trusted single-user development/runtime mode. Its Sandbox Worker runs child processes with limits and auditing but is not an isolation boundary. Untrusted executable plugins must use the Docker deployment or another hardened Sandbox Worker.

## Consequences

Users can run the product with `npx @cuti-ai/video-agent-harness web` and do not need Docker, system Python, PostgreSQL, or system FFmpeg. First start requires downloading Python and installing Python wheels. Production deployment retains the existing Docker and PostgreSQL path.
