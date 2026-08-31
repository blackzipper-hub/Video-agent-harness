# Agent Note: 首次视频构建使用产物构建图

Status: implemented

[English](2026-08-29-initial-video-build.md) | 中文

## Problem

增量运行时可以重建已选择产物，但不能创建第一个不可变作品版本。复用旧 Cuti Coordinator 会在 DeepSeek 下面引入第二个 LLM Planner；把 15 秒视频当作一次 Provider 调用，则会丢失镜头连续性、重启恢复、draft 复用、成本归因和原子发布。

## Decision

DeepSeek 生成一个经过校验且与 Provider 无关的 `VideoSpec`。Workflow Plugin 把它编译为持久化 `BuildPlan`，统一支持 `initial`、`incremental` 与 `export`，并使用稳定的 `BuildStep` 身份。Runtime 执行 Capability，把每个成功结果保存为 draft 产物，按策略对失败产物重试一次，并仅在校验通过后原子发布新的 `ProjectVersion`。

内置 `cuti.seedance-story` 工作流复用现有 Cuti 原子 Provider 和媒体操作。每个角色都有稳定的参考产物，镜头只依赖其实际使用角色的参考产物。视频片段通过提取出的真实尾帧形成串行连续性链；独立工作表示为依赖图中的独立分支。`cuti.music-video` 与 `cuti.lipsync-music-video` 复用该构建图并增加各自的领域要求。`cuti.media-core` 负责 TTS、时间线、拼接、混音、字幕、Lipsync 及面向导出的媒体操作。`cuti.continuity-validator` 负责结构检查、最终媒体探测，以及可选的 Cuti 既有 VLM 一致性检查。

Provider 接受远程任务后立即把提交 ID 写入 `BuildStep`。重试或进程重启时，该 ID 会重新传给 Provider Bridge，先对账／查询原任务，再考虑新提交。本地自托管配置运行导入的 Cuti Media Service，并与 Video Runtime 共用本地媒体卷；Video Runtime 在 `/files` 提供该卷，因此 S3 是可选能力而不是隐含前提。

DeepSeek 新增项目级创建、计划、构建、检查、结构化编辑预览、重建、状态、取消、产物选版与导出工具。付费编辑先生成预览计划，并要求单独调用执行；其 Agent Loop 与 Session 实现均不修改。

增量计划也可以创建基础版本中不存在的产物。例如，给无音乐项目增加音乐时，会真正生成 `bgm` 与 `mix-bgm` 步骤，先把两个输出保存为 draft，再在原子版本提交时统一选中。时间线编辑会根据修改后的 VideoSpec 重新编译工作流参数：仅调整顺序会复用镜头并重建拼接，修改时长则只让对应镜头及其媒体下游失效。Create 工作区通过 Runtime 回调调用这些操作，不再使用旧 video-analysis 编辑接口。

## Verification

Python 测试覆盖 VideoSpec 校验、依赖环拒绝、首次计划编译、稳定尾帧串接、Provider ID 对账、逐步骤持久化、增量新增产物、媒体编辑费用、原子提交、幂等重复执行及 PostgreSQL 仓库重启。现有增量、插件、安全和 API 测试继续通过。TypeScript 检查覆盖 Service Definition、HTTP Provider、视频工具组合与 Create 媒体编辑器。一次真实 GPT Image + Seedance 运行已完成尾帧提取、时间线／成片组装、FFprobe 校验、原子版本提交、MP4 导出、DeepSeek 查看和 Video Studio 可播放预览。

## Alternatives considered

**首次构建复用旧 LangGraph Coordinator。** 拒绝，因为这会让 Runtime 出现第二个由模型控制的计划，并让恢复依赖对话状态。

**把所有 Provider 和 FFmpeg 操作暴露给 DeepSeek。** 拒绝，因为模型会负责排序，并可能绕过产物事务、连续性链和嵌套 Capability Grant。

**每个产物成功后立即发布。** 拒绝，因为字幕或最终校验失败时，用户会看到部分更新的作品。

## Consequences

首次与增量构建共用作品版本、产物依赖、幂等、恢复、授权和审计状态。`/create/:threadId` 读取统一 Runtime 工作区投影，并把聊天事件与项目 Build 事件分别传输。失败 Build 留下的 draft 产物会占用存储，需要后续保留策略清理。真实 VLM 连续性校验会增加模型成本，因此保持可选；确定性的时间线和最终媒体检查始终执行。
