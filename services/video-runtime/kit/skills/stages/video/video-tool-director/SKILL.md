---
name: video-tool-director
description: >-
  Call the video generation tool with the given i2v_prompt and images. No craft rewrite.
---

# Video Tool Director

Execute one video generation tool call from Human JSON facts. Do not invent URLs or JSON results.

## Process

1. **Read the Human JSON facts. Call the tool named in `facts.tool_name` once.**
2. Build tool args from facts (see Modes). Only rewrite `i2v_prompt` when regenerating or on retry after failure.
3. On `success=true`: stop. Do not call again.
4. On `success=false`: adjust once or twice (see Retry), then if still failing fill `error_msg` + `raw_error_msg`.

## Modes (branch on facts)

### Regenerate merge
If `facts.user_regenerate_instruction` is non-empty:
- Merge professional base `facts.i2v_prompt` with the user instruction into **one** final `i2v_prompt`.
- Conflicts (costume color, action, etc.): **user instruction wins**.
- I2V first-frame: start image still constrains appearance. If user changes costume color but the start frame has not, the merged prompt must **explicitly** state the desired color and transition from the first frame; otherwise the model keeps the start-frame look.

### Reference-to-video
If `facts.use_reference_to_video` is true:
- Treat `i2v_prompt` as T2V motion brief.
- `reference_images` (`facts.reference_images_count` images) are already in runtime context — **do not** pass `start_image_url` / `end_image_url`.
- Soft refs: camera push-in, head turns, shot-size changes are allowed; do **not** apply hard I2V first-frame cuts to camera moves.
- **Identity only from character refs** (OM): face / hair / costume / prop. Do **not** treat gray studio cyclorama or sheet bg as the scene — place comes from the prompt Environment (and location refs if present). On retry, prefer keeping place+time+light; never “fix” by dropping Environment.

### Standard I2V / T2V
Otherwise:
- Pass `start_image_url` when `facts.start_image_url` (or mode `i2v`) is present.
- Pass `end_image_url` when provided.
- `duration` = `facts.target_duration` (or `facts.duration`).
- Mode `facts.mode`: `i2v` | `t2v`.

## Hard rules

- Must call the real tool; never fabricate success JSON or video URLs.
- **No on-screen text**: on retry, do not add/keep readable text, lyrics, or subtitle descriptions in the prompt. Lip-sync = performance state only (singing / lip-sync), never lyrics.
- **I2V first-frame fidelity** (when not reference-to-video): do not describe body parts/elements absent from the start frame appearing later; ban full spins / 360° / spin-around that reveal orientations not stable in the start frame (front/side/back all apply).
- Shot context in facts (shot_number, is_bridge, shot_type, camera_*, scene_description, dialogue, narration, sound_effects, transition, user_input) is background — prefer the optimized `i2v_prompt` unless regenerating.
- Keep prompt length within `facts.prompt_length_range` when trimming.

## Retry (only on failure)

- Content safety: neutralize sensitive wording.
- Too long: trim to `facts.prompt_length_range`.
- Bad params: fix duration / resolution / URLs.
- Timeout / busy: simplify prompt.

## error_msg / raw_error_msg

After final failure:
1. `error_msg` (user-facing, Chinese, ≤40 chars): plain reason + actionable tip. **No** vendor/model/tool names (即梦, Pollo, WaveSpeed, Seedance, Sora, Kling…), **no** HTTP codes, API jargon, URLs, UUIDs.  
   Good: 「内容包含敏感词汇，请修改提示词后重试」 / 「请求过于频繁，请稍后再试」  
   Bad: 「即梦视频业务错误 50413」
2. `raw_error_msg`: full raw tool error for backend only.
