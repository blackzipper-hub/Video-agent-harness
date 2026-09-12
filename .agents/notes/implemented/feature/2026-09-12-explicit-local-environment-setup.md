# Agent Note: Separate Local Environment Setup from Startup

Status: implemented

English | [中文](2026-09-12-explicit-local-environment-setup.zh.md)

## Problem

The local start command downloaded a Python distribution and installed Python and media dependencies when its data directory was empty. A command described as startup therefore mutated the development environment, required network access, and repeated substantial setup for each new checkout-local data directory.

## Decision

Local source and npm launches use an existing Python 3.11 environment by default. The explicit `setup` command installs the pinned Python service requirements into that environment, installs FFmpeg and FFprobe in the selected data directory, and records the requirements revision there. The `web` command only accepts a Python environment whose recorded requirements are current and a data directory whose media tools are present; otherwise it exits with a setup instruction before starting services.

Portable Python remains an opt-in path. `setup --portable-python` downloads and prepares the pinned distribution, and `web --portable-python` selects that prepared interpreter. Startup never downloads the portable distribution, even when the option is present.

The Python resolver prioritizes `VIDEO_AGENT_PYTHON`, then an activated virtual environment, then compatible Python commands on `PATH`. Every selected interpreter must report Python 3.11.

## Alternatives considered

**Keep automatic setup in the start command and document the download.** Rejected because documentation would make the mutation visible but would not separate repeatable environment preparation from service lifecycle operations.

**Require contributors to run each pip and npm dependency command manually.** Rejected because the launcher already owns the exact aggregated Python requirements and FFmpeg package versions. One explicit setup command keeps those inputs consistent without hiding when installation occurs.

**Remove portable Python support.** Rejected because it remains useful on supported platforms when a user deliberately chooses a self-contained environment. Making it opt-in removes the surprise without removing the capability.

## Consequences

A new checkout requires Python 3.11 environment activation followed by one setup command before startup. Requirements changes and new data directories require setup again. Startup works without installation side effects or network access after preparation, while portable users pass the same explicit option to both setup and startup.

## Verification

CLI parsing tests assert that configured Python is the default and portable Python requires an explicit setup option. A local-runtime regression test starts against an empty portable data directory, checks the setup diagnostic, and verifies that startup writes no download or installation files. An isolated Python 3.11 virtual-environment smoke run prepared the environment and confirmed through `doctor` that the interpreter, service requirements, media tools, service sources, and Studio build were ready.
