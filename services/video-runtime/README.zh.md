# Video Runtime

[English](README.md) | 中文

Python Video Runtime 独立于 agent Session 或 Run 管理长期视频项目。它存储不可变作品版本、带版本产物、类型化产物依赖、变更请求、重建计划、持久 Build、校验结果、时间线、导出和项目内单调递增事件。

## 运行

独立进程只需要 `requirements-runtime.txt` 中的依赖：

```sh
python -m pip install -r requirements-runtime.txt
python -m uvicorn app.video_runtime.standalone:app --host 127.0.0.1 --port 8001
```

设置 `VIDEO_RUNTIME_DATABASE_URL` 可启用 Postgres 持久化。未设置时，服务使用只适合测试和本地评估的内存存储。根目录的 `compose.video.yml` 会在启动服务前按顺序执行 `migrations/video_runtime` 中的全部迁移。

## Runtime API

`/api/video` 下的版本化 API 提供项目创建与查看、统一编辑工作区、结构化编辑与确定性依赖影响预览、Build 提交与取消、产物版本选择、作品版本恢复、固定作品版本导出、已配置插件管理和项目事件流。修改操作可能因重放产生重复工作时，必须携带幂等键。Build、选择、恢复和导出会在修改权威状态前比较基础作品版本。

## 插件执行

`video-plugin.yaml` 声明 Provider、Workflow、Style、Validator 和 Media 贡献，以及依赖与权限。`VIDEO_PLUGIN_PATHS` 可以用平台分隔的目录替换内置 `plugins` 根目录，这些目录的直接子目录包含 manifest。插件可以继承 `BaseVideoPlugin`；其 `capability_handlers()` 将声明的 Capability id 映射到可执行 Handler。受信任的内置插件可以进程内加载；不受信任的插件通过 `sandbox_runtime` 声明镜像、入口和超时，运行时不会导入它的模块，而是带着签名 Grant 的限制交给 `services/sandbox-worker` 执行。

`VIDEO_SKILL_PATHS` 可以替换进程级 Skill 根目录。未配置时，单一 `VideoSkillRuntime` 会发现内置 system、builtin、external、creative、stage 和 video-edit Skill。共享 Catalog 校验可执行契约和 Workflow 声明，在每个已规划 Build 步骤上冻结 Director 与辅助 Skill 上下文，并通过 `cuti.skill-workflows` 注册 Workflow 贡献。`GET /api/video/workflows` 同时返回 manifest 工作流与最终生效的 Skill 工作流。

内置适配器复用 Cuti 的文本、图片、音乐、视频、TTS、FFmpeg、字幕与 Lipsync 操作。`cuti.seedance-story`、`cuti.music-video` 和 `cuti.lipsync-music-video` 编译与 Provider 无关的 `VideoSpec`；`cuti.style-presets` 在编译前应用已安装的提示词默认值。生产身份可以使用独立 Runtime 的服务 Bearer Token，或组合应用中的 Cuti JWT 适配器；两者均未配置时默认拒绝请求。

所有已注册 Capability 都通过 `RuntimeCapabilityRegistry` 和 `CapabilityExecutionGateway` 执行。网关校验服务端签名 Grant，Grant 绑定项目、Session、用户、插件、Capability、域名、费用上限、超时、并发、重试、幂等键、审计 id 和过期时间。进程内可执行插件要求服务端持有至少 32 字节的 `VIDEO_CAPABILITY_GRANT_SECRET`。

## 兼容 API

`app.main:app` 保留供 Video Studio 客户端使用的 Cuti HTTP 接口，但 `VIDEO_AGENT_BACKEND` 只接受 `deepseek`。产品启动会把 V2 聊天和基于 thread 的 Studio 路由转换为原生 Session RPC，并调用 `VideoBuildRuntime`；不会挂载导入的 DeepAgents／LangGraph Planner。导入的媒体服务也通过同一个进程级 Catalog 解析提示词 Skill。

## 测试

```sh
python -m unittest discover -s tests/video_runtime -v
```

更大范围的 Cuti 测试仍需要原有依赖与外部服务。
