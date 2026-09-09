# Agent Note: 移除 Seedance 提示词包

Status: implemented

[English](2026-09-09-remove-seedance-prompt-pack.md) | 中文

## 问题

内置的 `seedance-20` 提示词包向 Catalog 添加了一个父 Skill 和 28 个嵌套辅助 Skill，但 Video Runtime Workflow 并不依赖它们。保留这个未使用的包既增大仓库拉取体积，也会向用户展示不受支持的选项。

## 决策

仓库不再包含完整的 `seedance-20` 包及其嵌套 Skill。Skill 发现机制不会为被移除的包保留兼容别名或隐藏 Catalog 项。视频生成继续通过相互独立的 `seedance2` Workflow Skill 及其 Runtime Capability 执行。

## 考虑过的替代方案

**只在 Video Studio 中隐藏这些 Skill。** 不采用，因为 Runtime 发现和模型可见 Catalog 仍会暴露未使用的指令，仓库也仍会保留全部包文件。

**将这个包保留为可选内置依赖。** 不采用，因为已发布的 Workflow 均不依赖它；仍有独立价值的提示词指导可以作为单独维护的 Skill 安装。

## 影响

仓库拉取不再包含 268 个未使用文件，全新 Runtime Catalog 减少 29 个 Skill 条目。已经显式锁定被移除 Skill ID 的旧项目，需要先选择已安装的替代 Skill 才能继续规划。只有在具备独立测试和明确 Runtime 使用场景时，才考虑重新引入该包。
