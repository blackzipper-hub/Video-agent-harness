# 五个服务的 Dockerfile / Helm 放哪，以及本地 · dev · prod

[English](README.md) | 中文

三套互不替换：

| 套 | 谁用 | 怎么起 | 能不能改 |
|----|------|--------|----------|
| **同事本地** | README 那一套 | 仓库根 `compose.video.yml` + 本机 `pnpm dsh` | **不能弄坏** |
| **我们本地** | 自己测完整链路（含 Go 登录 + 容器里的 DSH） | `-f compose.video.yml -f deploy/local/compose.yml` | 只动 deploy/local |
| **dev / prod** | EKS | 同一批 Helm chart + `deploy/overlays/{dev,prod}` | 新 namespace，先别动现网 Ingress |

`deploy/harness-test/` 是之前隔离试跑，正式三套不用它。

## Dockerfile 跟服务走（不进 deploy）

同一份给同事 compose、我们 compose、GitHub 编镜像。

| # | 服务 | Dockerfile | 本地（同事） | 本地（我们） | Helm chart |
|---|------|------------|--------------|--------------|------------|
| 1 | Video Runtime | `services/video-runtime/Dockerfile.runtime` | compose.video.yml | 同一份，只改 DSH URL | `services/video-runtime/helm/cuti-videoagent` |
| 2 | Media | `services/media-service/Dockerfile` | 同上 | 同上 | `services/media-service/helm/cuti-media-service` |
| 3 | Video Studio | `apps/video-studio/Dockerfile` | 无登录、无需认证开关 | 同样无登录；对外访问由网关保护 | `deploy/charts/video-studio` |
| 4 | DeepSeek Harness | `apps/dsh/Dockerfile` | **不编镜像**，本机 `pnpm dsh` | compose 里编 + sidecar | `deploy/charts/deepseek-harness` |
| 5 | Go API | `Cuti-backend-go/Dockerfile` | 不启 | compose 编 `../Cuti-backend-go` | `Cuti-backend-go/helm/cuti-api-go` |

数据面：Postgres / Redis 没有自己的业务 Dockerfile（官方镜像）。同事本地只有 Postgres。我们本地和 EKS 有 Postgres + Redis（Go 启动必连 Redis）。

**Helm 环境差异只放 overlay，不改 chart 默认当环境。** Runtime / Media 的 chart 仍在 `services/*/helm`，因为现网旧 release 也是这份；新栈 helm 时 **只** `-f deploy/overlays/...`，不要再叠 `services/*/helm/values-dev.yaml`（那是旧 :8000 Chat 进程）。

```text
cuti-video-agent
  compose.video.yml              同事 README（只读）
  apps/*/Dockerfile              Studio / DSH 怎么造镜像
  services/*/Dockerfile*         Runtime / Media 怎么造镜像
  services/*/helm                Runtime / Media chart 源文件
  deploy/charts                  Studio、DSH、Postgres、Redis、migrate
  deploy/overlays/dev            正式 dev values
  deploy/overlays/prod           正式 prod values
  deploy/local/compose.yml       叠在 compose.video.yml 上；不要单独 -f 这一份
  deploy/harness-test            旧测试，正式不用
  .github/workflows/deploy.yml

Cuti-backend-go
  Dockerfile
  helm/cuti-api-go
  helm/values-dev.yaml
  helm/values-prod.yaml
```

每个仓只留一个 **Build and deploy** workflow：Helm 检查 → 编镜像进 ECR → helm upgrade。推 `dev` 会上 `vda-dev`，推 `main` 会上 `vda-prod`。镜像打 **git SHA**（Helm 用这个滚 Pod），同时再打浮动别名 `dev` / `main` 方便人在 ECR 里找，**不要**用别名做 `--set image.tag`。也可以在 Actions 里手动 Run，选 dev 或 prod（Video Agent 还可勾服务，默认全勾）。

两个仓要分别点一次 **Build and deploy**（Video Agent 的 Studio / Runtime / Media / DSH，和 Go 仓的 API）。prod 建议 GitHub Environment `vda-prod` 开 required reviewers。GitHub 托管 runner 需要仓库 Secret `AWS_ROLE_ARN`（OIDC）；EC2 上的 instance role 只给那台机器用，GitHub 的虚机用不了。没配好之前镜像和 helm 仍走跳板机。GitHub 调用的是 `deploy/vda-upgrade.sh`，**不会**跑 `vda-apply.sh`（那是第一次装 namespace 用的，不要删）。DeepSeek 自带的 CI / Release / Issue / 文档那些 workflow 已去掉。

本地点一次（和 Actions 同一脚本）：

```bash
./deploy/vda-upgrade.sh dev --tag "$GITHUB_SHA"
./deploy/vda-upgrade.sh prod --tag "$GITHUB_SHA" --studio
```

Go：`Cuti-backend-go/helm/vda-upgrade.sh prod --tag main`

## 现在可以打开

| 环境 | 打开 | 集群 |
|------|------|------|
| **Dev** | http://k8s-vdadev-cutistud-9e1f0bbc43-61600723.ap-southeast-2.elb.amazonaws.com | `vda-dev` |
| **Prod** | http://k8s-vdaprod-cutistud-084c8e4334-770037228.ap-southeast-2.elb.amazonaws.com | `vda-prod` |

本机隧道（不改公网 DNS）：

```bash
./deploy/port-forward.sh           # vda-dev Studio → http://127.0.0.1:3000
./deploy/port-forward.sh prod 3001 # vda-prod Studio → :3001
./deploy/port-forward.sh compose   # 跳板机 colleague-video compose（3000/8001/3080/8443/18080）
```

`compose` 走 `songsong_ap_southeast_2` 的 SSH `-L`；换机器时设 `COMPOSE_SSH_HOST`。

ALB 已挂悉尼 ACM（443）。`dev.newai.land` 指 `vda-dev` ALB，`www.cuti.land` 指 `vda-prod` ALB。HTTPS 用这些域名；ALB 长域名走 https 会证书名对不上。


## 同事本地（不要改）

根目录 README：`docker compose --env-file .env -f compose.video.yml up`，另开终端 `pnpm dsh web …`。单用户、无 Go、无 Redis。

## 我们本地（完整五件套）

`Cuti-backend-go` 和 `cuti-video-agent` 放在同一父目录。然后：

```sh
cd cuti-video-agent
cp .env.example .env          # 填 OPENAI / WAVESPEED
mkdir -p deploy/local/go-env
cp deploy/local/go.env.dev.example deploy/local/go-env/.env.dev
docker compose --env-file .env -f compose.video.yml -f deploy/local/compose.yml up --build -d
```

| 端口 | 服务 |
|------|------|
| 3000 | Studio（Go 登录，不是单用户） |
| 8001 | Runtime |
| 3080 | DSH（容器 sidecar，不必再本机 pnpm dsh） |
| 8443 | Go |
| 18080 | Media |

Studio 的 `nginx.conf` 走 hostname `video-runtime` / `cuti-api-go`，与 compose 服务名一致。DSH 仍 bind 127.0.0.1，sidecar 听 8080。

## 部署 dev / prod

新 namespace：`cuti-dev`、`cuti-prod`。Ingress 默认关。现网旧 release 先别 upgrade。

Secret 先自己 apply（example 在对应 overlay 目录）：`cuti-postgres`、`cuti-go-dsn`、`cuti-videoagent-secrets`、`cuti-media-service-secrets`、`cuti-api-go-env`。

DSH 的 `VIDEO_RUNTIME_USER_ID` 必须等于 Runtime 的 `VIDEO_RUNTIME_LOCAL_USER_ID`（dev：`cuti-dev-user`，prod：`cuti-prod-user`）。

```bash
ENV=dev          # 或 prod
NS=cuti-$ENV
kubectl create namespace "$NS"

# 填好密码后再 apply
kubectl apply -n "$NS" -f deploy/overlays/$ENV/postgres-secret.example.yaml
kubectl apply -n "$NS" -f deploy/overlays/$ENV/go-dsn.example.yaml

helm upgrade --install cuti-postgres deploy/charts/postgres -n "$NS" -f deploy/overlays/$ENV/postgres.yaml
helm upgrade --install cuti-redis deploy/charts/redis -n "$NS" -f deploy/overlays/$ENV/redis.yaml
helm upgrade --install cuti-migrate deploy/charts/runtime-migrate -n "$NS" -f deploy/overlays/$ENV/runtime-migrate.yaml

helm upgrade --install cuti-runtime services/video-runtime/helm/cuti-videoagent -n "$NS" \
  -f deploy/overlays/$ENV/runtime.yaml
helm upgrade --install cuti-media services/media-service/helm/cuti-media-service -n "$NS" \
  -f deploy/overlays/$ENV/media.yaml
helm upgrade --install cuti-studio deploy/charts/video-studio -n "$NS" -f deploy/overlays/$ENV/studio.yaml
helm upgrade --install cuti-dsh deploy/charts/deepseek-harness -n "$NS" -f deploy/overlays/$ENV/dsh.yaml
```

Go 仓：

```bash
helm upgrade --install cuti-go ./helm/cuti-api-go -n cuti-dev -f ./helm/values-dev.yaml
helm upgrade --install cuti-go ./helm/cuti-api-go -n cuti-prod -f ./helm/values-prod.yaml
```

`--set image.tag=<git SHA>` 钉这次 CI 推进 ECR 的不可变标签。浮动 `dev` / `main` 只给人看。

## 第三方依赖

| | 同事本地 | 我们本地 | EKS dev/prod |
|--|----------|----------|--------------|
| Postgres | compose 卷 | 同左 + 多一个 `storybook_dev` | **PVC**（chart）或继续 RDS |
| Redis | 无 | compose 卷 | **PVC** 或 ElastiCache。**Go 必有** |
| S3 | 不用（local 盘） | 不用 | 成片用 S3 |
| Runtime Redis | 不用 | 不用 | 不用（`ENABLE_TASK_WORKER=false`） |

现网旧 Go 仍在 EC2，进程就是 `-env cuti-api-go.dev`（:8443）和 `-env cuti-api-go.prod`（:2096）。新栈 Helm 也只用这两个名字；库不在本机时用 Secret 里的 `CUTI_DATABASE_DSN` 覆盖 yaml 里的 `127.0.0.1`。

PVC 重启 Pod **不丢**；删 PVC 才丢。Media `/workspace` 是 emptyDir，本来就该丢。成片走 S3。
