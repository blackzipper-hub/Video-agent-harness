# Agent Note: Studio type-aware checks

Status: implemented

English | [中文](2026-09-09-studio-type-aware-checks.zh.md)

## Problem

Imported Studio code uses heterogeneous API payloads and optional media versions. An unsupported TypeScript configuration option prevents type-aware lint from reporting these accesses reliably. Blindly deleting null checks can turn empty conversations or out-of-range media selections into render failures.

## Decision

Studio enables strict null and indexed-access checking, models chat events and media records explicitly, and retains runtime validation at storage and HTTP entry points. Array accesses without a guaranteed element remain guarded. Asynchronous callbacks retain cancellation checks after awaiting external work. Fetch headers are merged through `Headers`, and failed canvas encoding rejects export instead of producing a null image result.

## Alternatives considered

**Bypass lint or suppress imported code.** Rejected because it leaves malformed data and asynchronous failures unresolved and prevents contributors from reproducing a trustworthy check result.

**Remove every apparently redundant guard.** Rejected because index signatures and asynchronous mutation can make static assumptions stronger than actual browser state. Indexed-access checking and explicit post-await state reads preserve the necessary guards.

## Consequences

Frontend fixes require concrete event types and empty-state handling rather than permissive payload casts. Focused tests cover storage records, header representations, structured output, and canvas failure. Production builds validate bundling; Provider availability and live media quality remain separate acceptance concerns. DeepSeek agent-loop behavior and Workflow compilation are unchanged by this repair.
