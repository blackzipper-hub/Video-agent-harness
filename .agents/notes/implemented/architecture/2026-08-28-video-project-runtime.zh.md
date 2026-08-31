# Agent Note: 视频项目使用独立增量运行时

Status: implemented

[English](2026-08-28-video-project-runtime.md) | 中文

## Problem

agent Session 记录一次对话，但可编辑视频是长期项目，其产物、时间线、远程生成任务与版本会跨越多个 Session。把 Session 或单次 agent Run 当作项目，会让删除、重试、并发编辑、局部重建和 Provider 恢复错误地修改其他状态。把所有 Provider 与媒体操作直接暴露给模型，还会让授权和审计散落在多条执行路径中。

## Decision

DeepSeek Harness 负责 Session、模型调用、agent loop，以及在项目级视频工具之间做选择。独立 Python `VideoBuildRuntime` 负责项目、不可变作品版本、带版本产物、类型化依赖、结构化变更请求、重建计划、持久 Build 与步骤、校验、时间线、导出和项目内单调有序事件。

`@cuti-ai/video-runtime` Service Definition、`@cuti-ai/video-runtime-http` Service Provider 与 `@cuti-ai/tool-video` Consumer 组成跨进程能力 seam。`@cuti-ai/video-agent-bundle` Cordis patch 将它们组合起来，不在 DeepSeek `agent-loop` 中增加视频分支。

Video Studio 将 DeepSeek Session 事件流与 Video Runtime 项目事件流合并为一份用户可见的制作状态。它展示模型身份与用量、agent 决策阶段、具名工具输入与结果、持久 Build 进度和 Build 步骤，但不暴露模型私有推理或系统指令。agent 回合结束却没有启动 Build 时，界面会把它显示为未完成的制作尝试，而不是成功的视频生成。

Video Studio 以 Create 工作区的通用 Artifact 渲染器作为展示权威。已选与执行中的 Runtime 产物会直接显示为文档、故事、图片、视频、音频或兜底产物卡片。Build 失败时，已成功的 draft 产物仍然可见。Create 工作区不再使用专用视频结果投影与旧的 thread 聚合请求；Runtime 感知的选择操作可以切换版本而不调用 Provider。

增量引擎把结构化编辑融合进已选择的 `VideoSpec`，根据产物依赖确定性计算重建、校验和复用集合，并持久化生成所需 Capability 参数。重建会比较基础作品版本，使用稳定的幂等键执行步骤，把成功结果保存为 draft，并通过一次作品版本提交发布全部产物选择。失败时保留旧版本，重启恢复会复用已经完成的 draft 步骤。

视频插件在 `video-plugin.yaml` 中声明可执行贡献与权限。注册的嵌套 Capability 使用签名执行 Grant，并经过统一的授权、超时、并发、重试、取消与审计网关。不受信任的可执行插件不能在进程内加载。

声明式 Workflow Skill 继续以 `SKILL.md` 包存在。Video Runtime 通过可信的 `cuti.skill-workflows` 适配器加载既有 Workflow Registry 元数据，校验声明的 Capability 和 Skill 依赖，并把每个已启用 Skill 的名称贡献为工作流 ID。适配器把工作流模式编译为规范化 BuildPlan，不恢复旧 Coordinator。DeepSeek 的 `workflow_id` schema 接受已安装 ID，未知工作流仍由 Runtime 权威拒绝。

## Verification

TypeScript 组合测试挂载真实 Cordis 工具运行时，并通过 Video Runtime Service 分发项目工具。Python 测试固定了依赖失效、可执行编辑参数、未受影响镜头复用、过期版本冲突、幂等提交、插件生命周期与资源释放、不受信任插件拒绝、签名身份与域名 Grant、通过执行钩子进行 registry 分发、兼容事件投影、Session 缺失时的 Run 列表，以及空项目后续消息自动执行创建流程。Video Studio 生产构建会验证统一工作区与用户可见的制作状态。浏览器验收还会确认 Build 失败后，draft 故事、脚本、结构化项目数据、图片、视频、音频和兜底产物仍能通过通用渲染器显示。

## Alternatives considered

**在 DeepSeek agent loop 中实现视频语义。** 拒绝，因为产物依赖分析、项目事务、Provider 恢复和时间线属于领域状态，而不是语言模型控制流。这种改法还会让上游同步持续产生冲突。

**把 Video Runtime 表示成一个暴露 Provider 级工具的大型模型插件。** 拒绝，因为它会向模型暴露不稳定的内部操作、消耗上下文来描述执行细节，并产生绕过作品版本和授权检查的替代路径。

**继续把 AgentRun 当作 Project。** 拒绝，因为一个项目必须绑定多个 Session 与 Build，而删除或压缩 Session 不能删除项目产物。

**使用 TypeScript 运行时重写 Cuti。** 拒绝，因为 Cuti 已经拥有媒体 Provider、Workflow 编译、任务 Attempt、远程轮询、产物持久化、Validator 和剪辑管线。跨进程 seam 能保留这些代码，并隔离增量迁移。

## Consequences

系统包含两个进程，并要求在两者之间提供 HTTP 身份与取消适配器。项目正确性不再依赖模型重试和 Session 生命周期；由于核心 loop 未修改，DeepSeek 上游更新仍可合并。

Provider、Workflow、Style、Validator 和 Media 实现可以作为视频插件演进。插件不能替换规范化项目事务，也不能发布部分项目状态。兼容 BFF 会把核心 Cuti V2 聊天路由和基于 thread 的 Studio 项目路由转换为 DeepSeek 原生 Session RPC；生产部署仍需提供认证用户身份、Sandbox Worker 传输和具体 Provider bundle。

无效 Workflow Skill 会使适配器加载失败，不会留下一个可选但在付费工作开始后才失败的工作流。适配器保留静态工作流模式和 pipeline 策略；需要独立可执行生命周期的指令仍属于完整 Video Plugin bundle。
