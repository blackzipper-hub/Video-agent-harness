# Video Agent Harness

[English](README.md) | 中文

Video Agent Harness 是一个基于 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的开源、项目型视频 Agent Harness。DeepSeek 负责对话、Agent Loop 和高层工具选择；Cuti Video Runtime 负责持久化项目、Skill、Artifact 依赖、增量构建、时间线、校验和导出。

主要产品界面是 Video Studio。它的 `/create/:threadId` 工作区左侧展示 DeepSeek 对话，右侧展示已经生成的剧本、图片、视频片段、音频、构建进度和最终视频。

## 架构

```text
Video Studio (:3000)
  |-- /chat-v1 and /api/video
  v
Video Runtime + compatibility BFF (:8001)
  |-- projects, Skills, artifacts, builds, versions
  |-- media service and sandbox worker
  v
DeepSeek Harness (:3080)
  |-- LLM conversation and video_* tool selection
  v
Provider / Workflow / Validator / Media plugins
```

本项目增加了 `@cuti-ai/video-runtime`、`@cuti-ai/video-runtime-http`、`@cuti-ai/tool-video` 和 `@cuti-ai/video-agent-bundle`，没有增加第二套 Agent Loop。详细设计见 [Video Agent Harness 架构说明](docs/video-agent-harness.zh.md)。

规划逻辑复刻 Cuti V2 的持续 `PlanPatch` 契约。Workflow 只限定允许使用的 Capability 和创作规则，不再预编译完整制作 DAG。每一批当前可执行任务完成后， Video Runtime 持久化真实 Artifact，并自动唤醒同一个 DeepSeek Session；DeepSeek 再提交下一批 `add_tasks`、取消仍未开始的任务，或在最终视频完成后宣布目标完成。

## 从源码克隆并运行（推荐）

这是开源分支的发布验收路径。一个命令会同时启动 Video Studio、DeepSeek Harness、Video Runtime、Media Service 和 Sandbox Worker；无需 Docker、PostgreSQL、Redis 或系统 Python。

### 前置条件

- Git。
- Node.js `22.19+` 或 `24+`。
- pnpm `11.x`。先运行 `pnpm --version`；如果尚未安装 pnpm，执行 `corepack enable`。
- 首次运行需要联网，以便启动器下载经过校验和验证的便携 Python、Python 依赖，以及采用各自许可证的 FFmpeg/FFprobe 工具。

### 1. 克隆开源分支

```sh
git clone --depth 1 --branch deepseek-harness-open --single-branch https://github.com/blackzipper-hub/Video-agent-harness.git
cd Video-agent-harness
```

仓库可见性设为 **Public** 后，任何人都能匿名执行上述 HTTPS 命令。正式公开前，已获邀请且配置了 GitHub SSH Key 的协作者可以使用：

```sh
git clone --depth 1 --branch deepseek-harness-open --single-branch git@github.com:blackzipper-hub/Video-agent-harness.git
cd Video-agent-harness
```

浅克隆已包含构建和运行所需的全部内容；后续确实需要完整历史时，可以执行 `git fetch --unshallow`。

### 2. 配置 Provider Key

macOS 或 Linux：

```sh
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

打开 `.env`，填写计划使用的能力所需的 Key，且不要提交该文件。对话加默认视频 Workflow 的常用最小配置是：

```dotenv
OPENAI_API_KEY=your-openai-key
WAVESPEED_API_KEY=your-wavespeed-key
```

`OPENAI_API_KEY` 用于 DeepSeek Harness 的对话和规划模型。默认视频 Workflow 需要 `WAVESPEED_API_KEY` 或 `ARK_API_KEY`；只有生成音乐的 Workflow 才需要 `SUNO_API_KEY`。没有 Key 时界面和健康检查仍可启动，但调用对应模型或媒体能力时会失败。

### 3. 安装并构建

在仓库根目录执行：

```sh
pnpm install --frozen-lockfile
pnpm run build
```

根目录构建会同时生成本地启动器需要的 Video Studio 产物。

### 4. 启动完整本地服务栈

```sh
pnpm video:local -- --data-dir .video-agent-harness-data
```

保持终端运行。首次运行会安装本地运行时依赖，可能需要几分钟；后续启动会复用已被 Git 忽略的 `.video-agent-harness-data`。明确使用仓库内的数据目录，还能避免部分 Windows 环境中的跨盘重命名错误。

当终端输出 `Video Agent Harness is ready` 后，访问 [http://127.0.0.1:3000/#/zh/create](http://127.0.0.1:3000/#/zh/create)。Video Studio 没有登录流程，本地项目身份固定为 `local-user`。

### 5. 验证服务栈

在第二个终端中进入同一仓库根目录并执行：

```sh
pnpm video:doctor -- --data-dir .video-agent-harness-data
curl http://127.0.0.1:8001/health
```

Doctor 应将每个组件显示为 `OK`，健康检查应返回 `{"status":"healthy"}`。端口 `3000` 是 Video Studio，`3080` 是 DeepSeek Harness，`8001` 是 Video Runtime，`8090` 是 Sandbox Worker，`18080` 是 Media Service。

在启动终端按 `Ctrl+C` 即可停止。npm 本地模式面向可信的单用户机器，子进程 Worker 不构成安全隔离边界；需要 PostgreSQL、多用户运行或执行不受信任的插件时，请使用 Docker Compose 部署方式。

可以先用下面的低成本 Prompt 测试：

> 制作一个 10 秒、两个镜头的电影感视频：日出时，一个机器人给一朵花浇水。两个镜头保持同一个机器人，不要旁白，并导出最终 MP4。

## 开发模式

如果需要使用 Vite 热更新，请保持完整本地服务栈运行，然后在另一个终端执行：

```sh
cd apps/video-studio
cp .env.example .env.local
```

Windows PowerShell 同样可以使用 `Copy-Item .env.example .env.local`。在 `apps/video-studio/.env.local` 中设置：

```dotenv
VITE_VIDEO_RUNTIME_URL=http://127.0.0.1:8001
VITE_VIDEOCHAT_URL=http://127.0.0.1:8001
VITE_CUTI_BACKEND_URL=http://127.0.0.1:8001
VITE_BACKEND_URL=http://127.0.0.1:8001
```

Studio 没有登录流程。项目身份是 Video Runtime 的 `local-user`。

回到仓库根目录启动 Vite：

```sh
pnpm --filter @cuti-ai/video-studio run dev
```

访问 [http://127.0.0.1:5173/#/zh/create](http://127.0.0.1:5173/#/zh/create)。

## 常见问题

| 现象 | 检查项 |
| --- | --- |
| 页面能打开，但发送 Prompt 后没有响应 | 确认 DeepSeek Harness 仍在端口 `3080` 运行，并且仓库 `.env` 中存在 `OPENAI_API_KEY`。 |
| 出现 `401`、`NO_AUTH` 或模型认证错误 | 检查 `.env` 中的 `OPENAI_API_KEY`，然后重启本地服务栈以重新加载。 |
| 图片或视频生成失败 | 配置所选 Workflow 需要的 Provider Key；默认 Seedance 路径需要 `WAVESPEED_API_KEY` 或 `ARK_API_KEY`。 |
| Build 一直排队或 Runtime 不可用 | 执行 `pnpm video:doctor -- --data-dir .video-agent-harness-data`，并检查启动器终端。 |
| 端口已被占用 | 释放或映射端口 `3000`、`3080`、`8001`、`8090` 或 `18080`，并保持 URL 和代理配置一致。 |
| 首次安装出现 `EXDEV` 或 `cross-device` | 使用文档中的 `--data-dir .video-agent-harness-data`，且不要把数据目录指向另一个磁盘。 |
| 使用代理时模型或 Provider 请求超时 | 启动服务栈前设置 `HTTP_PROXY`、`HTTPS_PROXY` 和 `NODE_USE_ENV_PROXY=1`。 |

## 测试

```sh
pnpm --filter @cuti-ai/video-studio run build
python -m unittest discover -s services/video-runtime/tests/video_runtime -v
```

仓库同时保留 DeepSeek Harness 的完整检查。更多说明见[开发文档](docs/development.zh.md)和[贡献指南](CONTRIBUTING.zh.md)。

## 项目状态

项目目前处于开发者预览阶段，可能出现不兼容变更。Provider 调用可能产生真实费用，请先使用短视频和低成本 Prompt 测试。

## 许可证

[MIT](LICENSE)。DeepSeek 与 Cuti 导入代码的来源记录在[源码来源说明](docs/source-provenance.zh.md)中；第三方依赖及许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
