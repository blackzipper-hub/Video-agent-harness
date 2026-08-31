---
name: edit-visual
description: >-
  改画面/外观：重新生成关键帧、镜头视频、视觉元素(角色/道具/场所)，以及它们之间的级联更新。
  当用户说"太暗了/换颜色/重画/衣服改成X/某一镜的画面/重新生成角色/分镜"等**单镜或单元素**画面修改时使用。
  【路由】改**具名视觉元素**(角色/道具/树/场所等，会跨镜复用)的外观(颜色/材质/造型，如"把树叶改成绿色/给女孩换裙子")
  → 优先 regenerate_characters(改元素本体、级联所有引用镜)，而非只 regenerate_keyframes 单镜。
  仅"某一镜的整体画面(亮度/构图)"才用 regenerate_keyframes。
  注意：用户要改的是"整体画风/全片风格/色调"这类全局设定时，属大纲 style，用 edit-text 的 modify_outline，不在此。
  涉及工具 regenerate_keyframes / regenerate_videos / regenerate_characters。
---

# edit-visual — 改画面与级联

## 视觉链依赖（上游变下游要跟）
视觉元素 → 关键帧 → 视频 → 片段 → 成片。上游变了，下游会不一致，需按需重跑。

## 改某一镜：先定边界再选工具
1. 单镜画面（外观/亮度/小道具）且没指定用已有某版 → 默认 `regenerate_keyframes(shot_numbers=[N], instruction="…", mode="instruction")`（新增一版关键帧，不自动选用）。
2. 属于角色(视觉参考图)本身、或需多镜一致 → `regenerate_characters`，并说明波及哪些关键帧。
3. 不确定 → 先 `get_artifact_detail(artifact_type=keyframe, shot_number=N)` 看引用与关联视频，必要时 `analyze_image`/`analyze_video`。

"所有角色重生成" → 取 character 列表**全部 uuid**，**一次** `regenerate_characters`。

## 必须串行（编辑级联链）
改角色→更新画面：
1. `regenerate_characters`（多个一次传全部 uuid）→ 等完成
2. 告知用户哪些关键帧含该元素
3. 询问是否更新这些关键帧 → 确认后 `regenerate_keyframes`
4. 询问是否更新视频 → 确认后 `regenerate_videos`
5. 如需 `reassemble_video`

只改关键帧→更新视频：
1. `regenerate_keyframes` → 等完成
2. 告知关联视频；同意继续则先 `select_version(keyframe,…)` 选到新版（见 version-switch skill）
3. 询问后 `regenerate_videos`

## 绝对禁止并行
- 同一视觉元素的 regenerate_characters 与引用它的 regenerate_keyframes/videos
- 同一镜头的 regenerate_keyframes 与 regenerate_videos
- reassemble_video 与任何 regenerate

## 重新生成后的引导（仅编辑已有产物后适用）
重生成完成 → `get_artifact_detail` 看关联 → 告知受影响下游 → 询问是否级联 → 不自动级联 → 继续下游前先按 version-switch 处理选用。

## mode 参数
- `instruction`（推荐）：在原 prompt 上叠加修改指令。
- `direct_prompt`：完全替换为用户提供的完整 prompt。

## 示例
- "第 3 镜太暗了"：get_artifact_detail(keyframe,3) → regenerate_keyframes([3],"调亮") → 问是否选用新版并 select_version → 问是否重跑视频。
- "把树叶改成绿色"：get_project_status → get_artifact_detail(character) 找 uuid → regenerate_characters([uuid],"改绿") → 看关联关键帧 → 询问级联。
