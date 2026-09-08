# Video Runtime

Provider 分发将任务输入 ArtifactVersion 转换为有序的图片、视频和音频 URI 列表，直调与 Atomic 路径共用。显式媒体参数顺序优先，其余任务输入按声明顺序去重合并。引用素材缺失、提示词索引越界时在提交前失败。输出产物记录解析后的输入版本与具体参数。旧调用把 Seedance 模型名放在 `provider` 字段时，分发会拆分为 WaveSpeed 通道和精确的模型版本；相互冲突的版本参数在提交前失败。分发保留远程任务 ID 和幂等键，参考图不会自动变成首帧。

失败任务可通过 PlanPatch 替换映射提交新参数。待执行的下游任务会复制并改接依赖，旧下游步骤在同一版本事务中取消。失败步骤保留原参数和 `superseded_by` 指针；整单重试跳过已替代及已取消步骤。已开始执行的下游不能自动改接。失败的持续 Build 在查看修复检查点时仍保持失败，只有替换补丁通过计划、规格与项目版本校验并提交后才恢复。

Create 的停止操作同时取消 DeepSeek 对话和活动 Runtime 构建，保留草稿、待执行步骤与远程任务 ID；已提交的外部任务可能继续执行并计费。发送后续消息前需明确确认恢复。恢复会继续最新的已停止构建，检查原项目版本、复用已完成步骤并查询原远程任务，而非创建替代构建。刷新页面不会自动恢复。

[English](README.md) | 中文

Python Video Runtime 独立于 agent Session 或 Run 管理长期视频项目。它存储不可变作品版本、带版本产物、类型化产物依赖、变更请求、重建计划、持久 Build、校验结果、时间线、导出和项目内单调递增事件。

## 运行

独立进程安装与 Runtime 镜像相同的依赖，来源是 `pyproject.toml`：

```sh
python -m pip install -e .
python -m uvicorn app.video_runtime.standalone:app --host 127.0.0.1 --port 8001
```

设置 `VIDEO_RUNTIME_DATABASE_URL` 可启用 Postgres 持久化。未设置时，独立服务使用 `VIDEO_RUNTIME_LOCAL_STATE_PATH` 指定的原子 JSON 存储（默认 `./data/video-runtime-state.json`）；只有隔离测试才应设置 `VIDEO_RUNTIME_IN_MEMORY=true`。根目录的 `compose.video.yml` 会在启动服务前按顺序执行 `migrations/video_runtime` 中的全部迁移。

使用本地存储时，独立入口默认将 `PUBLIC_BASE_URL` 设置为 `http://127.0.0.1:8001`，与文档端口及 `/files/*` 挂载一致；若 Runtime 使用其他域名或端口，需要显式覆盖。发往远程生成服务的媒体仍会经过 media-egress 上传，不会直接把回环地址交给 Provider。

## Runtime API

`/api/video` 下的版本化 API 提供项目创建与查看、统一编辑工作区、结构化编辑与确定性依赖影响预览、Build 提交与取消、产物版本选择、作品版本恢复、固定作品版本导出、已配置插件管理和项目事件流。修改操作可能因重放产生重复工作时，必须携带幂等键。Build、选择、恢复和导出会在修改权威状态前比较基础作品版本。

## 插件执行

同时启用分阶段规划和 `VIDEO_CONTINUOUS_PLAN_PATCH_ENABLED` 时，每个 Build 由一个 Runtime Worker 调度。Worker 按配置的并发上限启动依赖已完成的任务，在单任务完成、最终失败或收到 PlanPatch 时重新读取持久化计划。单任务结束即可请求 Agent 规划，不等待无关 Provider 操作。同一 Build 只保留一个待处理检查点；规划期间发生的后续结束事件合并到下一份快照，通过绑定的 DeepSeek Session 投递，不增加第二套 Planner。失败任务保留记录，由 Agent 使用新任务 ID 提交替代任务。

执行期间可调用 `POST /api/video/projects/{project_id}/builds/{build_id}/checkpoints/live` 打开或复用规划快照，然后携带返回的检查点 ID、准确的 Plan/Spec 版本和幂等键提交修改。PlanPatch 可以追加依赖已有任务或同批新增任务的步骤，也可以取消待执行任务及其待执行后继。任务启动与取消原子比较已存储的 Step 状态；不能借待执行任务取消操作取消运行中的任务。有活动任务时禁止宣告目标完成。服务退出保留远程任务 ID 用于对账；恢复时即使 Build 仍在等待 Agent，也会继续追踪远程任务。同一仓库只能由一个 Runtime 进程执行任务，尚不提供分布式 Worker 租约。

`video-plugin.yaml` 声明 Provider、Workflow、Style、Validator 和 Media 贡献，以及依赖与权限。`VIDEO_PLUGIN_PATHS` 可以用平台分隔的目录替换内置 `plugins` 根目录，这些目录的直接子目录包含 manifest。插件可以继承 `BaseVideoPlugin`；其 `capability_handlers()` 将声明的 Capability id 映射到可执行 Handler。受信任的内置插件可以进程内加载；不受信任的插件通过 `sandbox_runtime` 声明镜像、入口和超时，运行时不会导入它的模块，而是带着签名 Grant 的限制交给 `services/sandbox-worker` 执行。

`VIDEO_SKILL_PATHS` 可以替换进程级 Skill 根目录。未配置时，单一 `VideoSkillRuntime` 会发现内置 system、builtin、external 和 stage-director Skill。共享 Catalog 校验可执行契约和 Workflow 声明，在每个已规划 Build 步骤上冻结辅助 Skill 上下文，并通过 `cuti.skill-workflows` 注册 Workflow 贡献。`GET /api/video/workflows` 同时返回 manifest 工作流与最终生效的 Skill 工作流。

每个可选 Workflow 都必须公开一个具名的专用编译器契约。未知 Skill mode、或没有显式 Runtime 描述的 Workflow 插件会显示为不可用；系统不存在通用／默认 Workflow 编译器回退。

内置适配器复用 Cuti 的文本、图片、音乐、视频、TTS、FFmpeg、字幕与 Lipsync 操作。可选工作流（`mv`、`seedance2`、`short-drama-workflow`、`product-ad-video` 以及 product-workflow 变体）编译与 Provider 无关的 `VideoSpec`；`cuti.style-presets` 在编译前应用已安装的提示词默认值。生产身份可以使用独立 Runtime 的服务 Bearer Token，或组合应用中的 Cuti JWT 适配器；两者均未配置时默认拒绝请求。

每个新的 `ProjectIntent` 和 `VideoSpec` 都持久化一份语言契约，分别记录界面、用户可见内容、对白或旁白、字幕和 Provider 提示词语言。BFF 会把该契约注入首次规划、后续编辑和自动检查点回合；Runtime 生成的用户产物会记录契约，并拒绝明确的文本语言不匹配。只有 `language` 字段的旧文档会为所有内容字段补上相同语言的默认值。

所有已注册 Capability 都通过 `RuntimeCapabilityRegistry` 和 `CapabilityExecutionGateway` 执行。网关校验服务端签名 Grant，Grant 绑定项目、Session、用户、插件、Capability、域名、费用上限、超时、并发、重试、幂等键、审计 id 和过期时间。进程内可执行插件要求服务端持有至少 32 字节的 `VIDEO_CAPABILITY_GRANT_SECRET`。

场景参考图会同时检查 Provider 输入边界和最终渲染像素。场景参考图校验失败时，会在任何依赖它的视频片段执行前进入分阶段构建唯一一次语义修复。`VIDEO_SCENE_REFERENCE_VISUAL_VALIDATION_ENABLED` 可显式启停像素检查；未设置时，只要配置了 `OPENAI_API_KEY` 就会自动启用。`VIDEO_SCENE_REFERENCE_VALIDATOR_MODEL` 用于指定支持视觉输入的校验模型。

## 兼容 API

Studio 对接 `app.video_runtime.standalone:app`。`/chat-v1/service` 挂载的是 DeepSeek BFF，不是已下线的 LangGraph Planner。

如果粘贴的 Create Space URL 指向一个仍存在于 DeepSeek、但在恢复后的 Runtime 中已没有 Project 绑定的 Session，BFF 会创建全新 Session 并返回新的 `thread_id`，不会把新 Project 接到未绑定的历史 Session 上。

## 测试

检查点投递根据已消费的 Session 消息和已结束的回合进行对账。回合结束却未处理检查点时，在原有投递次数限制内重试；重试和检查点查询均包含当前任务结果。排队中的消息、活跃回合、已处理检查点和已取消 Build 不会被判定为缺少确认。投递中断仍通过租约恢复。

```sh
python -m unittest discover -s tests/video_runtime -v
```

`tests/` 下还有 Capability、媒体工具和 DeepSeek BFF 的 pytest 覆盖。
