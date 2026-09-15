# Agent Note: Session continuation across chat and video checkpoints

Status: implemented

English | [中文](2026-09-12-session-continuation.zh.md)

## Problem

A conversation can finish one turn while more user messages or Runtime planning notifications remain possible. Project modification timestamps do not order these turns: ordinary chat need not modify video state. Closing the browser stream on a turn end hides subsequent responses.

## Decision

Studio subscribes for the selected Session's lifetime and orders hydrated run status by Harness event sequence. Replayed older events cannot replace a newer run projection. A previous turn's terminal state cannot cancel the current message POST; explicit Stop and request ownership control cancellation.

The BFF retains the project's bound Harness Session, rejects mismatched thread ids, and logs durable project/build/checkpoint references with follow-ups. The agent uses Session history and compaction summaries together with current Runtime tool results. Automatic checkpoints retain that Session and identify actual preceding artifact versions. No additional planning loop or conversation store is introduced.

This complements [conversation routing](2026-09-12-conversation-routed-create-entry.md) and [dynamic workflow execution](../architecture/2026-09-09-dynamic-workflow-skills.md); their intent-selection and execution-ownership decisions remain active.

## Alternatives considered

**Only advance project timestamps.** Rejected because this cannot receive automatic continuation after the frontend closes its stream, and project edits are not conversation revisions.

**Create a new agent or checkpoint store per stage.** Rejected because it fragments the user history and duplicates the existing Harness event log and durable Runtime execution records.

## Consequences

Failed-turn projection preserves the provider error message and code. Studio restores the latest failure notice from hydrated events as well as live SSE, so reopening an API-credit failure does not hide its cause. A generic failure label alone loses the actionable provider diagnosis; backend mapping tests and the built-browser transcript cover credit exhaustion, including page reload.

An idle selected conversation retains one reconnecting SSE connection. Both persisted stores are required for restart continuity; a checkpoint does not recreate a deleted Session. Regression tests cover multiple messages on one binding, current artifact references, stale snapshot rejection, delayed POST completion, and an automatic turn arriving without another browser send. Paid media quality is outside these transport and persistence checks.
