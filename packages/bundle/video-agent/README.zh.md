# @cuti-ai/video-agent-bundle

[English](README.md) | 中文

Cordis bundle，用于将 DeepSeek Harness Profile 连接到 Python Cuti Video Runtime，并暴露稳定的高层视频工具集合。

## Model Experience

### Video Agent 组合

#### 模型看到什么

bundle 本身不提供模型可见内容。它的 Cordis patch 挂载 `@cuti-ai/tool-video`，后者的 12 个高层视频工具构成完整的模型可见接口。本地 OpenAI 路由使用五分钟的请求和流空闲超时，避免长上下文 VideoSpec 规划被 Provider 传输层提前中断。

#### Token 影响

只有已挂载工具包的固定 schema 和依赖数据的有界结果会占用 Token。

#### KV Cache 影响

bundle 不增加提示词文本；组合不变时，已挂载工具目录保持前缀稳定。

## Known Limitations and Deferred Work

- Python 运行时必须独立部署，并可通过 `VIDEO_RUNTIME_URL` 访问。
