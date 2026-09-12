# Agent Note: Keep One Harness Loop

Status: implemented

English | [中文](2026-09-11-single-harness-loop.zh.md)

## Problem

Video Runtime exposed a second family of agent loops behind reference research and visual-consistency checks. Those paths could start their own planner while the DeepSeek Harness already owned planning, context management, and tool selection. Leaf provider tools also imported the second framework only for decorators, message containers, retries, and structured output.

## Decision

The DeepSeek Harness is the only agent loop. Reference research runs as direct `web_search` work in the active Harness context and has no schedulable `research.generate` capability. Image and video providers return successful generations without starting an LLM consistency loop. Build validation keeps deterministic media checks, and optional reference-pixel validation is one direct OpenAI request rather than an agent loop.

Video Runtime uses its own small async-tool wrapper for provider metadata and explicit runtime context. Atomic text generation calls the official OpenAI SDK, and audio transcription calls the official Google GenAI SDK with a Pydantic response schema. The Runtime dependency manifest and production imports exclude the retired agent-framework packages.

The source launcher builds Video Studio as part of the root build. Its explicit environment setup command installs the standalone LangSmith observability client with the Python service requirements, while startup only validates that prepared environment. Legacy Redis cancellation and rate-limit adapters load only when those optional paths are used. The repository dotenv template excludes process-launch proxy settings that DeepSeek Harness intentionally accepts only from the shell. Local single-user startup therefore does not require a Redis package or server.

## Alternatives considered

**Keep the secondary loops but hide their capability IDs.** Rejected because image and video wrappers could still start them implicitly, so behavior would depend on a hidden planner and its transitive dependencies.

**Retain the framework as a generic tool-decorator dependency.** Rejected because provider execution needs only a name, schema metadata, an async callable, and an explicit context carrier. A local wrapper makes that boundary visible and prevents another agent runtime from returning through a leaf dependency.

**Remove reference research entirely.** Rejected because current information can improve creative direction. The active Harness can search directly without persisting an intermediate capability artifact or delegating planning.

## Consequences

Runs have one owner for planning and context compression. Existing plans that name `research.generate` must be replanned against the current capability catalog. Image and video generation retain bounded provider retry and fallback but no longer spend additional model calls judging consistency or rewriting prompts. The removed structured-output recovery utilities are unavailable to new provider code; new leaf integrations use official provider SDKs or deterministic logic.

Source users run the documented root build, prepare a declared Python 3.11 environment with `pnpm video:setup`, and then start with `pnpm video:local`. Startup performs no dependency installation.

## Verification

Runtime tests parse every production Python file and fail if a retired framework package is imported. A second check rejects those packages in `pyproject.toml` and rejects `research.generate` from Runtime code, plugin manifests, and Skills. Atomic execution, MV workflow alignment, plugin registration, tool-wrapper fallback, and direct tool metadata tests cover the remaining paths.

The local launcher was smoke-tested through Media Service, Sandbox Worker, Video Runtime, DeepSeek Harness, the production Studio gateway, and the Vite development proxy. Provider wrappers import successfully with no Redis package installed, and anonymous local project creation succeeds through the Studio proxy.
