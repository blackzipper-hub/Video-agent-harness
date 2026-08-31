# Cuti 媒体服务

[English](README.md) | 中文

Cuti 平台的媒体处理微服务，负责 FFmpeg 视频与音频处理、图片缩放和 S3 文件管理，并为每个任务隔离工作目录。

## 架构

- **FastAPI** REST API
- **FFmpeg** 视频与音频处理，并带并发控制
- **Pillow** 图片处理
- **S3** 输入与输出存储
- 按 `run_id` 隔离工作目录，并基于 TTL 清理
- 使用 Helm、HPA 自动扩缩容部署到 AWS EKS

## 快速开始

```bash
# Local development
pip install -r requirements.txt
cp .env.example .env  # edit with your AWS credentials
uvicorn app.main:app --reload --port 8080

# Docker
docker-compose up --build

# Run tests
pip install pytest pytest-asyncio httpx
pytest tests/ -v
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/video/info` | 读取视频元数据 |
| POST | `/api/v1/video/trim` | 裁剪视频或补足时长 |
| POST | `/api/v1/video/concat` | 拼接视频 |
| POST | `/api/v1/video/speed-adjust` | 调整视频速度 |
| POST | `/api/v1/video/normalize` | 统一分辨率 |
| POST | `/api/v1/video/strip-audio` | 移除音轨 |
| POST | `/api/v1/video/mix-audio` | 混合音频与视频 |
| POST | `/api/v1/video/add-audio` | 添加音轨 |
| POST | `/api/v1/video/create-placeholder` | 生成黑色视频 |
| POST | `/api/v1/video/extract-frame` | 按时间点提取静帧 |
| POST | `/api/v1/audio/info` | 读取音频时长 |
| POST | `/api/v1/audio/trim` | 裁剪音频 |
| POST | `/api/v1/audio/extract-from-video` | 从视频提取音频 |
| POST | `/api/v1/audio/convert` | 转换音频格式 |
| POST | `/api/v1/image/info` | 读取图片尺寸 |
| POST | `/api/v1/image/resize` | 调整图片尺寸 |
| POST | `/api/v1/pipeline/segment-process` | 多视频对齐并拼接 |
| POST | `/api/v1/pipeline/ensure-on-s3` | 下载、标准化并上传 |
| POST | `/api/v1/pipeline/workspace/cleanup` | 清理工作目录 |
| GET | `/healthz` | 存活探针 |
| GET | `/readyz` | 就绪探针 |
| GET | `/metrics` | Prometheus 指标 |

## 部署

```bash
# Build and push to ECR
aws ecr get-login-password | docker login --username AWS --password-stdin <account>.dkr.ecr.ap-southeast-2.amazonaws.com
docker build -t <account>.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-media-service:latest .
docker push <account>.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-media-service:latest

# Deploy to EKS via Helm
helm upgrade --install cuti-media-service ./helm/cuti-media-service \
  --set image.repository=<account>.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-media-service \
  --set image.tag=latest
```
