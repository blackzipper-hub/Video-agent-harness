# Agent Note: 视频构建通过单一运行时解析全部 Skill

Status: implemented

[English](2026-08-30-unified-video-skill-runtime.md) | 中文

## Problem

Cuti 内曾存在两条本质不同的 Skill 路径。V2 Runtime 通过 `SkillCatalog` 发现并校验元数据、可执行契约、Workflow 声明、依赖、选择器、信任和安装状态；较早的媒体辅助逻辑则从固定 stage 目录直接读取指定 `SKILL.md`。迁移后的 Build 因而可能通过 Catalog 编译 Workflow，却在执行嵌套提示词时读取计划之外、可变且没有版本记录的文件。

## Decision

Python 进程只拥有一个 `VideoSkillRuntime`。它包含完整的已导入 Cuti Skill 根目录、一个 `SkillCatalog`、一个 Capability Registry、一个 Workflow Registry 和一个不依赖 Agent 模型的 `SkillResolver`。DeepSeek 提供结构化 `VideoSpec`；Workflow Plugin 编译确定性的 `BuildPlan` 步骤；Resolver 将 Workflow 依赖、显式启用 Skill、项目锁定 Skill、声明的 Director Skill 和 Capability 绑定 Skill 应用到对应步骤。

每个持久化步骤在执行前冻结已解析 Skill 的身份、声明版本、完整文件哈希、角色、Hook、指令和可选约束分类。Provider 提示词使用这份冻结上下文，生成 Artifact 保留 Skill 来源。必需 Skill 缺失或停用时，解析会明确失败。仍需专用 LLM 提示词的导入媒体服务也通过进程级 Catalog 加载指定 Skill；固定 stage 目录读取器不再存在。

迁移后的 Cuti Skill 前端通过 BFF 直接读取 `VideoSkillRuntime.catalog`。单次 Workflow 选择以 `workflow_id` 传递；Style 和辅助 Skill 以 `activated_skill_ids` 传递；项目长期启用状态作为 `ProjectSkillLock` 存入 Video Runtime 仓库，绝不挂在 Agent Run 上。现有安全 ZIP 解压与校验逻辑把 Skill 安装到 Runtime external 根目录，随后统一重载 Catalog、Capability Registry、Workflow Registry 和自动生成的 Workflow Plugin。构建进度展示每个已持久化 `BuildStep.resolved_skills`，但不暴露模型私有思维链。

产品启动只接受 DeepSeek 后端，不会把导入的 Cuti Planner 作为第二套 Agent Loop 挂载。导入的 Coordinator 和任务运行时代码仍作为 Cuti Provider 兼容实现的依赖，但它们共享相同的 Resolver 类型，不构成另一个产品入口。

## Verification

定向 Python 测试覆盖选择器匹配、Workflow supervisor 与依赖应用、冻结上下文序列化、BuildStep 持久化、Workflow 发现、Provider 提示词注入、Artifact 来源、首次规划、通过已安装 Workflow Plugin 的 API 规划、Catalog 驱动的 BFF 列表、结构化选择、项目锁持久化和 ZIP 安装重载。直接辅助函数检查证明 Catalog 可以解析并返回启用的 Director Skill。迁移新增前端文件通过定向 ESLint。

## Alternatives considered

**仅为 Video Runtime 增加一个小型 Director Resolver。** 未采用，因为它会重复 Cuti 的 Catalog、依赖、选择器、安装和信任语义，并随着 Skill 演进产生分叉。

**在导入媒体服务中保留直接读取 stage 目录。** 未采用，因为这种方式绕过启用状态、配置根目录、版本身份和持久化 Build 来源。

**为 Workflow Skill 恢复 Cuti Coordinator。** 未采用，因为 DeepSeek 已负责模型规划；第二套 Loop 会拆分意图理解与恢复责任。

## Consequences

说明型 Skill、Workflow、Director 和可执行 Skill 共用同一事实来源，恢复后的 Build 使用最初持久化的 Skill 上下文。Skill 变更会影响新计划，但不会悄悄改变执行中的 Build。项目默认选择可以跨 DeepSeek Session 持续存在，显式结构化选择仍可对单次 Build 覆盖默认值。Runtime 每个进程会初始化一次更大的 Catalog；旧代码曾静默忽略的停用 Skill 现在会让受影响操作明确报错。
