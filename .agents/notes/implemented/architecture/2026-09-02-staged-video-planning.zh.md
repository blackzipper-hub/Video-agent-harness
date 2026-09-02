# Agent Note：基于语义检查点的视频规划

状态：已实现

[English](2026-09-02-staged-video-planning.md) | 中文

## 问题

在依赖媒体的事实出现之前，无法诚实地规划完整视频 DAG。音乐真实时长和节拍、转写文字、产品细节、生成后的角色参考以及连续性修复决策，都只能在前一阶段完成后获知。若一开始强制完整 `VideoSpec`，模型只能编造这些事实，或者把创作规划错误地搬进 Runtime。

## 决策

DeepSeek 继续作为唯一 LLM Planner。新 Build 先保存 `ProjectIntent`、不可变的局部 `VideoSpecRevision`，并只生成第一阶段的确定性步骤。Workflow Skill 在 frontmatter 中声明 `full`、`staged` 或 `agentic` 规划模式及语义检查点。阶段完成后，Video Runtime 保存 draft 产物、创建持久化 `PlanCheckpoint`，并把 Build 切换为 `waiting_agent`。

BFF 的检查点协调器只是一项带租约的可靠投递任务。它通过 `session.prompt(mode=queue)` 把有界的产物摘要排入同一个 DeepSeek Session。DeepSeek 重新加载已锁定 Workflow、检查 Checkpoint，并通过高层工具提交结果。Runtime 校验 Project、用户、Session、Workflow、Capability 和基础版本，然后原子追加 `BuildPlanRevision` 并恢复确定性执行。已完成步骤不可修改。Provider 轮询、转码、尾帧提取、拼接、上传和普通校验不会唤醒模型。

旧 schema version 1 计划继续按原逻辑执行。新的分阶段 Build 使用 schema version 2，并由 `VIDEO_STAGED_PLANNING_ENABLED` 控制。

## 考虑过的替代方案

- **每个 BuildStep 后调用 LLM**——适应性强，但成本高、速度慢、难恢复，并让不需要创作判断的技术步骤获得了多余权限。
- **保留一次性完整 DAG**——执行器最简单，但迫使首次模型调用编造尚未知的媒体事实。
- **在 Video Runtime 中加入 Planner**——协调较少，但会形成第二套 Agent Loop 和相互冲突的创作决策来源。

## 结果

- 计划只追加且可复现；过期 Checkpoint 结果会返回冲突。
- 通过持久租约和幂等键，自动续跑可跨 BFF 或 Runtime 重启恢复。
- Workflow 作者可以声明语义边界，无需修改 DeepSeek Harness 核心。
- Create Space 可以展示可审计的阶段、产物、Skill 和决策摘要，而不暴露隐藏思维链。
- Checkpoint 可以接收完整修订版 `VideoSpec`，也可以只接收结构化章节 patch。Runtime 会基于上一条不可变 Revision 合并，并在追加步骤前校验当前阶段所需的完整输入。
- 关键帧 Workflow 分别设置剧本、参考图和首张关键帧语义边界，同时保留 Cuti 后续镜头依赖真实尾帧的连续性关系。
