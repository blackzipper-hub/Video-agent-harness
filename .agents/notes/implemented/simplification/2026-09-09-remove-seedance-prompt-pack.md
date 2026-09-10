# Agent Note: Remove the Seedance Prompt Pack

Status: implemented

English | [中文](2026-09-09-remove-seedance-prompt-pack.zh.md)

## Problem

The bundled `seedance-20` prompt pack contributed one parent Skill and 28 nested helper Skills to the catalog even though Video Runtime workflows do not depend on them. Keeping the unused package enlarged repository checkouts and presented unsupported choices to users.

## Decision

The repository excludes the complete `seedance-20` package and its nested Skills. Skill discovery has no compatibility alias or hidden catalog entry for the removed package. Executable video generation continues through the independent `seedance2` Workflow Skill and its Runtime capabilities.

## Alternatives considered

**Hide the Skills only in Video Studio.** Rejected because Runtime discovery and model-visible catalogs would still expose unused instructions while the repository retained all package files.

**Keep the package as an optional built-in dependency.** Rejected because no shipped Workflow requires it; independently useful prompt guidance can be installed as a separately maintained Skill.

## Consequences

Repository checkouts omit 268 unused files and fresh Runtime catalogs omit 29 Skill entries. Existing projects that explicitly locked one of the removed Skill IDs must choose an installed replacement before further planning. The package can return only as an independently tested dependency with a current Runtime use case.
