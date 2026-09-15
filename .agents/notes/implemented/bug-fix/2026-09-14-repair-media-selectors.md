# Agent Note: Repair media task selectors

Status: implemented

English | [中文](2026-09-14-repair-media-selectors.zh.md)

## Problem

Cloning a pending descendant after an upstream task fails can update scheduling dependencies while leaving media parameters pointed at the failed task. The descendant then starts successfully but cannot locate its required output.

## Decision

Repair applies the complete replacement mapping to declared task selectors after all descendants are cloned. Scalar and ordered-list selectors share a definition with plan admission. Admission rejects unavailable task ids and derives scheduling dependencies from selectors. Historical plans and non-selector parameters remain unchanged.

## Alternatives considered

**Replace every matching string.** Rejected because prompts, URLs, and artifact ids are not task references.

**Ask the model to repair every downstream parameter.** Rejected because Runtime owns automatic descendant cloning and must preserve executable input references itself.

## Consequences

Tests reproduce the failed lookup with old parameters and pass repaired parameters through the real media plugin, mocking only the media service call. Coverage includes selector kinds, ordering, transitive descendants, unchanged history, and rejection before plan persistence. Existing failed builds require explicit repair; this change does not resubmit paid generation jobs or fix model-provider credit exhaustion.
