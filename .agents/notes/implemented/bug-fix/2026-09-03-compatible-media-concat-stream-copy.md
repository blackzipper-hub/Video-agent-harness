# Agent Note: Compatible media segments use stream-copy concatenation

Status: implemented

English | [中文](2026-09-03-compatible-media-concat-stream-copy.zh.md)

## Problem

The media service treated only H.264 `yuv420p` segments as safe for stream-copy concatenation. Seedance emits matching HEVC Main10 segments, so a request to normalize four already-compatible clips started a full H.264 transcode and could exhaust the media client's timeout after every expensive provider step had completed.

## Decision

Zero-transition concatenation probes the video and audio stream parameters of every input. Exact matches use the FFmpeg concat demuxer with `-c copy` even when the caller requests normalization, because normalization is a compatibility guarantee rather than a demand to re-encode. Codec, pixel format, dimensions, frame rate, audio presence, audio codec, sample rate, channels, channel layout, and audio time base participate in the decision.

Incompatible streams and transitions use the existing normalized transcode. The post-concat duration check forces that transcode path if stream copy produces invalid timestamps, without reconsidering the fast path recursively.

## Verification

The media-service concat suite covers matching HEVC Main10 video with AAC audio and asserts that normalization selects `-c copy`. Four persisted Seedance segments from the failed build concatenate into a decodable 60.28-second HEVC video with AAC audio.

## Alternatives considered

**Always normalize to H.264.** Rejected because identical production segments pay a large latency and CPU cost without gaining compatibility.

**Keep the H.264-only allowlist.** Rejected because stream compatibility depends on matching parameters, not one preferred codec.

**Copy every input without inspection.** Rejected because codec, video, or audio mismatches can create corrupt output or timestamp discontinuities.

## Consequences

Compatible Seedance builds complete concatenation in seconds without generation loss. Mixed media still incurs transcoding, and every direct-copy candidate performs metadata probes before concatenation.
