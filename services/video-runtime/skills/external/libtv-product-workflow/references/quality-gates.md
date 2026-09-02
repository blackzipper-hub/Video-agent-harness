# Quality gates

## Gate A - Intake readiness

Pass only when at least one usable identity reference, target duration, and aspect ratio exist. Record uncertainty for missing front/back/side/detail coverage.

## Gate B - Identity contract coverage

Verify every applicable protected attribute is covered by the final prompt/task and source evidence. Reject when a style reference is treated as product truth.

## Gate C - Multireference package acceptance

Before video generation verify:

- every image has one explicit semantic role;
- `@图片N` bindings match the persisted ordered URL list;
- complementary product views agree on identity;
- the visible surface required by the shot is evidenced;
- style/setting references cannot redefine product identity;
- no storyboard/keyframe/start-frame artifact is present;
- provider mode is Seedance 2 reference-to-video, never I2V.

Reject when identity coverage is missing or any image is ambiguously treated as a first frame.

## Gate D - Video take acceptance

Inspect the complete take. Reject product melting, morphing, jitter, duplicated parts, implausible scale, corrupted text/logo, unintended occlusion, wrong motion, forbidden effects, unusable boundaries, broken interaction, frozen/black frames, codec failure, or missing required audio. Rank passing takes by identity fidelity, motion integrity, commercial polish, narrative fitness, and editability.

## Gate E - Join acceptance

Inspect the final second before and first second after every boundary. Compare pose, position, scale, camera direction, velocity, lighting, background, and audio phase. Repair by trimming, regenerating the weaker shot, inserting planned B-roll, then using a short justified transition.

## Gate F - Technical normalization

Verify resolution, aspect ratio, frame rate, codec, pixel format, color behavior, audio format, and playable duration. Remove accidental black/frozen frames.

## Gate G - Final master

Watch the complete master with sound. Verify sequence, timing, identity, claims, supplied copy, continuity, pacing, sound synchronization, music balance, safe framing, playback, and durable artifact persistence.

## Retry policy

- Retry transient network/provider/rate-limit failures with bounded backoff and jitter.
- Use only authorized credential fallbacks; otherwise stop and expose required configuration.
- Never resubmit an identical deterministic quality failure.
- Preserve accepted artifacts and restart from the nearest failed stage.
- Record every attempt, token usage, provider cost when available, failure reason, and selected/discarded status.
