# Agent Note: Conversation-routed Create entry

Status: implemented

English | [中文](2026-09-12-conversation-routed-create-entry.zh.md)

## Problem

The Create workspace sent every first message through a prompt that required video planning and build tool calls in the same turn. A greeting or general question therefore started production even though the user had not requested video work.

## Decision

The Create workspace marks requests submitted by the home-page generation form as automatic video requests. An explicit Workflow selection has the same meaning. The compatibility backend applies the mandatory plan-and-build prompt only to those requests.

Other first messages receive a conversation-routing prompt with the bound draft project context. The DeepSeek agent answers greetings and general questions directly and calls `video_*` tools only when the visible request asks for video creation, editing, inspection, or export. The draft project remains the durable conversation container and becomes the video project if the agent chooses video work.

## Alternatives considered

**Classify intent with backend keywords.** Rejected because a fixed phrase list cannot reliably distinguish creative requests, questions about video, and ordinary conversation across supported languages. The agent already owns tool selection and has the necessary request context.

**Remove automatic production from every entry.** Rejected because the home-page generation form and an explicit Workflow selection promise one-action production. Those callers carry an explicit `automatic_video` signal instead.

**Delay project creation until the first video tool call.** Rejected because navigation, transcript persistence, uploads, and sidebar state already depend on a bound project. Creating an empty draft has no build side effect and preserves those contracts.

## Consequences

Sending a greeting creates a durable conversation draft but no Video Build. Home-page generation and explicit Workflow requests retain deterministic plan-and-build behavior. Focused compatibility tests pin both routing modes and verify that a greeting remains visible in the transcript without creating a build.
