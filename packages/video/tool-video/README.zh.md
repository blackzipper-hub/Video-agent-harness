# @cuti-ai/tool-video

[English](README.md) | 中文

一组稳定的高层工具，使 DeepSeek agent 能够操作带版本的视频项目，而不暴露 Provider 专用调用。

## Model Experience

### 项目视频工具

#### 模型看到什么

12 个 `video_*` 工具提供项目创建与计划、项目查看、结构化编辑与依赖影响预览、重建执行、持久 Build 控制、产物选择和导出。`video_edit_preview` 只返回费用与受影响步骤，不执行付费操作。schema 不包含 Provider 凭据、内部任务、插件钩子和原始媒体操作。

#### Token 影响

启用工具插件时会产生固定 schema 成本；结果依赖数据，并采用有界摘要。

#### KV Cache 影响

启用的工具集合不变时，前缀保持稳定。

## Known Limitations and Deferred Work

- 首个版本把项目 id 作为显式工具参数；自动把活动项目注入工具参数的功能仍待实现。
