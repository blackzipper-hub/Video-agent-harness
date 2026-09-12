# Agent Note: 保留单一 Harness 循环

Status: implemented

[English](2026-09-11-single-harness-loop.md) | 中文

## 问题

Video Runtime 在参考调研和视觉一致性校验背后暴露了第二套 Agent 循环。DeepSeek Harness 已经负责规划、上下文管理与工具选择，但这些路径仍可能启动自己的 Planner。叶级 Provider 工具也仅为装饰器、消息容器、重试和结构化输出而导入第二套框架。

## 决策

DeepSeek Harness 是唯一的 Agent 循环。参考调研由当前 Harness 上下文直接执行 `web_search`，不再提供可调度的 `research.generate` Capability。图片和视频 Provider 成功生成后直接返回，不启动 LLM 一致性循环。构建 Validator 保留确定性媒体检查；可选的参考图像素校验只直接请求一次 OpenAI，不是 Agent 循环。

Video Runtime 使用小型本地异步工具封装保存 Provider 元数据并显式传递运行上下文。Atomic 文本生成直接调用官方 OpenAI SDK，音频转录通过官方 Google GenAI SDK 和 Pydantic 响应 Schema 执行。Runtime 依赖清单与生产代码导入均排除已停用的 Agent 框架包。

源码启动器会在根构建中构建 Video Studio，在其托管的 Python 依赖目录中安装独立的 LangSmith 可观测性客户端，并且只在实际使用旧版 Redis 取消与限流适配器时才加载它们。仓库的 dotenv 模板不会再包含 DeepSeek Harness 明确要求只能由启动 Shell 传入的进程级代理设置。因此，本地单用户启动不要求安装 Redis 包或运行 Redis 服务。

## 考虑过的替代方案

**保留第二套循环但隐藏它们的 Capability ID。** 不采用，因为图片和视频 Wrapper 仍可能隐式启动它们，运行行为仍会依赖隐藏的 Planner 及其传递依赖。

**保留框架并只把它当作通用工具装饰器依赖。** 不采用，因为 Provider 执行只需要名称、Schema 元数据、异步可调用对象和显式上下文载体。本地封装使边界清晰，也防止另一套 Agent Runtime 通过叶级依赖重新进入系统。

**完全移除参考调研。** 不采用，因为当前信息能够改善创意方向。当前 Harness 可以直接搜索，无需持久化中间 Capability Artifact，也无需委派规划。

## 影响

每次运行只有一个规划与上下文压缩责任方。包含 `research.generate` 的已有 Plan 必须依据当前 Capability Catalog 重新规划。图片与视频生成保留有界的 Provider 重试和降级，但不再额外调用模型判断一致性或重写 Prompt。已移除的结构化输出恢复工具不能用于新 Provider 代码；新的叶级集成使用官方 Provider SDK 或确定性逻辑。

源码用户可以先执行文档中的根构建，再执行 `pnpm video:local`；托管启动器会安装自身所需的 Python 与媒体依赖，不依赖仓库中未声明的本地环境。

## 验证

Runtime 测试会解析每个生产 Python 文件，一旦导入已停用框架包便失败。另一项检查会拒绝 `pyproject.toml` 中出现这些包，并拒绝 Runtime 代码、Plugin Manifest 与 Skill 中出现 `research.generate`。Atomic 执行、MV Workflow 对齐、Plugin 注册、工具 Wrapper 降级以及直接工具元数据测试覆盖保留的路径。

本地启动器已经依次通过 Media Service、Sandbox Worker、Video Runtime、DeepSeek Harness、生产版 Studio Gateway 与 Vite 开发代理的冒烟测试。在没有安装 Redis 包的情况下，Provider Wrapper 可以成功导入；通过 Studio 代理匿名创建本地项目也能成功。
