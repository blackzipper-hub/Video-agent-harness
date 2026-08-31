# Agent Note：Seedance2 保留原 Cuti Workflow 语义

状态：已实现

[English](2026-08-31-seedance2-workflow-preservation.md) | 中文

## 问题

迁移后的 `seedance2` 适配器用固定故事图替换了所选 Skill 的自主创作指令，还在生成步骤上声明了 `character-director`、`character-image-tool-director`、`video-director` 和 `video-tool-director`，但原 Cuti `seedance2/SKILL.md` 并未声明这些依赖。因此，请求可能显示 `workflow_id=seedance2`，实际却执行另一套创作 Workflow。

## 决策

DeepSeek 仍是唯一规划 Loop。显式 `$seedance2` 语法选择已安装的 Workflow，兼容 BFF 将原始 `seedance2/SKILL.md` 及其 `reference.md` 放入可持久回放的规划提示。DeepSeek 按这些指令生成 `VideoSpec`；Video Runtime 只保存规格与制作记录、使用原生音频生成已编排片段、提取每段真实尾帧供下一段使用、拼接片段、验证结果，并记录恢复与审计状态。

适配器不再生成角色参考图，也不再隐式应用通用 Cuti Director Skill。用户仍可显式激活辅助 Skill。Create 输入框中的文本时长会在提交前覆盖面板默认值。

## 验证

定向 Python 测试证明 `$seedance2` 会加载原始指令与参考文件、持久化解析后的 Workflow、不生成图片或旁白流水线、不隐式应用 Director Skill，并保留串行尾帧接续。Video Studio 生产构建与定向 ESLint 检查通过。

## 考虑过的替代方案

**保留固定图并将其重命名。** 未采用，因为用户选择的是已安装的 Cuti Workflow，并期待其指令控制创作规划。

**恢复 Cuti 原 Coordinator。** 未采用，因为这会在 DeepSeek 之外增加第二套模型规划 Loop；将原始指令加载到 DeepSeek 可保留唯一 Planner。

## 后果

选择 `seedance2` 现在会同时改变路由与创作行为。Runtime 的持久性与 Skill 的创作选择保持独立。已存在的 Build 继续使用其持久化计划，只有新规划的 Build 使用恢复后的行为。
