# Agent Note: Dynamic Workflow Skills

Status: implemented

English | [中文](2026-09-09-dynamic-workflow-skills.zh.md)

## Problem

A name-bound compiler registry prevents independently installed Workflow Skills from reaching the existing continuous PlanPatch executor. An initially empty resolver mapping also loses subsequent catalog updates.

## Decision

Explicit agentic Skills with capability allowlists use continuous PlanPatch execution. The shared admission function serves the Skill catalog, Workflow description, BFF selection, and plan submission. Named Cuti workflows retain metadata checks. The resolver keeps the catalog's mutable mapping even when empty. Declared generated deliverable types support document-only completion without weakening existing video completion rules. An active Build reuses the exact Skill instructions persisted on its accepted plan when files change without a declared version bump; a declared version change remains incompatible and requires an explicit Project upgrade. Workflow-free post-production plans exclude the locked generation Workflow while retaining explicitly declared media Skills and non-Workflow Project locks.

## Alternatives considered

**Generate executable compilers with the model.** Rejected because model decisions belong in validated task data, not process code with ambient permissions.

**Assign every new Skill an existing production compiler.** Rejected because similar metadata does not imply identical creative or dependency semantics.

## Consequences

Dialogue captioning reuses Cuti's transcript-to-template path. Audio recognition excludes screenplay prompts; template rendering ignores authored dialogue markup when a transcript is supplied. Explicit translations preserve one-to-one segment timing, while default captions use source speech. Provider plugins expose video creation as PlanPatch operations, allowing generation and concatenation without another Workflow. Model tools treat the user's edit request as authorization to preview and apply, and identify intermediate builds as work to continue rather than final deliverables.

Subtitle-language parsing no longer spans source-dialogue clauses. Translation follow-ups reach the Agent; genuine language conflicts return an actionable HTTP 422 instead of an unhandled 500. The Studio submission client preserves error details, and generic error labels no longer expose an uninterpolated status placeholder.

Structural edits project creative fields from persisted revisions before VideoSpec validation, excluding execution history. Workflow-free PlanPatch inputs accept redundant URLs only when they match the explicitly referenced project artifact. Transforms can consume earlier operation outputs and create multiple variants of the same source; project version checks and resource ownership remain enforced. This removes formatting and composition obstacles without treating external resource addresses as authorized project inputs.

New agentic Skills need no Runtime source edit but require continuous planning configuration. Runtime validation and signed execution permissions still apply. ZIP installation, reload, instruction loading, task execution, denied capabilities, and document completion have an integrated regression test. Real model selection and paid Provider quality require separate live acceptance; no DeepSeek core loop changes are included.

Live snapshots are caller-owned rather than automatically delivered: delivering a snapshot opened by the Agent feeds its own acknowledgement back into another turn. Empty acknowledgements preserve plan and specification revisions while resolving the checkpoint atomically. Regression coverage verifies repeated acknowledgements stay quiet and a later task completion still delivers a notification.
