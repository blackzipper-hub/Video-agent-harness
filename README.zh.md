# Cuti Video Agent Harness

[English](README.md) | 中文

Cuti Video Agent Harness 是一个基于 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 构建、面向长期视频项目的开源 video agent harness。它将 DeepSeek 的对话与工具选择运行时和 Cuti 的视频项目、时间线、媒体工作流及产物运行时组合在一起。

它沿用 DeepSeek Harness 的 Cordis 插件架构，并增加增量媒体构建模型：一次修改只让受影响的产物失效，同时保留可复用输出、检查连续性，并提交一个不可变作品版本。

## 视频架构

DeepSeek Harness 负责 Session、提示词、agent loop 和高层工具选择。Python Video Runtime 是项目、产物依赖、Build、时间线、校验结果与作品版本的唯一事实来源。Video Studio 继续作为视频原生前端。

集成通过 `@cuti-ai/video-runtime`、`@cuti-ai/video-runtime-http`、`@cuti-ai/tool-video` 和 `@cuti-ai/video-agent-bundle` 完成，不修改 DeepSeek `agent-loop`。详见 [Video Agent Harness 参考](docs/video-agent-harness.zh.md)。

## 本地视频服务

启动 Postgres、增量 Video Runtime、隔离 Sandbox Worker 和 Video Studio：

```sh
docker compose -f compose.video.yml up --build
```

Video Studio 地址是 `http://127.0.0.1:3000/video`，Video Runtime 地址是 `http://127.0.0.1:8001`。需要对话控制时，从源码目录加载视频 bundle 并启动 DeepSeek Web：

```sh
pnpm install
pnpm run build
VIDEO_RUNTIME_URL=http://127.0.0.1:8001 VIDEO_RUNTIME_SERVICE_TOKEN=video-harness-runtime-local pnpm dsh web --patch packages/bundle/video-agent/cordis.patch.yml
```

## 开发者预览

DeepSeek Harness 目前处于 _开发者预览_ 阶段，正在快速迭代。**未来将出现破坏兼容性的变更。**

<a id="run"></a>

## 运行

### 通过 `npm` 运行

安装 `Node.js`，然后运行：

```sh
npx @deepseek-ai/dsh web
```

该命令默认会在 `http://127.0.0.1:3080` 启动 Web UI，本机启动时还会用默认浏览器打开页面。通过 SSH 启动时只打印宿主机 URL，因为本地转发地址由 SSH 客户端或编辑器持有。传入 `--no-open` 可仅运行服务器而不打开浏览器。详见 [Web UI 指南](docs/user/guide/index.zh.md)。

<a id="run-from-source"></a>

### 从源码运行

如需从仓库源码运行：

```sh
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness
pnpm install
pnpm run build
pnpm dsh web
```

`pnpm run build` 会准备仓库产物。`pnpm dsh web` 会直接使用这些已构建产物，不会重新构建。

## 社区与支持

- 欢迎通过 [GitHub Discussions](https://github.com/deepseek-ai/deepseek-harness/discussions) 提交反馈或 bug 报告。
- 为你的插件仓库添加 [`dsh-plugin`](https://github.com/topics/dsh-plugin) 话题，便于被发现。
- 欢迎加入 DeepSeek Harness 企微群：扫码添加企微小助手并填写入群问卷，完成后小助手会邀请你入群。

<table>
  <thead>
    <tr>
      <th align="center">企微小助手</th>
      <th align="center">入群问卷</th>
      <th align="center">微信公众号</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td align="center"><img src="https://cdn.deepseek.com/harness/readme/community-wecom-assistant.png" alt="DeepSeek Harness 企微小助手二维码" width="180" height="180"></td>
      <td align="center"><a href="https://trtgsjkv6r.feishu.cn/share/base/form/shrcnIt5twSVdLGD52KJBckGCgg"><img src="https://cdn.deepseek.com/harness/readme/community-wecom-survey.png" alt="DeepSeek Harness 入群问卷二维码" width="180" height="180"></a></td>
      <td align="center"><img src="https://cdn.deepseek.com/harness/readme/community-wechat-official-account.png" alt="DeepSeek Harness 团队微信公众号二维码" width="180" height="180"></td>
    </tr>
  </tbody>
</table>

## 参与贡献

参见 [CONTRIBUTING.md](CONTRIBUTING.zh.md)。

## 开发

请先阅读[开发指南](docs/development.zh.md)与[架构文档](docs/architecture.zh.md)。

面向 agent：请遵循 [AGENTS.md](AGENTS.md)。

## 许可证

[MIT](LICENSE)

第三方依赖及其许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
