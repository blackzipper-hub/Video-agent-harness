# Cuti-VideoAgent AWS EKS 部署指南

> 目标：将 VideoAgent 从 EC2 迁移到 EKS，老代码不动、EC2 保留运行，前端可随时切回。
> 迁移开始日期：2026-03-10

---

## 当前环境

| 资源 | 值 |
|------|-----|
| AWS 账户 | `699475938168` |
| 区域 | `ap-southeast-2` (Sydney) |
| VPC | `vpc-0cc2ca6f9f8545227` (默认 VPC, `172.31.0.0/16`) |
| EC2 | `i-00656af7c396635b2` (t3.xlarge, `172.31.4.254`) |
| EC2 安全组 | `sg-05b22295d2df9fbdf` (launch-wizard-12) |
| EKS Cluster | `cuti-cluster` (已有，Media Service 在用) |
| EKS Cluster SG | `sg-005f5a5754e566118` |
| Subnet | `2a: subnet-0bf453fe72ddec405` / `2b: subnet-0ffcdae24cf683e02` / `2c: subnet-0f3c38b136b0568d2` |
| S3 (dev) | `cuti-agent-assets-dev-699475938168-ap-southeast-2` |
| S3 (prod) | `cuti-agent-assets-prod-699475938168-ap-southeast-2` |
| CDN (dev) | `https://cdn-dev.newai.land` |
| CDN (prod) | `https://cdn.newai.land` |
| ECR | `699475938168.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-videoagent` |

---

## 架构概览

```
┌─────────────────── AWS VPC (172.31.0.0/16) ───────────────────┐
│                                                                │
│  ┌──────────────────┐     ┌──────────────────────────────────┐ │
│  │  EC2 (172.31.    │     │  EKS Cluster (cuti-cluster)      │ │
│  │    4.254)        │     │                                  │ │
│  │                  │     │  Node Group: media-workers        │ │
│  │  Nginx (SSL)     │     │  └── Media Service Pods          │ │
│  │  Go backend      │     │                                  │ │
│  │  Frontend (node) │     │  Node Group: agent-workers        │ │
│  │  PostgreSQL ◀────┼──── │  └── VideoAgent Pods              │ │
│  │  Redis    ◀──────┼──── │                                  │ │
│  └──────────────────┘     └──────────────────────────────────┘ │
│                                                                │
│  EC2 Nginx → internal ALB → K8s VideoAgent Pod                 │
│  K8s Pod → EC2 内网 IP (172.31.4.254) → PostgreSQL/Redis       │
└────────────────────────────────────────────────────────────────┘
```

- EKS 和 EC2 在同一 VPC，延迟 <1ms，传输免费
- 同一 EKS 集群通过 namespace 区分 dev / prod
- VideoAgent 和 Media Service 同集群，可用 K8s Service DNS 直连

---

## 第 1 步：EC2 安全组加入站规则

> 允许 EKS Pod 连接 EC2 上的 PostgreSQL 和 Redis。

```bash
# 加 PostgreSQL 入站
aws ec2 authorize-security-group-ingress \
  --group-id sg-05b22295d2df9fbdf \
  --protocol tcp --port 5432 \
  --source-group sg-005f5a5754e566118 \
  --description "Allow PostgreSQL from EKS pods"

# 加 Redis 入站
aws ec2 authorize-security-group-ingress \
  --group-id sg-05b22295d2df9fbdf \
  --protocol tcp --port 6379 \
  --source-group sg-005f5a5754e566118 \
  --description "Allow Redis from EKS pods"
```

**执行记录：** ✅ 已执行（2026-03-10）。两条规则已生效。

---

## 第 2 步：PostgreSQL 改监听地址

> 备份配置 → 改 listen_addresses → 加 pg_hba.conf 规则 → 重启。

```bash
# 备份
sudo cp /etc/postgresql/14/main/postgresql.conf /etc/postgresql/14/main/postgresql.conf.bak
sudo cp /etc/postgresql/14/main/pg_hba.conf /etc/postgresql/14/main/pg_hba.conf.bak

# 改 listen_addresses
# /etc/postgresql/14/main/postgresql.conf
# 原来: listen_addresses = 'localhost'
# 改为: listen_addresses = '0.0.0.0'

# 加 VPC 网段访问（pg_hba.conf 末尾加）
# host    all    all    172.31.0.0/16    md5

# 重启 PG
sudo systemctl restart postgresql
```

**执行记录：** ✅ 已执行（2026-03-10）。PG 重启后已验证所有 EC2 本地服务（VideoAgent dev/prod）自动重连正常。重启期间现有活跃 LangGraph task 会报 `AdminShutdown` 错误，需重启 VideoAgent 进程清理连接池。

---

## 第 3 步：Redis 改监听地址

> 备份 → 改 bind → 重启。**不加密码**，靠安全组保护。

```bash
# 备份
sudo cp /etc/redis/redis.conf /etc/redis/redis.conf.bak

# 改 bind
# /etc/redis/redis.conf
# 原来: bind 127.0.0.1 -::1
# 改为: bind 0.0.0.0

# 重启 Redis
sudo systemctl restart redis-server
```

**执行记录：** ✅ 已执行（2026-03-10）。Redis 重启后 EC2 上服务自动重连正常（几秒内）。未加 `requirepass`，避免影响所有已有 Redis 客户端配置。

---

## 第 4 步：从 K8s Pod 验证 DB/Redis 连通性

```bash
# 在已有 media-service Pod 中测试
kubectl exec -it <media-service-pod> -n dev -- bash

# 测试 PostgreSQL
apt-get update && apt-get install -y postgresql-client
psql -h 172.31.4.254 -U cuti_user -d cuti_storybook -c "SELECT 1;"

# 测试 Redis
apt-get install -y redis-tools
redis-cli -h 172.31.4.254 ping
# 返回 PONG
```

**执行记录：** ✅ 已执行（2026-03-10）。DB 和 Redis 都能从 K8s Pod 连通。

---

## 第 5 步：创建 agent-workers Node Group

> VideoAgent 用 m6i.large (2C/8G)，和 Media Service 的 c6i.xlarge 分开。

```bash
eksctl create nodegroup \
  --cluster cuti-cluster \
  --name agent-workers \
  --region ap-southeast-2 \
  --node-type m6i.large \
  --nodes 1 \
  --nodes-min 1 \
  --nodes-max 4 \
  --node-volume-size 50 \
  --node-volume-type gp3 \
  --node-private-networking \
  --node-labels "workload-type=agent" \
  --tags "Project=cuti-videoagent" \
  --managed
```

**执行记录：** ✅ 已执行（2026-03-10）。首次执行因中断重试，第二次成功。Node `ip-172-31-26-237` (m6i.large) 已加入集群，label `workload-type=agent` 已生效。

当前集群节点：

| Node Group | 实例类型 | 用途 | Label |
|------------|----------|------|-------|
| `media-workers` | c6i.xlarge (4C/8G) | Media Service | `workload=media-processing` |
| `agent-workers` | m6i.large (2C/8G) | VideoAgent | `workload-type=agent` |

---

## 第 6 步：创建 ECR 镜像仓库

```bash
aws ecr create-repository \
  --repository-name cuti-videoagent \
  --region ap-southeast-2 \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256

# 返回: 699475938168.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-videoagent

# 生命周期策略（保留最近 20 个镜像）
aws ecr put-lifecycle-policy \
  --repository-name cuti-videoagent \
  --region ap-southeast-2 \
  --lifecycle-policy-text '{
    "rules": [{
      "rulePriority": 1,
      "description": "Keep last 20 images",
      "selection": {
        "tagStatus": "any",
        "countType": "imageCountMoreThan",
        "countNumber": 20
      },
      "action": { "type": "expire" }
    }]
  }'
```

**执行记录：** ✅ 已执行（2026-03-10）。

---

## 第 7 步：代码改动

### 7.1 task_worker.py stop() timeout

```python
# app/services/worker/task_worker.py 第 333 行
# 原来: timeout=300.0（5分钟）
# 改为: timeout=10500.0（2小时55分钟，留5分钟余量给K8s清理）
await asyncio.wait_for(
    asyncio.gather(*self.running_tasks.values(), return_exceptions=True),
    timeout=10500.0  # 2小时55分钟，留5分钟余量给K8s清理
)
```

### 7.2 移除 static 文件挂载（config.py + main.py）

```python
# config.py：移除 __init__ 中的 os.makedirs(self.STATIC_PHOTOS_DIR, exist_ok=True)
# main.py：移除 StaticFiles mount 相关代码（/api/photos, /api/videos, /api/audios）
```

K8s 环境中静态文件全部走 S3/CDN，不再需要本地 static 目录。

### 7.3 Unlink static 目录的 symlinks

```bash
# 三个部署目录的 static/ 下 symlinks 全部 unlink（不删实际数据）
# 实际数据在 /home/ubuntu/app/cartoonbook/data/（232GB），保持不动

# local repo
unlink /home/songsong/local/Cuti-VideoAgent/static/audios
unlink /home/songsong/local/Cuti-VideoAgent/static/photos
unlink /home/songsong/local/Cuti-VideoAgent/static/videos

# dev deployment
unlink /home/songsong/Cuti-VideoAgent-dev/static/audios
unlink /home/songsong/Cuti-VideoAgent-dev/static/photos
unlink /home/songsong/Cuti-VideoAgent-dev/static/videos

# prod deployment
unlink /home/songsong/Cuti-VideoAgent/static/audios
unlink /home/songsong/Cuti-VideoAgent/static/photos
unlink /home/songsong/Cuti-VideoAgent/static/videos
```

**执行记录：** ✅ 已执行（2026-03-10）。unlink 后验证实际数据 `/home/ubuntu/app/cartoonbook/data/`（232GB, images 10114 个, videos 29406 个）完好无损。

> **后续确认**：如果 K8s 部署后前端完整流程跑通，证明 static 目录确实无人使用，可安全删除 EC2 上的 232GB 数据释放空间。

---

## 第 8 步：创建 Dockerfile

```dockerfile
FROM python:3.11-slim AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        libpq-dev \
        gcc \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        libcairo2 \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN pip install --no-cache-dir pip setuptools wheel && \
    pip install --no-cache-dir ".[dev]" 2>/dev/null || pip install --no-cache-dir .
COPY . .
RUN mkdir -p /workspace /tmp/cuti-workspace

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
```

`.dockerignore` 包含 `static/` 防止将本地 static 文件拷进镜像。

**踩坑记录：**
1. `libgdk-pixbuf2.0-0` 在 `python:3.11-slim` 中不存在，正确包名是 `libgdk-pixbuf-2.0-0`
2. 本地 `static/photos` 是 symlink 到实际文件，被 `COPY . .` 拷入后变成文件（不是目录），`os.makedirs` 会报 `FileExistsError`。解决：添加 `static/` 到 `.dockerignore` + 移除 `os.makedirs` + 移除 StaticFiles mount

---

## 第 9 步：创建 Helm Chart

### 文件结构

```
helm/
├── cuti-videoagent/
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
│       ├── deployment.yaml
│       ├── service.yaml
│       ├── ingress.yaml
│       ├── hpa.yaml
│       └── secret.yaml
├── values-dev.yaml
└── values-prod.yaml
```

### 关键配置

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `terminationGracePeriodSeconds` | `10800` (3小时) | 长任务完成不被中断 |
| `safe-to-evict` annotation | `"false"` | 防止 Cluster Autoscaler 驱逐 |
| Rolling Update `maxSurge` | `1` | 先起新 Pod |
| Rolling Update `maxUnavailable` | `0` | 旧 Pod 跑完再删 |
| `nodeSelector` | `workload-type: agent` | 只调度到 agent-workers |
| `service.port` | `8000` | VideoAgent 端口 |
| ALB scheme | `internal` | VPC 内部，不对外暴露 |
| ALB `idle_timeout` | `1200s`（20分钟） | SSE 长连接需要 |

---

## 第 10 步：创建 K8s Secrets

```bash
# Dev 环境
kubectl create secret generic cuti-videoagent-secrets \
  --namespace=dev \
  --from-literal=OPENAI_API_KEY='...' \
  --from-literal=GOOGLE_API_KEY='...' \
  --from-literal=POLLO_API_KEY='...' \
  --from-literal=WAVESPEED_API_KEY='...' \
  --from-literal=SUNO_API_KEY='...' \
  --from-literal=LANGSMITH_API_KEY='...' \
  --from-literal=JWT_SECRET_KEY='...' \
  --from-literal=DATABASE_URL='postgresql+psycopg://cuti_user:PASSWORD@172.31.4.254:5432/cuti_storybook' \
  --from-literal=REDIS_URL='redis://172.31.4.254:6379/0' \
  --from-literal=SQS_QUEUE_URL='https://sqs.ap-southeast-2.amazonaws.com/699475938168/QUEUE_NAME' \
  --from-literal=MEDIA_SERVICE_URL='http://cuti-media-service.dev.svc.cluster.local:8080' \
  --from-literal=S3_BUCKET_NAME='cuti-agent-assets-dev-699475938168-ap-southeast-2' \
  --from-literal=CDN_DOMAIN='https://cdn-dev.newai.land' \
  --from-literal=AWS_ACCESS_KEY_ID='...' \
  --from-literal=AWS_SECRET_ACCESS_KEY='...'

# Prod 环境（类似，改对应的值）
```

> 注意：`MEDIA_SERVICE_URL` 使用 K8s Service DNS `http://cuti-media-service.dev.svc.cluster.local:8080`，同集群直连，不走外部 ALB。
> `DATABASE_URL` 和 `REDIS_URL` 使用 EC2 内网 IP `172.31.4.254`。

**执行记录：** ✅ 已执行（2026-03-10）。dev namespace Secret 已创建。

---

## 第 11 步：构建并推送 Docker 镜像

```bash
cd /home/songsong/local/Cuti-VideoAgent

# 登录 ECR
aws ecr get-login-password --region ap-southeast-2 | \
  docker login --username AWS --password-stdin \
  699475938168.dkr.ecr.ap-southeast-2.amazonaws.com

# 构建（在 EC2 上执行，需 ~5 分钟）
docker build -t cuti-videoagent:dev-local-002 .

# Tag + Push
docker tag cuti-videoagent:dev-local-002 \
  699475938168.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-videoagent:dev-local-002
docker push 699475938168.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-videoagent:dev-local-002
```

**执行记录：** ✅ 已执行（2026-03-10）。两次构建：`dev-local-001`（首次，有 static 问题）、`dev-local-002`（修复后成功）。

---

## 第 12 步：Helm 部署 Dev

```bash
cd /home/songsong/local/Cuti-VideoAgent

helm upgrade --install cuti-videoagent ./helm/cuti-videoagent \
  --namespace dev \
  -f ./helm/values-dev.yaml \
  --set image.tag=dev-local-002
```

### 验证

```bash
kubectl get pods -n dev -l app=cuti-videoagent
# NAME                               READY   STATUS    RESTARTS   AGE
# cuti-videoagent-5d7fb9d598-bbbsz   1/1     Running   0          11m

kubectl get hpa -n dev
# NAME              TARGETS       MINPODS   MAXPODS   REPLICAS
# cuti-videoagent   cpu: 0%/70%   1         4         1

kubectl get ingress -n dev
# NAME                      HOSTS   ADDRESS
# cuti-videoagent-ingress   *       internal-k8s-dev-cutivide-5472a19def-2102573271.ap-southeast-2.elb.amazonaws.com

# 测试健康检查（从 EC2 执行，VPC 内可达）
curl -s http://internal-k8s-dev-cutivide-5472a19def-2102573271.ap-southeast-2.elb.amazonaws.com:8000/health
```

**执行记录：** ✅ 已执行（2026-03-10）。Pod Running，health check 通过，HPA 正常工作。初始有 Redis `Connection closed by server` 日志，几秒后自动重连。LangSmith 404 是已有问题，不影响功能。

---

## 第 12.1 步：Redis protected-mode 修复

> 部署后发现 K8s Pod 连 Redis 报 `ConnectionResetError: Connection reset by peer`。

**原因：** Redis `bind 0.0.0.0` + 无密码 + `protected-mode yes`（默认），会拒绝所有非 loopback 连接。EC2 本地服务走 `127.0.0.1` 不受影响，只有 K8s Pod 从外部 IP 连才被拒。

```bash
# 从 localhost 执行（protected-mode 允许 loopback 管理）
redis-cli CONFIG SET protected-mode no    # 实时生效
redis-cli CONFIG REWRITE                  # 写入配置文件持久化

# 验证
redis-cli CONFIG GET protected-mode       # 应返回 "no"
redis-cli -h 172.31.4.254 ping            # 应返回 PONG
```

**执行记录：** ✅ 已执行（2026-03-10）。修复后 K8s Pod Redis 连接恢复正常，EC2 本地服务无影响。

---

## 第 12.2 步：ALB 监听端口改为 80

> 原 Helm ingress 模板 `listen-ports` 使用了 `service.port`（8000），导致 ALB 只监听 8000，URL 需要带 `:8000`。

改为标准端口 80：

```yaml
# helm/cuti-videoagent/templates/ingress.yaml
# 原来: alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": {{ .Values.service.port }}}]'
# 改为:
alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": 80}]'
```

```bash
helm upgrade cuti-videoagent ./helm/cuti-videoagent \
  --namespace dev -f ./helm/values-dev.yaml \
  --set image.tag=dev-local-002

# 验证（等 30 秒 ALB 更新 listener）
curl -s http://internal-k8s-dev-cutivide-xxx.ap-southeast-2.elb.amazonaws.com/health
# 端口 80 通，端口 8000 不通
```

**执行记录：** ✅ 已执行（2026-03-10）。ALB 现在监听端口 80，URL 不再需要 `:8000`。

---

## 第 12.3 步：Route53 私有域名

> ALB DNS 太长且可能变化，用 Route53 私有托管区创建短域名。

已有私有托管区 `cuti.internal`（Zone ID: `Z02796452TC8HCHKQ0U69`）。

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id Z02796452TC8HCHKQ0U69 \
  --change-batch '{
    "Changes": [{
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "videoagent-dev.cuti.internal",
        "Type": "CNAME",
        "TTL": 60,
        "ResourceRecords": [{
          "Value": "internal-k8s-dev-cutivide-5472a19def-2102573271.ap-southeast-2.elb.amazonaws.com"
        }]
      }
    }]
  }'
```

**Route53 域名映射：**

| 短域名 | 指向 | 用途 |
|--------|------|------|
| `media-dev.cuti.internal` | Media Service dev ALB | 已有 |
| `media.cuti.internal` | Media Service prod ALB | 已有 |
| `videoagent-dev.cuti.internal` | VideoAgent dev ALB | 新增 |
| `videoagent.cuti.internal` | VideoAgent prod ALB | 待 prod 部署后创建 |

**执行记录：** ✅ 已执行（2026-03-10）。`videoagent-dev.cuti.internal` 解析正常，health check 通过。

---

## 第 13 步：前端配置

### 13.1 .env.development.local（本地 npm run dev）

```bash
# Vite proxy target（仅 npm run dev 生效，build 后不存在）
VITE_CUTI_BACKEND_URL=http://videoagent-dev.cuti.internal
```

### 13.2 vite.config.ts 添加 /api/cv-v1 代理

```typescript
// server.proxy 中新增（放在 /api/cuti 之前）
'/api/cv-v1': {
  target: env.VITE_CUTI_BACKEND_URL,
  changeOrigin: true,
  secure: false,
  rewrite: (path) => path.replace(/^\/api\/cv-v1/, '/api/cuti'),
},
```

### 13.3 api.ts 改成环境变量控制

```typescript
// src/services/api.ts
// 原来: const CUTI_VIDEO_API_BASE_URL = '/api/cuti';
// 改为:
const CUTI_VIDEO_API_BASE_URL = import.meta.env.VITE_CUTI_VIDEO_API_BASE_URL || '/api/cuti';
```

### 13.4 env 文件加 VITE_CUTI_VIDEO_API_BASE_URL

| 文件 | 值 | 场景 |
|------|-----|------|
| `.env.development` | `/api/cv-v1` | dev build（dev.newai.land）走 K8s |
| `.env.production` | `/api/cuti` | prod build（newai.land）暂时走 EC2，K8s prod 就绪后改 `/api/cv-v1` |
| `.env.development.local` | 不需要加 | Vite proxy 已有 `/api/cv-v1` 规则 |

### 13.5 前端环境变量说明

| 变量 | `npm run dev` | `npm run build` | 说明 |
|------|---------------|-----------------|------|
| `VITE_BACKEND_URL` | Vite proxy target | OG meta 用 | 仅 proxy，不打入 JS |
| `VITE_CUTI_BACKEND_URL` | Vite proxy target | **不生效** | 仅 proxy，不打入 JS |
| `VITE_API_BASE_URL` | 打入 JS | 打入 JS | CartoonBook API base path |
| `VITE_CUTI_VIDEO_API_BASE_URL` | 打入 JS | 打入 JS | VideoAgent API base path |
| `VITE_BASE_PATH` | 不用 | 静态资源前缀 | build 时用 |

**执行记录：** ✅ 已执行（2026-03-10）。

### 13.6 测试

```bash
cd cuti-front-end-lovable
npm run dev
# 浏览器访问 http://localhost:8080
# 所有 /api/cuti/* 请求会被 proxy 到 K8s ALB
```

**执行记录：** ✅ 配置已完成（2026-03-10）。待用户运行 `npm run dev` 测试完整流程。

---

## 第 14 步：Nginx 加 /api/cv-v1/ 路由

> 只加新路由，老路由 `/api/cuti/` 不动。新路由无人使用，不影响现有流量。

### 14.1 备份

```bash
cp /etc/nginx/sites-enabled/cartoonbook-dev ~/cartoonbook-dev.bak.20260310
cp /etc/nginx/sites-enabled/cartoonbook ~/cartoonbook.bak.20260310
```

### 14.2 Dev Nginx（cartoonbook-dev，dev.newai.land）

文件头部加 upstream：

```nginx
# K8s VideoAgent dev ALB（Route53: videoagent-dev.cuti.internal）
upstream cuti_videoagent_eks_dev {
    server videoagent-dev.cuti.internal max_fails=3 fail_timeout=30s;
    keepalive 32;
}
```

在 `location /api/cuti/` 之前加新 location：

```nginx
# K8s VideoAgent — /api/cv-v1/ → EKS ALB /api/cuti/（新路由，老路由不动）
location /api/cv-v1/ {
    proxy_pass http://cuti_videoagent_eks_dev/api/cuti/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_connect_timeout 60s;
    proxy_send_timeout 1200s;
    proxy_read_timeout 1200s;
    proxy_buffering off;
}
```

### 14.3 Prod Nginx（cartoonbook，newai.land/cuti.land）— 待 K8s prod 部署后

```nginx
upstream cuti_videoagent_eks_prod {
    server videoagent.cuti.internal max_fails=3 fail_timeout=30s;
    keepalive 32;
}

location /api/cv-v1/ {
    proxy_pass http://cuti_videoagent_eks_prod/api/cuti/;
    # ... 同 dev
}
```

### 14.4 应用

```bash
sudo nginx -t && sudo nginx -s reload
```

**执行记录：**
- Dev（cartoonbook-dev）：✅ 配置已准备（/tmp/cartoonbook-dev.tmp），待手动执行 `sudo cp && sudo nginx -s reload`
- Prod（cartoonbook）：⏳ 待 K8s prod 部署后

### 14.5 ALB 换 IP 与 Nginx 动态解析（避免 502）

**每次 GitHub Actions 部署会换 ALB IP 吗？**

- **不会。** 当前 workflow 做的是 `helm upgrade --install`，只更新 Deployment 的镜像等，**不会删除/重建 Ingress**，所以 ALB 资源不变，DNS 名不变。
- **ALB 的 A 记录（IP）仍可能变化**，常见情况：
  - AWS 侧维护、多 AZ 调度或故障转移时，内部 ALB 的 IP 会更新；
  - 若有人执行过 `helm uninstall` 再 `helm install`，或删过 Ingress 再部署，会新建 ALB，拿到全新 IP。
- **Nginx 的坑**：用 `upstream { server videoagent-prod.cuti.internal; }` 时，Nginx 在**启动时**解析一次并缓存 IP。ALB 的 A 记录后续若变化，Nginx 仍用旧 IP → 连不上 → 502。只有 `reload`/`restart` 后才会重新解析。

**推荐**：对 `videoagent-prod.cuti.internal` 使用 **resolver + 变量** 做动态解析，让 Nginx 按 TTL 重新解析，ALB 换 IP 后无需再 reload。

**概念区分（为何用 EC2 的 DNS、谁会变谁不会变）：**

- **resolver 填的是谁？** Nginx 跑在 **EC2** 上，解析域名时只能向「EC2 能访问的 DNS 服务器」发请求，也就是 EC2 自己 `/etc/resolv.conf` 里配的 nameserver（通常是 VPC 默认 DNS 如 172.31.0.2，或本机 systemd-resolved 127.0.0.53）。**不是** K8s 集群里的 CoreDNS：EC2 和 Pod 不在同一网络，不会去问 K8s 的 DNS；`videoagent-prod.cuti.internal` 是 Route53 私有托管区里的记录，由 VPC 关联的 DNS（即 EC2 用的那个）解析到 ALB 的 IP。
- **会变的是谁？**  
  - **不会变**：**resolver 的 IP**（你「问谁」——DNS 服务器的地址）。在 AWS 里，VPC 的 DNS 是网段保留地址（如 172.31.0.2），同一 VPC 内固定；127.0.0.53 是本机，也不会变。  
  - **会变**：**问出来的结果**——即 `videoagent-prod.cuti.internal` 的 **A 记录（ALB 的 IP）**。ALB 多 AZ、扩缩或故障转移时，这些 IP 会变。用 `resolver` + 变量 + `valid=10s`，就是让 Nginx 每隔一段时间重新「问一次」DNS，拿到最新的 ALB IP，而不是启动时问一次就永远用旧 IP。

**为啥 dev 不常出这个问题？**  
机制上 dev 和 prod 一样（upstream 里写 hostname，启动时解析一次），理论上都会遇到 ALB 换 IP 后 Nginx 仍用旧 IP。dev 不常暴露出来，常见原因：（1）**prod 跑得久**，Nginx 很少 reload，ALB 的 A 记录一旦被 AWS 更新，prod 会一直用旧 IP 直到有人 reload；（2）**prod 流量大**，一换 IP 立刻大量 502，dev 请求少或刚巧最近 reload 过，窗口短；（3）dev 环境有时会频繁重启/重载（部署、调试），相当于经常刷新解析。建议 **dev 的 `/api/cv-v1/` 也改成 resolver + 变量**，和 prod 一致，避免以后 dev 也踩坑。

在 **server** 或 **http** 块中已有 `resolver` 时可复用；否则在 server 内加一行即可。下面用 **变量 + proxy_pass**，不再用 upstream 块。

**Prod 推荐配置（替换 14.3 的 upstream + location）：**

先在该 EC2 上确认本机用的 DNS，再填进配置，避免写错：

```bash
# 在 Nginx 所在 EC2 上执行，取第一个 nameserver 即可
grep nameserver /etc/resolv.conf | head -1
# 常见结果：nameserver 127.0.0.53（Ubuntu systemd-resolved）或 nameserver 172.31.0.2（VPC 默认 DNS）
```

```nginx
# 用上面命令得到的 nameserver IP 替换下面的 <RESOLVER_IP>（不要写死 172.31.0.2：仅当 VPC 为 172.31.0.0/16 时才是 .2，其他网段如 10.0.0.0/16 则为 10.0.0.2）
# resolver 的 IP 在同一个 VPC 内一般不会变；会变的是“被解析的” ALB 的 A 记录，用动态解析就是为了应对后者
resolver <RESOLVER_IP> valid=10s ipv6=off;

# 删掉 upstream cuti_videoagent_eks_prod 整块，改用下面 location

location /api/cv-v1/ {
    set $backend "videoagent-prod.cuti.internal";
    rewrite ^/api/cv-v1/(.*)$ /api/cuti/$1 break;
    proxy_pass http://$backend;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection "";
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_connect_timeout 60s;
    proxy_send_timeout 600s;
    proxy_read_timeout 600s;
    proxy_buffering on;
    proxy_buffer_size 8k;
    proxy_buffers 16 8k;
    proxy_busy_buffers_size 16k;
}
```

注意：

- `rewrite ... break` 把 `/api/cv-v1/xxx` 转为 `/api/cuti/xxx`，再通过 `proxy_pass http://$backend` 发给后端，与原来 upstream 的 `proxy_pass http://.../api/cuti/` 行为一致。
- `resolver` 必须填该 EC2 上 `grep nameserver /etc/resolv.conf` 得到的 IP，否则可能解析失败；`valid=10s` 表示对**后端域名**（如 videoagent-prod.cuti.internal）的解析结果缓存 10 秒，可在 10s～60s 间调整。
- 使用变量后 Nginx 不会对后端做 keepalive 连接池（按 TTL 重新解析），对内部 ALB 一般可接受；若流量极大再考虑保留 upstream + 定期 reload 或用其他方案。

改完后执行：

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 14.6 用 kubectl / DNS 看 ALB 与 Pod，以及如何复现与验证修复

**流量和 IP 的关系（谁在变）：**

```
用户 → Nginx（EC2）→ 解析 videoagent-prod.cuti.internal → 得到 ALB 的 IP（会变）→ 请求发到 ALB:80
                                                         → ALB 把请求转到 K8s Service → Pod（Pod IP 也会变，但 Nginx 不直接连 Pod）
```

- **会变且会导致 502 的**：是 **ALB 的 A 记录 IP**。Nginx 用 hostname 解析到的就是这些 IP；若解析结果被缓存，ALB 换 IP 后 Nginx 仍用旧 IP 就会 502。
- **Pod IP**：`kubectl get pods -o wide` 看到的是 Pod 的 IP，只在集群内用，Nginx 不直接连 Pod，所以 Pod 换 IP 不会直接导致 Nginx 502。看「是否修好」要盯的是 **ALB 的解析结果**，不是 Pod IP。

**用 kubectl / 本机 DNS 观察：**

```bash
# 1. 看 Ingress 和 ALB 的 DNS 名（prod）
kubectl get ingress -n prod
# 输出里 STATUS 下面会有类似 internal-k8s-prod-cutivide-xxx.ap-southeast-2.elb.amazonaws.com

# 2. 看 Pod 与 Pod IP（和 Nginx 502 无直接关系，仅作参考）
kubectl get pods -n prod -l app=cuti-videoagent -o wide
# 3. 在 Nginx 所在 EC2 上看「当前 DNS 解析出的 ALB IP」（这才是 Nginx 会连的）
getent hosts videoagent-prod.cuti.internal
# 或
dig +short videoagent-prod.cuti.internal
# 会得到多个 A 记录 IP，例如 172.31.6.178、172.31.40.104 等
```

dev 同理，把 `prod` 换成 `dev`，域名换成 `videoagent-dev.cuti.internal`。

**如何复现「旧 IP 导致 502」：**

思路：让 ALB 的 IP 发生变化，但**不 reload Nginx**，这样 Nginx 会继续用启动时缓存的旧 IP，请求就会 502。

1. **记下当前状态**（在 Nginx EC2 上）：
   ```bash
   getent hosts videoagent-prod.cuti.internal   # 记下当前 IP 列表，例如 172.31.6.178
   ```
2. **触发 ALB 换 IP**：删掉 Ingress 让 AWS 删掉 ALB，再重新部署让控制器新建 ALB（会得到新 IP）：
   ```bash
   kubectl delete ingress cuti-videoagent-ingress -n prod
   # 等 1～2 分钟让 ALB 被删掉，再重新部署
   helm upgrade --install cuti-videoagent ./helm/cuti-videoagent -n prod -f ./helm/values-prod.yaml --set image.repository=... --set image.tag=...
   ```
3. **不要 reload Nginx**。等新 ALB 起来后，再在 EC2 上执行：
   ```bash
   getent hosts videoagent-prod.cuti.internal   # 会变成新 IP，例如 172.31.20.xxx
   ```
4. 此时 Nginx 里缓存的仍是**旧 ALB 的 IP**（已不存在或不可用），对 `https://cuti.land/api/cv-v1/...` 发请求会 **502**。  
   （若不想动 prod，可在 **dev** 用同样步骤：删 dev 的 ingress，再 helm 部署 dev，不 reload 对应 Nginx server 块，用 dev 域名复现。）

**如何确认 resolver 修复有效：**

1. 已按 14.5 把 prod（或 dev）的 `/api/cv-v1/` 改成 **resolver + 变量**，并 `nginx -t && systemctl reload nginx`。
2. 用上面步骤让 ALB 换 IP，且**不再次 reload Nginx**。
3. 等几秒（超过你设的 `valid=10s` 即可）。
4. 再请求 `https://cuti.land/api/cuti/...`（或 dev 对应域名）。  
   **预期**：最多约 10 秒后，新请求会重新解析到新 ALB IP，返回 200（或业务正常），而不再持续 502。  
   若仍 502，检查：`resolver` 是否写对、该 server 块是否已 reload、`valid=` 是否过短导致解析过于频繁（可先试 30s）。

**简要对照：**

| 看什么 | 命令 | 说明 |
|--------|------|------|
| Ingress / ALB 名 | `kubectl get ingress -n prod` | 看 ALB 的 DNS 名 |
| Pod 与 Pod IP | `kubectl get pods -n prod -o wide` | 集群内用，Nginx 不直连 |
| 当前 ALB IP（Nginx 会连的） | 在 Nginx EC2 上 `getent hosts videoagent-prod.cuti.internal` | 会变的、导致 502 的是这些 IP |
| 复现 502 | 删 Ingress → 再部署 → 不 reload Nginx → 请求 /api/cv-v1 | 旧 IP 失效 → 502 |
| 验证修复 | 同上，但已用 resolver+变量；等 valid 秒后再请求 | 应自动用新 IP，不再 502 |

---

## 第 15 步：GitHub Actions CI/CD

### 工作流文件

- `.github/workflows/deploy-dev.yml` — 手动触发，支持选择分支（默认 `dev`）
- `.github/workflows/deploy-prod.yml` — 手动触发，支持选择分支（默认 `main`）

### GitHub Repo Secrets 配置

在 GitHub repo → Settings → Secrets 中添加：

| Secret | 值 |
|--------|-----|
| `AWS_ACCESS_KEY_ID` | cuti-dev IAM 用户的 Access Key |
| `AWS_SECRET_ACCESS_KEY` | cuti-dev IAM 用户的 Secret Key |

---

## 第 16 步：EBS CSI 驱动（动态块存储 / gp2 PVC）

> 背景：EKS 在开启 CSIMigration 后，`StorageClass gp2` 的动态卷由 **`ebs.csi.aws.com`** 创建。若集群未安装 **AWS EBS CSI**（`kubectl get csidriver` 无 `ebs.csi.aws.com`），PVC 会一直 **Pending**，事件中会出现 *Waiting for a volume to be created ... ebs.csi.aws.com*。  
> 与业务 Pod 无关：安装 CSI 只增加 `kube-system` 内控制器与 node DaemonSet，**不会**批量重启 `dev` / `prod` 应用 Pod。

### 仓库脚本（幂等）

在已配置 `aws` CLI 的机器上，于仓库根目录执行：

```bash
export EKS_CLUSTER_NAME=cuti-cluster
export AWS_REGION=ap-southeast-2
./scripts/ensure-ebs-csi-addon.sh
```

脚本行为概要：

| 动作 | 说明 |
|------|------|
| IAM OIDC Provider | 若账户中尚无本集群 Issuer 对应 Provider，则创建（多数集群已存在） |
| IAM 角色 | `cuti-cluster-ebs-csi-controller`（可用环境变量 `EBS_CSI_ROLE_NAME` 覆盖），信任 `kube-system/ebs-csi-controller-sa`，附加托管策略 `AmazonEBSCSIDriverPolicy` |
| EKS Add-on | 安装或补齐 **`aws-ebs-csi-driver`** 托管插件并绑定上述 IRSA；已 **ACTIVE** 且已绑角色时不会做破坏性修改 |

执行所需 IAM 权限（示例）：`eks:DescribeCluster` / `DescribeAddon` / `CreateAddon` / `UpdateAddon`，`iam` 侧创建/查询 OIDC Provider 与 Role、`AttachRolePolicy`，`sts:GetCallerIdentity`。

### 验证

```bash
aws eks update-kubeconfig --name cuti-cluster --region ap-southeast-2
kubectl get csidriver ebs.csi.aws.com
kubectl get pods -n kube-system -o wide | grep ebs-csi
```

### 与日志栈（Loki）的关系

- `helm/logging/loki-values.yaml` 里 Loki / MinIO 使用 **gp2** PVC，**须先**能动态供给 EBS。  
- 装好 EBS CSI 后，再跑 `.github/workflows/deploy-logging.yml` 或本地等价 `helm upgrade --install`。

### 新集群

每新建一个 EKS 集群，需 **对该集群再执行一次** 上述脚本（改 `EKS_CLUSTER_NAME`）；角色名默认 `{集群名}-ebs-csi-controller`，避免多集群共用同名角色产生混淆。

**执行记录：** ✅ **2026-04-01** 已在账户 `699475938168`、集群 `cuti-cluster`、区域 `ap-southeast-2` 执行：创建 IAM 角色 `cuti-cluster-ebs-csi-controller`，安装托管 Add-on `aws-ebs-csi-driver`（如 `v1.57.1-eksbuild.1`）；`monitoring` 内 Loki / MinIO 相关 **gp2** PVC 已 **Bound**，既有 VideoAgent 业务 Pod 未因此次操作重启。

---

## 第 17 步：日志栈（Loki / Promtail / Grafana）各是什么、怎么看日志

### 三个东西分别干什么

| 组件 | 作用 |
|------|------|
| **Loki** | 存日志、按标签检索；类似「日志专用数据库」，本身不替你收日志。 |
| **Promtail** | 跑在每个节点上的 **DaemonSet**，读容器标准输出/文件，**推**到 Loki。没有它，Loki 里是空的。 |
| **Grafana** | 网页控制台：**Explore** 里用 LogQL 查 Loki；**Dashboard** 是可选的图表/大盘，**不是**看日志的必经步骤。 |

（可选）**MinIO**：给 Loki 当 S3 兼容对象存储，存块与索引；和「在网页上看日志」无直接关系。

### 为什么打开 Grafana 却「什么都没有」

- **首页（Home）** 默认没有预装大盘，看起来会像「空的」，**正常**。  
- **看日志要去 Explore**，不是去 Dashboards 列表里找（除非你以后自己导入大盘）。  
- 右上角时间范围要覆盖「有日志」的时段，例如 **Last 15 minutes** / **Last 1 hour**；不要停在「某一秒」或很久以前。

### 在 Grafana 里查日志（推荐操作顺序）

1. 本机先跑：`./scripts/port-forward-grafana.sh`，浏览器打开打印的地址并登录。  
2. 左侧菜单点 **Explore**（罗盘/指南针图标；新版 Grafana 在左侧栏）。  
3. 顶部数据源下拉框选 **Loki**。  
4. 查询框输入 **LogQL**（可先复制下面一条试）：  
   - 任意命名空间有输出即可看见：`{namespace=~".+"}`  
   - 只看 dev：`{namespace="dev"}`  
   - 只看 prod：`{namespace="prod"}`  
   - 只看 monitoring 自己：`{namespace="monitoring"}`  
5. 点 **Run query**（或等自动刷新）。应出现日志行；可再点某条展开字段。  
6. **Connections → Data sources → Loki → Save & test** 应为绿色；若失败，检查 `monitoring` 内 `loki-gateway` 是否 Running。

### Loki 多租户 401（Promtail 推不上去）

若 Promtail 日志里出现 `401 ... no org id`，说明 Gateway 在多租户模式下要求 `X-Scope-OrgID`。本仓库 **`helm/logging/loki-values.yaml`** 已设 **`loki.auth_enabled: false`**（单集群内网场景）；改完后需对集群执行 `helm upgrade loki ...` 使配置生效。

---

## 后续步骤（待执行）

| # | 任务 | 说明 |
|---|------|------|
| 1 | Nginx dev reload | `sudo cp /tmp/cartoonbook-dev.tmp /etc/nginx/sites-enabled/cartoonbook-dev && sudo nginx -t && sudo nginx -s reload` |
| 2 | 前端 dev build + 部署 | `.env.development` 已配 `/api/cv-v1`，build 后走 K8s |
| 3 | `npm run dev` 完整流程测试 | 创建视频任务 → 确认走 K8s → 结果正确 |
| 4 | 验证 static 目录无人使用 | 如果步骤 3 通过，可安全删除 EC2 上 232GB 数据 |
| 5 | 长任务测试 | 跑 1 小时任务 + 中间部署新版本，验证旧 Pod 不被中断 |
| 6 | K8s prod 部署 | Helm install prod namespace + Route53 `videoagent.cuti.internal` |
| 7 | Nginx prod 加路由 | `cartoonbook` 加 `/api/cv-v1/` 指向 prod ALB |
| 8 | 前端 prod 切换 | `.env.production` 改 `VITE_CUTI_VIDEO_API_BASE_URL=/api/cv-v1` + build |
| 9 | 稳定一周后停 EC2 上的 VideoAgent | 可选 |

### 前端切换与回滚

```
切到 K8s:  VITE_CUTI_VIDEO_API_BASE_URL=/api/cv-v1  → build → 部署
切回 EC2:  VITE_CUTI_VIDEO_API_BASE_URL=/api/cuti   → build → 部署（秒级回滚）

Nginx 改动（验证通过后才执行）:

```nginx
location /api/cv-v1/ {
    proxy_pass http://cuti_videoagent_eks/api/cuti/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_connect_timeout 60s;
    proxy_send_timeout 1200s;
    proxy_read_timeout 1200s;
}
```

### 前端切换（改一个变量）

```typescript
// src/services/api.ts
// 切到 K8s:
const CUTI_VIDEO_API_BASE_URL = '/api/cv-v1';
// 切回 EC2:
const CUTI_VIDEO_API_BASE_URL = '/api/cuti';
```

---

## 回滚策略

| 场景 | 操作 | 耗时 |
|------|------|------|
| K8s 部署有问题 | `helm rollback cuti-videoagent 1 -n dev` | 秒级 |
| 前端切回 EC2 | `CUTI_VIDEO_API_BASE_URL` 改回 `/api/cuti` + rebuild | 分钟级 |
| K8s 整体故障 | EC2 上 VideoAgent 一直在跑，不受影响 | 零切换 |

---

## 运维常用命令

```bash
# 查看 Pod 状态
kubectl get pods -n dev -l app=cuti-videoagent
kubectl get hpa -n dev

# 查看日志
kubectl logs -f -l app=cuti-videoagent -n dev --tail=100

# 资源使用
kubectl top pods -n dev

# 更新部署
helm upgrade cuti-videoagent ./helm/cuti-videoagent \
  --namespace dev -f helm/values-dev.yaml \
  --set image.tag=<new-tag>

# 回滚
helm rollback cuti-videoagent 1 -n dev

# 手动扩缩容
kubectl scale deployment cuti-videoagent --replicas=3 -n dev

# 排障
kubectl describe pod <pod-name> -n dev
kubectl exec -it <pod-name> -n dev -- bash

# 查看 ALB DNS
kubectl get ingress -n dev cuti-videoagent-ingress -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

---

## 费用估算

| 项目 | 月费 | 说明 |
|------|------|------|
| agent-workers Node (m6i.large x1) | ~$85 | min=1, max=4，空闲 1 台 |
| ALB (新建 internal) | ~$18 | VideoAgent 专用 |
| ECR 存储 | ~$1 | 镜像存储 |
| **新增总计** | **~$103/月** | EKS 控制面和 media-workers 已有 |

> Cluster 控制面 ($73) 和 media-workers Node 费用由 Media Service 承担，VideoAgent 只新增 agent-workers Node + ALB。

---

## 超长任务处理机制

| 配置项 | 值 | 说明 |
|--------|-----|------|
| K8s `terminationGracePeriodSeconds` | `10800`（3小时） | 旧 Pod 最多等 3 小时跑完任务 |
| Pod annotation `safe-to-evict` | `"false"` | Cluster Autoscaler 不会驱逐 |
| `task_worker.py` stop() timeout | `10500s`（2h55m） | 留 5 分钟余量给 K8s 清理 |
| Rolling Update `maxSurge: 1, maxUnavailable: 0` | — | 先起新 Pod 再关旧 Pod |

**部署时行为：**
1. 新 Pod 启动，开始从 SQS 消费新任务
2. 旧 Pod 收到 SIGTERM → `TaskWorker.stop()` → `self.running = False` → 主循环退出，不再拉新消息
3. 只等当前正在执行的那一个任务自然完成
4. 没有活跃任务 → 旧 Pod 几秒内退出
5. 不会接新任务导致无限延长
