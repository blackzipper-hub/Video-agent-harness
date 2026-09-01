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

## Run

### Run from source

#### 前置条件

- Git。
- Docker Desktop，或安装了 Docker Compose v2 的 Docker Engine。Windows 需要使用 Linux 容器。
- Node.js `22.19+`（或 `24+`）以及 Corepack。
- 至少一个 OpenAI API Key，用于对话和规划。

如果要进行真实的默认视频构建，还需要配置 `WAVESPEED_API_KEY` 或 `ARK_API_KEY`。只有生成音乐的 Workflow 才需要 `SUNO_API_KEY`，其他 Provider Key 均为可选。

#### 1. 克隆并配置

```sh
git clone https://github.com/blackzipper-hub/Video-agent-harness.git
cd Video-agent-harness
cp .env.example .env
```

Windows PowerShell 请用 `Copy-Item .env.example .env` 代替 `cp`。随后打开 `.env` 填写 Key，不要提交该文件。

最小可用配置：

```dotenv
OPENAI_API_KEY=your-openai-key
WAVESPEED_API_KEY=your-wavespeed-key
```

即使没有付费 Provider Key，也可以启动界面和本地 API；只有在调用对应的对话或媒体生成能力时才会失败。

#### 2. 启动视频服务

```sh
docker compose --env-file .env -f compose.video.yml up --build -d
docker compose -f compose.video.yml ps
```

该命令会启动 Postgres、执行数据库迁移，并启动 Video Runtime、Media Service、Sandbox Worker 和 Video Studio。等待 `video-runtime`、`media-service` 和 `sandbox-worker` 进入健康状态。

验证 Runtime：

```sh
curl http://127.0.0.1:8001/health
```

预期响应为 `{"status":"healthy"}`。

#### 3. 构建并启动 DeepSeek Harness

首次克隆后执行一次：

```sh
corepack enable
pnpm install --frozen-lockfile
pnpm run build
```

DeepSeek 进程必须从启动它的终端读取 OpenAI Key。macOS 或 Linux：

```sh
export OPENAI_API_KEY="your-openai-key"
export VIDEO_AGENT_MODEL="gpt-5.6-terra"
export VIDEO_RUNTIME_URL="http://127.0.0.1:8001"
export VIDEO_RUNTIME_SERVICE_TOKEN="video-harness-runtime-local"
pnpm dsh --profile web --patch packages/bundle/video-agent/cordis.patch.yml --no-open
```

Windows PowerShell：

```powershell
$env:OPENAI_API_KEY = "your-openai-key"
$env:VIDEO_AGENT_MODEL = "gpt-5.6-terra"
$env:VIDEO_RUNTIME_URL = "http://127.0.0.1:8001"
$env:VIDEO_RUNTIME_SERVICE_TOKEN = "video-harness-runtime-local"
pnpm dsh --profile web --patch packages/bundle/video-agent/cordis.patch.yml --no-open
```

保持这个终端运行。DeepSeek Harness 默认监听 `http://127.0.0.1:3080`。

#### 4. 打开产品

访问 [http://127.0.0.1:3000/#/zh/create](http://127.0.0.1:3000/#/zh/create)。

端口 `3000` 的 Video Studio 是主要视频生成和编辑界面；端口 `3080` 是通用 DeepSeek Harness 界面；端口 `8001` 是 Video Runtime API。

可以先用下面的低成本 Prompt 测试：

> 制作一个 10 秒、两个镜头的电影感视频：日出时，一个机器人给一朵花浇水。两个镜头保持同一个机器人，不要旁白，并导出最终 MP4。

## 开发模式

如果需要使用 Vite 热更新，请保持 Docker 服务和 DeepSeek Harness 运行，然后在第三个终端执行：

```sh
cd apps/video-studio
cp .env.example .env.local
```

Windows PowerShell 同样可以使用 `Copy-Item .env.example .env.local`。在 `apps/video-studio/.env.local` 中设置：

```dotenv
VITE_LOCAL_SINGLE_USER_MODE=true
VITE_VIDEO_RUNTIME_URL=http://127.0.0.1:8001
VITE_VIDEOCHAT_URL=http://127.0.0.1:8001
VITE_CUTI_BACKEND_URL=http://127.0.0.1:8001
VITE_BACKEND_URL=http://127.0.0.1:8001
```

本地不要设登录模式。`VITE_LOCAL_SINGLE_USER_MODE=true` 时前端写死 `local@cuti.dev`，不调 Go。Dev / 集群镜像构建时这个开关是 `false`。

回到仓库根目录启动 Vite：

```sh
pnpm --filter @cuti-ai/video-studio run dev
```

访问 [http://127.0.0.1:5173/#/zh/create](http://127.0.0.1:5173/#/zh/create)。

## 常见问题

| 现象 | 检查项 |
| --- | --- |
| 页面能打开，但发送 Prompt 后没有响应 | 确认 DeepSeek Harness 仍在端口 `3080` 运行，并且启动它的终端设置了 `OPENAI_API_KEY`。 |
| 出现 `401`、`NO_AUTH` 或模型认证错误 | `.env` 会交给 Docker，但不会自动加载到 DeepSeek 终端；需要在该终端导出 `OPENAI_API_KEY`。 |
| 图片或视频生成失败 | 配置所选 Workflow 需要的 Provider Key；默认 Seedance 路径需要 `WAVESPEED_API_KEY` 或 `ARK_API_KEY`。 |
| Build 一直排队或 Runtime 不可用 | 执行 `docker compose -f compose.video.yml ps`，并查看 `docker compose -f compose.video.yml logs video-runtime`。 |
| 端口已被占用 | 释放或映射端口 `3000`、`3080`、`8001`、`8090` 或 `18080`，并保持 URL 和代理配置一致。 |
| Windows 上 Sandbox Worker 不健康 | 确认 Docker Desktop 使用 Linux 容器，并允许访问 Docker Socket。 |
| 使用代理时 Node 模型请求超时 | 在 DeepSeek 终端设置 `HTTP_PROXY`、`HTTPS_PROXY` 和 `NODE_USE_ENV_PROXY=1`。 |

停止 Docker 服务：

```sh
docker compose -f compose.video.yml down
```

只有在确定要删除本地 Postgres 数据和已生成媒体卷时，才增加 `-v`。

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
