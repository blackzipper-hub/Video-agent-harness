# Agent Note: Separate Local Environment Setup from Startup

Status: implemented

English | [中文](2026-09-12-explicit-local-environment-setup.zh.md)

## Problem

The local start command downloaded a Python distribution and installed Python and media dependencies when its data directory was empty. A command described as startup therefore mutated the development environment, required network access, and repeated substantial setup for each new checkout-local data directory.

## Decision

Local source and npm launches require the named `cuti-video-agent` Conda environment with Python 3.11 by default. The repository commits `environment.yml` to create that base environment consistently. The explicit `setup` command verifies the active Conda identity, installs the pinned Python service requirements into it, installs FFmpeg and FFprobe in the selected data directory, and records both the requirements revision and interpreter path there. The `web` command only accepts the same active environment with current recorded requirements and a data directory whose media tools are present; otherwise it exits with a setup instruction before starting services.

Portable Python remains an opt-in path. `setup --portable-python` downloads and prepares the pinned distribution, and `web --portable-python` selects that prepared interpreter. Startup never downloads the portable distribution, even when the option is present.

The default resolver uses `CONDA_PREFIX` only when `CONDA_DEFAULT_ENV` is `cuti-video-agent`, resolves Python directly inside that prefix, and verifies Python 3.11. It does not fall back to an unrelated virtual environment or a global interpreter on `PATH`.

## Alternatives considered

**Keep automatic setup in the start command and document the download.** Rejected because documentation would make the mutation visible but would not separate repeatable environment preparation from service lifecycle operations.

**Require contributors to run each pip and npm dependency command manually.** Rejected because the launcher already owns the exact aggregated Python requirements and FFmpeg package versions. One explicit setup command keeps those inputs consistent without hiding when installation occurs.

**Remove portable Python support.** Rejected because it remains useful on supported platforms when a user deliberately chooses a self-contained environment. Making it opt-in removes the surprise without removing the capability.

## Consequences

A new checkout creates the environment from `environment.yml`, activates `cuti-video-agent`, and runs one setup command before startup. Requirements changes and new data directories require setup again. Each new terminal activates the same named environment before startup. Startup works without installation side effects or network access after preparation, while portable users pass the same explicit option to both setup and startup.

## Verification

CLI parsing tests assert that Conda is the default and portable Python requires an explicit setup option. A local-runtime regression test verifies that startup rejects a missing named Conda environment, while another starts against an empty portable data directory, checks the setup diagnostic, and verifies that startup writes no download or installation files. The launcher tests can run without Conda installed; a real Conda environment smoke remains an integration check on a Conda-equipped host.
