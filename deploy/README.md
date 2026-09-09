# Dockerfiles and Helm for five services: local, dev, and prod

English | [中文](README.zh.md)

Three separate setups; none replaces another:

| Setup | Audience | Startup | Change policy |
|----|------|--------|----------|
| **Colleague local** | Root README users | Root `compose.video.yml` + local `pnpm dsh` | **Must remain working** |
| **Team local** | Full-chain testing (Go login + containerized DSH) | `-f compose.video.yml -f deploy/local/compose.yml` | Change deploy/local only |
| **dev / prod** | EKS | Shared Helm charts + `deploy/overlays/{dev,prod}` | New namespaces; leave existing Ingress alone |

`deploy/harness-test/` was an isolated trial and is not used by these three setups.

## Keep Dockerfiles with services, not in deploy

The same Dockerfiles serve colleague compose, team compose, and GitHub image builds.

| # | Service | Dockerfile | Colleague local | Team local | Helm chart |
|---|------|------------|--------------|--------------|------------|
| 1 | Video Runtime | `services/video-runtime/Dockerfile.runtime` | compose.video.yml | Same file, different DSH URL | `services/video-runtime/helm/cuti-videoagent` |
| 2 | Media | `services/media-service/Dockerfile` | Same as above | Same as above | `services/media-service/helm/cuti-media-service` |
| 3 | Video Studio | `apps/video-studio/Dockerfile` | No login or authentication switch | No login; gateway protects external access | `deploy/charts/video-studio` |
| 4 | DeepSeek Harness | `apps/dsh/Dockerfile` | **No image build**, local `pnpm dsh` | Compose build + sidecar | `deploy/charts/deepseek-harness` |
| 5 | Go API | `Cuti-backend-go/Dockerfile` | Not started | Compose builds `../Cuti-backend-go` | `Cuti-backend-go/helm/cuti-api-go` |

Data services: Postgres and Redis use official images, without business-specific Dockerfiles. Colleague local uses only Postgres. Team local and EKS use Postgres + Redis; Go requires a Redis connection on startup.

**Put Helm environment differences only in overlays, not chart defaults.** Runtime and Media charts remain in `services/*/helm` because existing releases use them too. For the new stack, use **only** `-f deploy/overlays/...`; do not layer `services/*/helm/values-dev.yaml`, which belongs to the old :8000 Chat process.

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

Each repository keeps one **Build and deploy** workflow: Helm checks → build images into ECR → helm upgrade. Pushing `dev` deploys to `vda-dev`; pushing `main` deploys to `vda-prod`. Images use the **git SHA** tag for Helm Pod rollouts, with floating `dev` / `main` aliases for browsing ECR. **Do not** use an alias for `--set image.tag`. Actions also supports manual runs for dev or prod; Video Agent allows selecting services, all selected by default.

Run **Build and deploy** separately in both repositories: Studio / Runtime / Media / DSH in Video Agent, and API in the Go repository. For prod, enable required reviewers on GitHub Environment `vda-prod`. GitHub-hosted runners need repository secret `AWS_ROLE_ARN` for OIDC; an EC2 instance role cannot authenticate GitHub's machines. Until configured, build images and run Helm from the bastion. GitHub runs `deploy/vda-upgrade.sh`, **not** `vda-apply.sh`, which initializes namespaces and must remain. The original DeepSeek CI / Release / Issue / documentation workflows have been removed.

Run locally using the same script as Actions:

```bash
./deploy/vda-upgrade.sh dev --tag "$GITHUB_SHA"
./deploy/vda-upgrade.sh prod --tag "$GITHUB_SHA" --studio
```

Go: `Cuti-backend-go/helm/vda-upgrade.sh prod --tag main`

## Available endpoints

| Environment | Open | Cluster |
|------|------|------|
| **Dev** | http://k8s-vdadev-cutistud-9e1f0bbc43-61600723.ap-southeast-2.elb.amazonaws.com | `vda-dev` |
| **Prod** | http://k8s-vdaprod-cutistud-084c8e4334-770037228.ap-southeast-2.elb.amazonaws.com | `vda-prod` |

Local tunnels without changing public DNS:

```bash
./deploy/port-forward.sh           # vda-dev Studio → http://127.0.0.1:3000
./deploy/port-forward.sh prod 3001 # vda-prod Studio → :3001
./deploy/port-forward.sh compose   # 跳板机 colleague-video compose（3000/8001/3080/8443/18080）
```

`compose` uses SSH `-L` through `songsong_ap_southeast_2`; set `COMPOSE_SSH_HOST` for a different machine.

The ALBs have Sydney ACM certificates on port 443. `dev.newai.land` points to the `vda-dev` ALB, and `www.cuti.land` to the `vda-prod` ALB. Use these domains for HTTPS; the long ALB hostname does not match the certificate.

## Colleague local (preserve this setup)

Follow the root README: `docker compose --env-file .env -f compose.video.yml up`, then `pnpm dsh web …` in another terminal. Single-user, no Go, no Redis.

## Team local (all five services)

Place `Cuti-backend-go` and `cuti-video-agent` under the same parent directory. Then:

```sh
cd cuti-video-agent
cp .env.example .env          # 填 OPENAI / WAVESPEED
mkdir -p deploy/local/go-env
cp deploy/local/go.env.dev.example deploy/local/go-env/.env.dev
docker compose --env-file .env -f compose.video.yml -f deploy/local/compose.yml up --build -d
```

| Port | Service |
|------|------|
| 3000 | Studio (Go login, not single-user) |
| 8001 | Runtime |
| 3080 | DSH (container sidecar; no local pnpm dsh needed) |
| 8443 | Go |
| 18080 | Media |

Studio's `nginx.conf` uses hostnames `video-runtime` / `cuti-api-go`, matching compose service names. DSH still binds to 127.0.0.1; the sidecar listens on 8080.

## Deploy dev / prod

New namespaces: `cuti-dev`, `cuti-prod`. Ingress is disabled by default. Do not upgrade the existing production releases yet.

Apply secrets yourself first; examples are in the corresponding overlay directories: `cuti-postgres`, `cuti-go-dsn`, `cuti-videoagent-secrets`, `cuti-media-service-secrets`, `cuti-api-go-env`.

DSH's `VIDEO_RUNTIME_USER_ID` must equal Runtime's `VIDEO_RUNTIME_LOCAL_USER_ID`: `cuti-dev-user` for dev, `cuti-prod-user` for prod.

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

Go repository:

```bash
helm upgrade --install cuti-go ./helm/cuti-api-go -n cuti-dev -f ./helm/values-dev.yaml
helm upgrade --install cuti-go ./helm/cuti-api-go -n cuti-prod -f ./helm/values-prod.yaml
```

`--set image.tag=<git SHA>` pins the immutable tag pushed to ECR by this CI run. Floating `dev` / `main` aliases are for humans only.

## Third-party dependencies

| | Colleague local | Team local | EKS dev/prod |
|--|----------|----------|--------------|
| Postgres | Compose volume | Same, plus `storybook_dev` | **PVC** (chart) or existing RDS |
| Redis | None | Compose volume | **PVC** or ElastiCache; **required by Go** |
| S3 | Not used (local disk) | Not used | Final videos use S3 |
| Runtime Redis | Not used | Not used | Not used |

The existing Go services still run on EC2 with `-env cuti-api-go.dev` (:8443) and `-env cuti-api-go.prod` (:2096). The new Helm stack uses the same two names. For a remote database, override the YAML's `127.0.0.1` using secret `CUTI_DATABASE_DSN`.

Restarting Pods **preserves** PVC data; deleting a PVC removes it. Media `/workspace` is emptyDir and intentionally disposable. Final videos go to S3.
