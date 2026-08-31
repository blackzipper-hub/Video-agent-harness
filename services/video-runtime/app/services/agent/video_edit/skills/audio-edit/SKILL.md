---
name: audio-edit
description: >-
  改音乐/配乐/BGM（以及旁白相关沟通）。当用户说"音乐换一下/换个BGM/音乐激烈点/配乐太吵/旁白"
  等音频类修改时使用。涉及工具 update_music_prompt。
---

# audio-edit — 音乐 / 旁白

## 改音乐 → update_music_prompt
`update_music_prompt(instruction="…")`：在当前音乐 prompt 基础上**融合**用户的修改说明（写修改说明即可，勿自己拼原文）。

## 音频链（独立于视觉链）
- 音乐、旁白与视觉元素/关键帧/视频的**画面内容互不影响**。
- 但音乐变更 → 片段(segment) → 成片(assembly) 需要更新；旁白变更同理。
- 因此改完音乐若用户要它进成片，按需 `reassemble_video`（走 edit-visual 的合成，注意 reassemble 不与任何 regenerate 并行）。

## 可以并行
音乐相关操作可以与任何其他操作同时进行（与视觉链无依赖）。

## 事实以工具为准
是否已更新、当前音乐状态 → 以 `get_project_status` / `get_artifact_detail` 为准，勿仅凭历史作答。
