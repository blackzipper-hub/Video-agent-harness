# Cuti-Media-Service AWS 部署指南

> 目标：将 Media Service 部署到 AWS EKS（Kubernetes），通过内部 ALB 供 EC2 上的 VideoAgent 调用。

## 当前环境

| 资源 | 值 |
|------|-----|
| AWS 账户 | `699475938168` |
| 区域 | `ap-southeast-2` (Sydney) |
| IAM 用户 | `cuti-dev` |
| VPC | `vpc-0cc2ca6f9f8545227` (默认 VPC, `172.31.0.0/16`) |
| EC2 | `i-00656af7c396635b2` (t3.xlarge, `172.31.4.254`) |
| Subnet | `2b: subnet-0ffcdae24cf683e02` / `2a: subnet-0bf453fe72ddec405` / `2c: subnet-0f3c38b136b0568d2` |
| S3 (dev) | `cuti-agent-assets-dev-699475938168-ap-southeast-2` |
| S3 (prod) | `cuti-agent-assets-prod-699475938168-ap-southeast-2` |
| CDN (dev) | `https://cdn-dev.newai.land` |
| CDN (prod) | `https://cdn.newai.land` |
| EKS | **没有**，需要新建 |
| ECR | 只有 CDK bootstrap 的，业务用的需要新建 |

---

## 架构概览

```
┌─────────────────── AWS VPC (172.31.0.0/16) ───────────────────┐
│                                                                │
│  ┌─────────┐     ┌────────────────┐     ┌──────────────────┐  │
│  │  EC2    │────▶│ Internal ALB   │────▶│  EKS Cluster     │  │
│  │ Video   │     │ (VPC内部,不对  │     │                  │  │
│  │ Agent   │     │  外网暴露)     │     │ namespace: dev   │  │
│  │ t3.xl   │     │                │     │ ┌──────────────┐ │  │
│  └─────────┘     └────────────────┘     │ │ media-svc    │ │  │
│                                          │ │ Pod x2 (HPA)│ │  │
│  ┌─────────┐                            │ └──────────────┘ │  │
│  │   S3    │◀───────────────────────────│                  │  │
│  │ Bucket  │                            │ namespace: prod  │  │
│  └─────────┘                            │ ┌──────────────┐ │  │
│                                          │ │ media-svc    │ │  │
│                                          │ │ Pod x2 (HPA)│ │  │
│                                          │ └──────────────┘ │  │
│                                          └──────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

- **EC2 VideoAgent → 内部 ALB → EKS Pod**（全部在同一 VPC，延迟 <1ms，传输免费）
- 同一 EKS 集群通过 **namespace** 区分 dev / prod
- 以后 VideoAgent 也迁入 K8s 后，直接用 K8s Service DNS 互调（去掉 ALB）

---

## 工具安装

> 这些是**管理工具**，装在你日常操作的机器上（本地电脑 或 AWS CloudShell 都行）。
> **不需要装在 EC2 上。** 部署完成后 EC2 只需要 `httpx` 发 HTTP 请求。

```bash
# ===== 以下在你的本地电脑或 CloudShell 执行 =====

# 1. AWS CLI（你已有，跳过）
aws --version   # 确认已安装
aws sts get-caller-identity   # 确认是 699475938168

# 2. kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
chmod +x kubectl && sudo mv kubectl /usr/local/bin/
kubectl version --client

# 3. eksctl
curl -sLO "https://github.com/eksctl-io/eksctl/releases/latest/download/eksctl_Linux_amd64.tar.gz"
tar -xzf eksctl_Linux_amd64.tar.gz && sudo mv eksctl /usr/local/bin/
eksctl version

# 4. helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version

# 5. Docker（构建镜像用，你已有就跳过）
docker --version
```

---

## 第 1 步：创建 ECR 镜像仓库

```bash
aws ecr create-repository \
  --repository-name cuti-media-service \
  --region ap-southeast-2 \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256

# 返回的 repositoryUri:
# 699475938168.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-media-service
```

设置生命周期策略（自动清理旧镜像，保留最近 20 个）：

```bash
aws ecr put-lifecycle-policy \
  --repository-name cuti-media-service \
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

---

## 第 2 步：构建并推送 Docker 镜像

```bash
cd /path/to/Cuti-Media-Service

# 登录 ECR
aws ecr get-login-password --region ap-southeast-2 | \
  docker login --username AWS --password-stdin \
  699475938168.dkr.ecr.ap-southeast-2.amazonaws.com

# 构建
docker build -t cuti-media-service:v0.1.0 .

# Tag + Push
docker tag cuti-media-service:v0.1.0 \
  699475938168.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-media-service:v0.1.0
docker tag cuti-media-service:v0.1.0 \
  699475938168.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-media-service:latest

docker push 699475938168.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-media-service:v0.1.0
docker push 699475938168.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-media-service:latest
```

---

## 第 3 步：创建 EKS 集群

> 耗时约 15-20 分钟。在你的**默认 VPC** 中创建，确保跟 EC2 互通。

将集群配置保存到项目中（版本管理）：

```bash
cat > /path/to/Cuti-Media-Service/infra/eks-cluster.yaml << 'CLUSTEREOF'
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig

metadata:
  name: cuti-cluster
  region: ap-southeast-2

# 复用 EC2 所在的默认 VPC
vpc:
  id: "vpc-0cc2ca6f9f8545227"
  subnets:
    public:
      ap-southeast-2a: { id: "subnet-0bf453fe72ddec405" }
      ap-southeast-2b: { id: "subnet-0ffcdae24cf683e02" }
      ap-southeast-2c: { id: "subnet-0f3c38b136b0568d2" }
    # eksctl 会自动创建私有子网（EKS 节点跑在私有子网）

managedNodeGroups:
  - name: media-workers
    instanceType: c6i.xlarge      # 4 vCPU, 8 GB — FFmpeg 需要算力
    desiredCapacity: 2
    minSize: 2
    maxSize: 6
    volumeSize: 50                # EBS 50GB
    volumeType: gp3
    privateNetworking: true       # 节点跑在私有子网
    labels:
      workload: media-processing
    tags:
      Project: cuti-media-service
    iam:
      withAddonPolicies:
        albIngress: true
        cloudWatch: true

iam:
  withOIDC: true                  # 启用 IRSA（Pod 级别 IAM 权限）

addons:
  - name: vpc-cni
  - name: coredns
  - name: kube-proxy

cloudWatch:
  clusterLogging:
    enableTypes: ["api", "audit", "authenticator"]
CLUSTEREOF
```

执行创建：

```bash
eksctl create cluster -f /path/to/Cuti-Media-Service/infra/eks-cluster.yaml

# 完成后验证
aws eks update-kubeconfig --name cuti-cluster --region ap-southeast-2
kubectl get nodes    # 应该看到 2 个节点
```

---

## 第 4 步：安装 AWS Load Balancer Controller

> 让 EKS 能创建 AWS ALB 的必要组件。

```bash
# 下载 IAM Policy
curl -O https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.7.1/docs/install/iam_policy.json

aws iam create-policy \
  --policy-name AWSLoadBalancerControllerIAMPolicy \
  --policy-document file://iam_policy.json

# 创建 Service Account
eksctl create iamserviceaccount \
  --cluster=cuti-cluster \
  --namespace=kube-system \
  --name=aws-load-balancer-controller \
  --attach-policy-arn=arn:aws:iam::699475938168:policy/AWSLoadBalancerControllerIAMPolicy \
  --approve

# Helm 安装
helm repo add eks https://aws.github.io/eks-charts
helm repo update

helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=cuti-cluster \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller

# 验证
kubectl get deployment -n kube-system aws-load-balancer-controller
```

---

## 第 5 步：创建 Namespace + Secret（Dev / Prod 分离）

```bash
# 创建 namespace
kubectl create namespace dev
kubectl create namespace prod

# ===== Dev 环境 =====
kubectl create secret generic cuti-media-service-secrets \
  --namespace=dev \
  --from-literal=S3_BUCKET=cuti-agent-assets-dev-699475938168-ap-southeast-2 \
  --from-literal=S3_CDN_PREFIX=https://cdn-dev.newai.land

# ===== Prod 环境 =====
kubectl create secret generic cuti-media-service-secrets \
  --namespace=prod \
  --from-literal=S3_BUCKET=cuti-agent-assets-prod-699475938168-ap-southeast-2 \
  --from-literal=S3_CDN_PREFIX=https://cdn.newai.land
```

---

## 第 6 步：配置 S3 访问权限（IRSA）

```bash
# 创建 IAM Policy
cat > /tmp/media-s3-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
    "Resource": [
      "arn:aws:s3:::cuti-agent-assets-*",
      "arn:aws:s3:::cuti-agent-assets-*/*"
    ]
  }]
}
EOF

aws iam create-policy \
  --policy-name CutiMediaServiceS3Policy \
  --policy-document file:///tmp/media-s3-policy.json

# Dev namespace
eksctl create iamserviceaccount \
  --cluster=cuti-cluster --namespace=dev \
  --name=cuti-media-service-sa \
  --attach-policy-arn=arn:aws:iam::699475938168:policy/CutiMediaServiceS3Policy \
  --approve

# Prod namespace
eksctl create iamserviceaccount \
  --cluster=cuti-cluster --namespace=prod \
  --name=cuti-media-service-sa \
  --attach-policy-arn=arn:aws:iam::699475938168:policy/CutiMediaServiceS3Policy \
  --approve
```

---

## 第 7 步：创建 Helm values 文件（Dev / Prod）

```bash
# Dev values — 少副本、低资源
cat > /path/to/Cuti-Media-Service/helm/values-dev.yaml << 'EOF'
replicaCount: 1

image:
  repository: 699475938168.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-media-service
  tag: latest

resources:
  requests:
    cpu: "500m"
    memory: "1Gi"
  limits:
    cpu: "1000m"
    memory: "2Gi"

autoscaling:
  enabled: true
  minReplicas: 1
  maxReplicas: 5
  targetCPUUtilizationPercentage: 70

workspace:
  sizeLimit: 10Gi

env:
  ENVIRONMENT: development
  LOG_LEVEL: debug
  PORT: "8080"
  FFMPEG_THREADS: "2"
  MAX_CONCURRENT_JOBS: "2"
  WORKSPACE_BASE: /workspace
  WORKSPACE_TTL_HOURS: "1"
  AWS_REGION: ap-southeast-2
EOF

# Prod values — 正式配置
cat > /path/to/Cuti-Media-Service/helm/values-prod.yaml << 'EOF'
replicaCount: 2

image:
  repository: 699475938168.dkr.ecr.ap-southeast-2.amazonaws.com/cuti-media-service
  tag: latest

resources:
  requests:
    cpu: "1000m"
    memory: "2Gi"
  limits:
    cpu: "2000m"
    memory: "4Gi"

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 20
  targetCPUUtilizationPercentage: 60

workspace:
  sizeLimit: 20Gi

env:
  ENVIRONMENT: production
  LOG_LEVEL: info
  PORT: "8080"
  FFMPEG_THREADS: "2"
  MAX_CONCURRENT_JOBS: "3"
  WORKSPACE_BASE: /workspace
  WORKSPACE_TTL_HOURS: "2"
  AWS_REGION: ap-southeast-2
EOF
```

---

## 第 8 步：Helm 部署

```bash
cd /path/to/Cuti-Media-Service

# ===== 部署 Dev =====
helm install cuti-media-service ./helm/cuti-media-service \
  --namespace dev \
  -f helm/values-dev.yaml

# ===== 部署 Prod =====
helm install cuti-media-service ./helm/cuti-media-service \
  --namespace prod \
  -f helm/values-prod.yaml

# 验证
kubectl get pods -n dev
kubectl get pods -n prod
kubectl get hpa -n dev
kubectl get hpa -n prod
```

---

## 第 9 步：创建内部 ALB Ingress

```bash
# ===== Dev Ingress =====
cat > /tmp/ingress-dev.yaml << 'EOF'
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: cuti-media-service-ingress
  namespace: dev
  annotations:
    alb.ingress.kubernetes.io/scheme: internal
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": 8080}]'
    alb.ingress.kubernetes.io/healthcheck-path: /healthz
    alb.ingress.kubernetes.io/tags: Environment=dev,Project=cuti-media-service
spec:
  ingressClassName: alb
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: cuti-media-service
                port:
                  number: 8080
EOF

# ===== Prod Ingress =====
cat > /tmp/ingress-prod.yaml << 'EOF'
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: cuti-media-service-ingress
  namespace: prod
  annotations:
    alb.ingress.kubernetes.io/scheme: internal
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": 8080}]'
    alb.ingress.kubernetes.io/healthcheck-path: /healthz
    alb.ingress.kubernetes.io/tags: Environment=prod,Project=cuti-media-service
spec:
  ingressClassName: alb
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: cuti-media-service
                port:
                  number: 8080
EOF

kubectl apply -f /tmp/ingress-dev.yaml
kubectl apply -f /tmp/ingress-prod.yaml

# 等 1-2 分钟，获取 ALB 地址
kubectl get ingress -n dev
kubectl get ingress -n prod
```

从 EC2 测试（SSH 到 EC2）：

```bash
# Dev
curl -s http://<dev-ALB-DNS>:8080/healthz
# Prod
curl -s http://<prod-ALB-DNS>:8080/healthz
```

---

## ~~第 10 步：CloudWatch Logs（Fluent Bit）~~ — 已移除

> **已于 2026-03-09 移除**，改用 Kubernetes Dashboard 查看日志。
>
> 清理内容：Fluent Bit DaemonSet、ConfigMap、ServiceAccount、ClusterRole/Binding、`amazon-cloudwatch` namespace 均已删除。
> IAM 资源（`FluentBitCloudWatchPolicy`、`FluentBitCloudWatchRole`）可在 AWS Console 中手动清理，也可保留不影响费用。

---

## 第 11 步：Kubernetes Dashboard

集群级 Web UI，可查看 Pod 日志、扩缩容、重启 Deployment。

### 11.1 安装 Dashboard

```bash
# 安装官方 Dashboard v2.7.0
kubectl apply -f https://raw.githubusercontent.com/kubernetes/dashboard/v2.7.0/aio/deploy/recommended.yaml

# 创建 admin 用户（YAML 在 infra/dashboard/admin-user.yaml）
kubectl apply -f infra/dashboard/admin-user.yaml
```

### 11.2 获取登录 Token

```bash
# Token 有效期 24h（K8s 限制），过期后重新生成
kubectl -n kubernetes-dashboard create token dashboard-admin
```

### 11.3 访问 Dashboard

**方式 A：EC2 端口转发（推荐）**

```bash
# 在 EC2 上执行，后台运行
kubectl -n kubernetes-dashboard port-forward svc/kubernetes-dashboard 9443:443 --address=0.0.0.0 &

# 浏览器访问
# https://<EC2-公网IP>:9443
# 选择 Token 登录，粘贴上面生成的 token
```

> 注意：需要 EC2 安全组放行 9443 端口（入站 TCP 9443）。8443 被已有服务占用。
> 浏览器会提示证书不安全（自签证书），点"高级 → 继续访问"即可。

**方式 B：本地 SSH 隧道（更安全，不需开端口）**

```bash
# 在本地终端执行
ssh -L 9443:localhost:9443 ubuntu@<EC2-IP> \
  "kubectl -n kubernetes-dashboard port-forward svc/kubernetes-dashboard 9443:443"

# 浏览器访问 https://localhost:9443
```

### 11.4 Dashboard 能做什么

| 功能 | 操作 |
|------|------|
| 看 Pod 日志 | Workloads → Pods → 点击 Pod → Logs 图标 |
| 扩缩容 | Workloads → Deployments → 点击 → Scale |
| 重启 | Workloads → Deployments → ⋮ 菜单 → Restart |
| 编辑 YAML | 任何资源 → Edit |
| 查看事件 | Cluster → Events |

### 11.5 资源占用

- Dashboard Pod：~100Mi 内存，~50m CPU
- Metrics Scraper：~40Mi 内存
- 成本：**$0**（集群内部署）

---

## 第 12 步：配置 VideoAgent

在 EC2 上的 VideoAgent 环境变量中添加：

```bash
# .env 或系统环境变量
MEDIA_SERVICE_URL=http://<dev-ALB-DNS>:8080    # dev 环境
# MEDIA_SERVICE_URL=http://<prod-ALB-DNS>:8080  # prod 环境
```

VideoAgent Python 调用：

```python
import httpx
import os

MEDIA_SERVICE_URL = os.getenv("MEDIA_SERVICE_URL", "http://localhost:8080")

async def call_media_service(endpoint: str, payload: dict, timeout: int = 300) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{MEDIA_SERVICE_URL}/api/v1/{endpoint}", json=payload)
        resp.raise_for_status()
        return resp.json()
```

> 只需要这一个环境变量。不需要前缀、不需要鉴权（VPC 内部，不对外暴露）。

---

## 本地调试方案

### 方式 1：docker-compose（推荐）

```bash
cd Cuti-Media-Service

# 编辑 .env 填入 AWS 凭证和 S3 配置
cp .env.example .env

docker-compose up --build
# Media Service 跑在 localhost:8080
```

VideoAgent 本地开发时：
```bash
MEDIA_SERVICE_URL=http://localhost:8080
```

### 方式 2：直接运行

```bash
cd Cuti-Media-Service
pip install -r requirements.txt
cp .env.example .env   # 编辑填入配置

uvicorn app.main:app --reload --port 8080
```

### 方式 3：Mock（不连真实 S3）

本地调试不想操作 S3 时，可以修改 `.env`：
```bash
ENVIRONMENT=development
S3_BUCKET=
S3_CDN_PREFIX=
```
Media Service 会正常处理 FFmpeg，只是 S3 上传/下载会失败（可以单独 mock）。

---

## GitHub Actions CI/CD 说明

```
手动做一次（第 1-9 步）           日常开发（自动）
┌───────────────────┐          ┌──────────────────────────┐
│ 创建 ECR          │          │ git push to main         │
│ 创建 EKS 集群     │          │    ↓                     │
│ 安装 ALB Controller│  ──→    │ GitHub Actions 自动触发  │
│ 创建 Namespace     │  一次性  │    ↓ 跑测试             │
│ 创建 Secret        │  搭建   │    ↓ docker build        │
│ 创建 Ingress       │          │    ↓ push ECR            │
└───────────────────┘          │    ↓ helm upgrade (EKS)  │
                                │    ↓ 部署完成            │
                                └──────────────────────────┘
```

- 第 1-9 步：**一次性基础设施搭建**，手动做
- GitHub Actions：搭建完成后，每次 `git push main` **自动**构建 + 部署
- 本地开发/测试不走 CI/CD，用 `docker-compose` 或 `uvicorn --reload`

GitHub Actions 需要在 repo Settings → Secrets 中配置 `AWS_ROLE_ARN`（见下方）。

### 配置 GitHub OIDC（让 Actions 能操作 AWS）

```bash
# 1. 添加 GitHub OIDC Provider（只做一次）
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1

# 2. 创建 Role（替换 your-org/Cuti-Media-Service 为你的 GitHub repo）
cat > /tmp/github-trust.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::699475938168:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:your-org/Cuti-Media-Service:*"
      }
    }
  }]
}
EOF

aws iam create-role \
  --role-name GitHubActionsEKSDeployRole \
  --assume-role-policy-document file:///tmp/github-trust.json

# 3. 附加权限
aws iam attach-role-policy --role-name GitHubActionsEKSDeployRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser

# EKS 权限
eksctl create iamidentitymapping \
  --cluster cuti-cluster \
  --arn arn:aws:iam::699475938168:role/GitHubActionsEKSDeployRole \
  --group system:masters \
  --username github-actions

# 4. 在 GitHub repo → Settings → Secrets 中添加：
#    AWS_ROLE_ARN = arn:aws:iam::699475938168:role/GitHubActionsEKSDeployRole
```

---

## 运维常用命令

```bash
# ====== 查看状态 ======
kubectl get pods -n dev -l app=cuti-media-service
kubectl get hpa -n dev
kubectl logs -f -l app=cuti-media-service -n dev --tail=100
kubectl top pods -n dev

# ====== 更新部署 ======
helm upgrade cuti-media-service ./helm/cuti-media-service \
  --namespace dev -f helm/values-dev.yaml \
  --set image.tag=v0.1.1

# ====== 回滚 ======
helm rollback cuti-media-service 1 -n dev

# ====== 扩缩容 ======
kubectl scale deployment cuti-media-service --replicas=5 -n dev
eksctl scale nodegroup --cluster=cuti-cluster --name=media-workers --nodes=4

# ====== 排障 ======
kubectl describe pod <pod-name> -n dev
kubectl exec -it <pod-name> -n dev -- bash
kubectl exec -it <pod-name> -n dev -- ffmpeg -version
```

---

## 费用估算

| 资源 | Dev | Prod |
|------|-----|------|
| EKS 控制面 | $73 (共享) | $73 (共享) |
| 节点 c6i.xlarge x2 | ~$230 | ~$230 |
| 内部 ALB | ~$25 | ~$25 |
| ECR | ~$0.5 | ~$0.5 |
| VPC 内传输 | $0 | $0 |
| **单环境合计** | **~$330/月** | **~$330/月** |

> 同一集群的 dev + prod 共享节点：实际约 **$330-400/月**（不是 x2）
> 可用 **Spot 实例** 节省 40-70%（eksctl 加 `spot: true`）

---

## 核对清单

### 一次性搭建
- [ ] AWS CLI 已配置（已确认）
- [ ] ECR 仓库创建
- [ ] Docker 镜像构建 & 推送
- [ ] EKS 集群创建（~20min）
- [ ] ALB Controller 安装
- [ ] Namespace 创建 (dev / prod)
- [ ] IRSA (S3 权限) 配置
- [ ] Secret 创建 (dev / prod)
- [ ] Helm 部署 (dev)
- [ ] 内部 ALB Ingress 创建 (dev)
- [ ] EC2 → ALB 连通性测试
- [ ] VideoAgent 环境变量配置

### 可选
- [ ] Helm 部署 (prod)
- [ ] 内部 ALB Ingress (prod)
- [ ] Route53 私有域名 (简化 ALB DNS)
- [x] ~~CloudWatch Logs (Fluent Bit)~~ — 已移除，改用 Dashboard
- [x] Kubernetes Dashboard
- [ ] GitHub OIDC + Actions CI/CD
- [ ] Spot 实例优化成本

---

## 未来演进

当 VideoAgent 也迁入 K8s 后：
1. 去掉 ALB，改用 K8s Service DNS 直连：`http://cuti-media-service.dev.svc.cluster.local:8080`
2. VideoAgent 的 `MEDIA_SERVICE_URL` 改为上面的地址
3. 零额外成本、零延迟
