# Agent Note: 可随 npm 分发的本地运行时

Status: implemented

[English](2026-09-08-portable-local-runtime.md) | 中文

## 问题

此前视频产品依赖 Docker Compose 提供 PostgreSQL、Python 服务、FFmpeg、沙箱执行和进程管理，首次启动明显重于可以直接通过 npm CLI 启动 Web 产品的上游 DeepSeek Harness。

## 决策

将 `@cuti-ai/video-agent-harness` 作为产品 CLI 发布。它在一个前台进程中管理 DeepSeek Harness、Video Runtime、Media Service、Sandbox Worker 和构建后的 Video Studio。首次运行时，CLI 下载经过固定校验和验证的便携 CPython，并在用户数据目录中安装隔离的 Python 依赖。它还会在首次运行时让 npm 将采用独立许可证的 FFmpeg 和 FFprobe 工具安装到该目录，而不是在这个 MIT 软件包中重新分发启用了 GPL 功能的二进制文件。

本地项目和构建状态复用已有的可崩溃恢复 `LocalJsonVideoProjectRepository`；生成媒体和沙箱工作目录也保存在每用户数据目录中。未设置 `VIDEO_RUNTIME_LOCAL_STATE_PATH` 时，直接通过 Python 启动也会解析到同一个平台专属目录。检测到工作目录相对的旧状态文件时，服务会阻止创建空的规范存储，并报告两个路径以便明确迁移。CLI 只暴露一个 Video Studio 端口，并将产品 API 路径代理到 Video Runtime。生产、多用户、PostgreSQL 和不受信任插件隔离仍可使用 Docker Compose。

## 安全边界

npm 本地模式是面向可信单用户的开发和本地运行模式。它的 Sandbox Worker 会限制并审计子进程，但不构成安全隔离边界。不受信任的可执行插件必须使用 Docker 部署或其他强化的 Sandbox Worker。

## 考虑过的替代方案

**保留依赖工作目录的 Runtime 默认路径。** 不采用，因为从另一个目录启动同一个代码库会静默创建不同的项目库。

**自动复制找到的第一个旧状态文件。** 不采用，因为多个旧文件可能包含不同项目，自动覆盖或按首个匹配选择会丢失有效状态。

## 后果

用户可以运行 `npx @cuti-ai/video-agent-harness web`，无需安装 Docker、系统 Python、PostgreSQL 或系统 FFmpeg。首次启动需要下载 Python 并安装 Python wheel。CLI 与直接 Runtime 启动共享同一个默认项目库；操作者必须明确合并或选择检测到的旧存储。生产部署继续保留现有 Docker 和 PostgreSQL 路径。
