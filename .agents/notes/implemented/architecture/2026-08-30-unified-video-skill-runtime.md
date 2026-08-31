# Agent Note: Video builds resolve every Skill through one runtime

Status: implemented

English | [中文](2026-08-30-unified-video-skill-runtime.zh.md)

## Problem

Cuti contained two materially different Skill paths. Its V2 runtime discovered and validated metadata, executable contracts, Workflow declarations, dependencies, selectors, trust, and installation state through `SkillCatalog`, while older media helpers read a named `SKILL.md` directly from a fixed stage directory. A migrated Build could therefore compile a Workflow through the catalog but execute nested prompts from mutable, unversioned files outside the persisted plan.

## Decision

The Python process owns one `VideoSkillRuntime`. It contains the complete imported Cuti Skill roots, one `SkillCatalog`, one Capability Registry, one Workflow Registry, and one agent-independent `SkillResolver`. DeepSeek supplies the structured `VideoSpec`; Workflow Plugins compile deterministic `BuildPlan` steps; the resolver applies Workflow dependencies, explicitly activated Skills, project locks, declared Director Skills, and Capability-bound Skills to those steps.

Each persisted step freezes resolved Skill identity, declared version, full-file hash, roles, hooks, instructions, and any constraint classification before execution. Provider prompts consume the frozen context and generated artifacts retain the Skill provenance. Required missing or disabled Skills fail at resolution. Imported media helpers that still need a focused LLM prompt load the named Skill through the process-owned catalog; the fixed-directory stage reader does not exist.

The migrated Cuti Skill UI reads the BFF catalog directly from `VideoSkillRuntime.catalog`. A one-turn Workflow selection is sent as `workflow_id`; Style and helper selections are sent as `activated_skill_ids`; long-lived enablement is stored as `ProjectSkillLock` in the Video Runtime repository, never on an Agent Run. The existing safe ZIP extraction and validation path installs into the Runtime external root and then reloads the catalog, Capability Registry, Workflow Registry, and generated Workflow Plugin. Build progress exposes each persisted `BuildStep.resolved_skills` without exposing private model reasoning.

Product startup accepts only the DeepSeek backend and does not mount the imported Cuti planner as a second agent loop. The imported coordinator and task runtime remain implementation dependencies for Cuti provider compatibility, but they share the same resolver types and do not form an alternative product entry.

## Verification

Focused Python tests cover selector matching, Workflow supervisor and dependency application, frozen-context serialization, BuildStep persistence, Workflow discovery, provider prompt injection, artifact provenance, initial planning, API planning through installed Workflow Plugins, catalog-backed BFF listing, structured selection, project lock persistence, and ZIP install/reload. A direct prompt-helper check proves catalog resolution returns an enabled Director Skill. Migration-owned frontend files pass focused ESLint.

## Alternatives considered

**Add a small Video Runtime-only Director resolver.** Rejected because it would duplicate Cuti's catalog, dependency, selector, install, and trust semantics and would drift as Skills evolved.

**Keep direct stage-directory reads inside imported media services.** Rejected because they bypass enabled state, configured roots, version identity, and persisted Build provenance.

**Restore the Cuti coordinator for Workflow Skills.** Rejected because DeepSeek already owns model planning; a second loop would split responsibility for intent and recovery.

## Consequences

Instruction, Workflow, Director, and executable Skill discovery share one source of truth, and a resumed Build uses the Skill context it originally persisted. Skill changes affect newly planned work without silently changing an in-flight Build. Project defaults survive across DeepSeek Sessions, while explicit structured selection can override them for one Build. The runtime initializes a larger catalog once per process, and a disabled Skill that older code silently ignored now stops the affected operation with an explicit error.
