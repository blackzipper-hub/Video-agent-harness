# Video Studio

[English](README.md) | 中文

Video Studio 是 Cuti Video Agent Harness 的视频原生前端。迁移期间，导入的 Cuti 聊天、产物浏览器、播放器、时间线、SSE 处理和旧 API 客户端继续可用。

`/create/:threadId` 是主工作区：左侧保留 DeepSeek 对话，右侧投影 Runtime 工作区，展示角色、镜头、产物版本与 Build 进度，并提供音乐替换、逐镜头 Lipsync、时间线顺序／时长／旁白编辑、费用确认和 MP4 导出。迁移期间，`/video` 路由只保留为高级诊断入口。

## 开发

```sh
pnpm --filter @cuti-ai/video-studio run dev
```

开发服务器使用 `VITE_VIDEO_RUNTIME_URL` 转发 `/api/video`；为了兼容组合式 Cuti 后端，默认值是 `http://127.0.0.1:8000`。使用独立运行时时，将它设置为 `http://127.0.0.1:8001`。

## 生产构建

```sh
pnpm --filter @cuti-ai/video-studio run build
```

根目录的 `compose.video.yml` 会构建应用并在 `http://127.0.0.1:3000` 提供服务。本地镜像把 `/api/video` 代理到独立运行时。生产环境的身份与聊天路由仍由部署方集成。
