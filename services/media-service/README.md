# Cuti Media Service

English | [中文](README.zh.md)

Media processing microservice for the Cuti platform. Handles FFmpeg video/audio processing, image resizing, and S3 file management with per-task workspace isolation.

## Architecture

- **FastAPI** REST API
- **FFmpeg** for video/audio processing with concurrency control
- **Pillow** for image processing
- **S3** for input/output storage
- **Workspace isolation** per `run_id` with TTL-based cleanup
- **AWS EKS** deployment with Helm + HPA autoscaling

## Quick Start

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

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/video/info` | Get video metadata |
| POST | `/api/v1/video/trim` | Trim/pad video to duration |
| POST | `/api/v1/video/concat` | Concatenate videos |
| POST | `/api/v1/video/speed-adjust` | Adjust video speed |
| POST | `/api/v1/video/normalize` | Normalize resolution |
| POST | `/api/v1/video/strip-audio` | Remove audio track |
| POST | `/api/v1/video/mix-audio` | Mix audio into video |
| POST | `/api/v1/video/add-audio` | Add audio track |
| POST | `/api/v1/video/create-placeholder` | Generate black video |
| POST | `/api/v1/video/extract-frame` | Extract a still frame at timestamp |
| POST | `/api/v1/audio/info` | Get audio duration |
| POST | `/api/v1/audio/trim` | Trim audio |
| POST | `/api/v1/audio/extract-from-video` | Extract audio from video |
| POST | `/api/v1/audio/convert` | Convert audio format |
| POST | `/api/v1/image/info` | Get image dimensions |
| POST | `/api/v1/image/resize` | Resize image |
| POST | `/api/v1/pipeline/segment-process` | Multi-video align + concat |
| POST | `/api/v1/pipeline/ensure-on-s3` | Download + normalize + upload |
| POST | `/api/v1/pipeline/workspace/cleanup` | Cleanup workspace |
| GET | `/healthz` | Liveness probe |
| GET | `/readyz` | Readiness probe |
| GET | `/metrics` | Prometheus metrics |

## Deployment

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
