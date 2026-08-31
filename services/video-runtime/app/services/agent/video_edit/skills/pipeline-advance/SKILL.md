---
name: pipeline-advance
description: >-
  推进主管线到下一阶段（不是编辑已有产物）。当用户在门控暂停点说"继续/继续生成/开始生成/
  好的继续/生成下一步/下一步"时使用。涉及工具 continue_pipeline。
---

# pipeline-advance — 推进主管线

先判断是"推进"还是"编辑"（看快照各阶段 status 与 `pending_gate`）：

| 模式 | 何时 | 用什么 | 用户常说 |
|---|---|---|---|
| 推进主管线 | 下一阶段产物 ⏳ 未开始 | `continue_pipeline(gate=…)` | 继续、开始生成、下一步 |
| 编辑修改 | 该阶段已有产物、要改内容 | regenerate_*/modify_*/update_* | 改颜色、太暗了、重画 |

## 门控 → 继续后进入
| pending_gate / gate | 含义 | 继续后 |
|---|---|---|
| after_music | 音乐已生成 | 分析/大纲 |
| after_outline | 大纲已生成 | 角色设计 |
| after_character | 角色已生成 | 场景/分镜细节 |
| after_storyboard_detail | 分镜细节已生成 | 关键帧批量生成；若 `shot_mode=reference_t2v`（Seedance2 跳关键帧）则直接批量生成视频 |
| after_keyframe_reflection | 关键帧已就绪 | 视频批量生成（`reference_t2v` 路径通常不会停在此门控） |
| after_shots | 镜头视频已生成 | 片段合并/成片 |

## 判断顺序
`get_project_status` → 看 `pending_gate` 与各阶段 status → 用户要继续且门控存在 → `continue_pipeline(gate=pending_gate)`。
`阶段` 字段可能滞后，**以 `pending_gate` 为准**。目标阶段已有产物且用户要改 → 走编辑工具，勿误用 continue_pipeline。
`任务状态: failed` 是最近一次 resume/运行失败，**不是**场景/分镜产物失败；场景已 ✅、关键帧 ⏳、且有 `pending_gate`（如 after_storyboard_detail）时，用户说继续 → 直接 `continue_pipeline`，禁止编造「请先修复场景」。
`continue_pipeline` 返回 `resume_submitted` 后：按工具返回的 `message` / `pending_step` 告知用户正在做什么；**禁止**在 `failed_video` / `reference_t2v` 时说「从关键帧确认继续」。

## failed_* 失败暂停
如 `failed_keyframe` / `failed_video`：该阶段有失败项、管线阻断。用户说重试/继续 → `continue_pipeline`，gate 取对应正常门控：
- `failed_keyframe` → `after_keyframe_reflection`（后端会重跑关键帧，不是跳过到视频）
- `failed_video` → `after_shots`（后端会重跑镜头视频，不是跳过到成片）
仍失败且未修 → 先说明失败项。用户说「重新生成关键帧」而当前是 `failed_video` 时，说明本路径无关键帧可重生成，应重试视频。

## 与 regenerate_* 的分叉
regenerate_* 要求 DB 已有对应产物；返回"未找到…"通常是该阶段尚未批量生成或上次保存失败 → 改用 `continue_pipeline`（`failed_video` 用 `after_shots`）。

推进主管线**不适用**编辑级联询问。
