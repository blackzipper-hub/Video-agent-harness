#!/usr/bin/env bash
# First-time install into a NEW namespace. GitHub Deploy does not call this
# (GitHub uses deploy/vda-upgrade.sh). Keep this file — it copies secrets, PVCs,
# and schema. Do not use it to roll already-running vda-dev / vda-prod.
# Does not touch live helm releases in namespaces `dev` / `prod`.
#
#   ./deploy/vda-apply.sh dev
#   ./deploy/vda-apply.sh prod
set -euo pipefail

ENV="${1:?usage: $0 dev|prod}"
case "$ENV" in
  dev|prod) ;;
  *) echo "env must be dev or prod" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GO_ROOT="$(cd "$ROOT/../Cuti-backend-go" && pwd)"
NS="vda-${ENV}"
ECR="699475938168.dkr.ecr.ap-southeast-2.amazonaws.com"

# Working tags from the jump-host builds (GHA will later tag main).
TAG_RUNTIME=harness-runtime
TAG_MEDIA=harness-test-requests
TAG_STUDIO=harness-test
TAG_DSH=harness-test-bundles
TAG_GO=vda-prod-http-cookie

SRC_NS=$ENV   # copy API keys from live `dev` or `prod` secrets
GO_ENV_KEY=.env.dev
GO_ARGS_ENV=cuti-api-go.dev
PG_DB=storybook_dev
USER_ID="cuti-dev-user"
if [[ "$ENV" == prod ]]; then
  GO_ENV_KEY=.env.prod
  GO_ARGS_ENV=cuti-api-go.prod
  PG_DB=storybook
  USER_ID="cuti-prod-user"
fi

kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"

echo ">> copy live $SRC_NS secrets (API keys) into $NS"
kubectl get secret cuti-videoagent-secrets -n "$SRC_NS" -o json \
  | python3 -c "
import json,sys
s=json.load(sys.stdin)
print(json.dumps({
  'apiVersion':'v1','kind':'Secret','type': s.get('type','Opaque'),
  'metadata': {'name':'cuti-videoagent-secrets','namespace':'$NS'},
  'data': s['data'],
}))
" | kubectl apply -f -

kubectl get secret cuti-media-service-secrets -n "$SRC_NS" -o json \
  | python3 -c "
import json,sys
s=json.load(sys.stdin)
print(json.dumps({
  'apiVersion':'v1','kind':'Secret','type': s.get('type','Opaque'),
  'metadata': {'name':'cuti-media-service-secrets','namespace':'$NS'},
  'data': s['data'],
}))
" | kubectl apply -f -

# AWS keys live on the videoagent secret; media pods need them too if not using IRSA.
python3 - << PY
import json, subprocess, base64, os
ns="$NS"
raw=subprocess.check_output(["kubectl","-n",ns,"get","secret","cuti-videoagent-secrets","-o","json"])
src=json.loads(raw)["data"]
rawm=subprocess.check_output(["kubectl","-n",ns,"get","secret","cuti-media-service-secrets","-o","json"])
media=json.loads(rawm)
data=media.get("data") or {}
for k in ("AWS_ACCESS_KEY_ID","AWS_SECRET_ACCESS_KEY"):
    if k in src:
        data[k]=src[k]
media["data"]=data
media["metadata"]={"name":"cuti-media-service-secrets","namespace":ns}
subprocess.run(["kubectl","apply","-f","-"], input=json.dumps(media).encode(), check=True)
PY

echo ">> Go env file from EC2 into Secret $GO_ENV_KEY"
TMP=$(mktemp)
ssh -o BatchMode=yes songsong_ap_southeast_2 "sudo cat /home/ubuntu/.cuti-api-go/${GO_ENV_KEY}" > "$TMP" || \
  scp -q "songsong_ap_southeast_2:/home/ubuntu/.cuti-api-go/${GO_ENV_KEY}" "$TMP"
kubectl -n "$NS" create secret generic cuti-api-go-env \
  --from-file="${GO_ENV_KEY}=${TMP}" \
  --dry-run=client -o yaml | kubectl apply -f -
rm -f "$TMP"

if ! kubectl -n "$NS" get secret cuti-postgres >/dev/null 2>&1; then
  PW=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 24)
  kubectl -n "$NS" create secret generic cuti-postgres \
    --from-literal=POSTGRES_DB="$PG_DB" \
    --from-literal=POSTGRES_USER=cuti \
    --from-literal=POSTGRES_PASSWORD="$PW"
fi
PW=$(kubectl -n "$NS" get secret cuti-postgres -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
RT_DSN="postgresql://cuti:${PW}@postgres:5432/${PG_DB}"
GO_DSN="postgres://cuti:${PW}@postgres:5432/${PG_DB}?sslmode=disable"
kubectl -n "$NS" create secret generic cuti-go-dsn \
  --from-literal=dsn="$GO_DSN" \
  --dry-run=client -o yaml | kubectl apply -f -

# Overlay runtime DSN via extra --set after copying secret: patch secret key
python3 - << PY
import json, subprocess, base64
ns="$NS"
url="$RT_DSN"
raw=json.loads(subprocess.check_output(["kubectl","-n",ns,"get","secret","cuti-videoagent-secrets","-o","json"]))
raw["data"]["VIDEO_RUNTIME_DATABASE_URL"]=base64.b64encode(url.encode()).decode()
raw["data"]["VIDEO_RUNTIME_LOCAL_USER_ID"]=base64.b64encode(b"$USER_ID").decode()
raw["data"]["VIDEO_RUNTIME_USER_ID"]=base64.b64encode(b"$USER_ID").decode()
raw["metadata"]={"name":"cuti-videoagent-secrets","namespace":ns}
subprocess.run(["kubectl","apply","-f","-"], input=json.dumps(raw).encode(), check=True)
print("runtime dsn patched")
PY

echo ">> helm $NS"
helm upgrade --install cuti-postgres "$ROOT/deploy/charts/postgres" -n "$NS" \
  -f "$ROOT/deploy/overlays/$ENV/postgres.yaml"
helm upgrade --install cuti-redis "$ROOT/deploy/charts/redis" -n "$NS" \
  -f "$ROOT/deploy/overlays/$ENV/redis.yaml"

echo "wait postgres"
kubectl -n "$NS" rollout status deploy/cuti-postgres --timeout=180s
kubectl -n "$NS" rollout status deploy/cuti-redis --timeout=120s

echo ">> Go schema into in-cluster postgres"
PGPOD=$(kubectl -n "$NS" get pod -l app=postgres -o jsonpath='{.items[0].metadata.name}')
HAS_USERS=$(kubectl -n "$NS" exec "$PGPOD" -- sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT to_regclass('\''public.users'\'')"')
if [[ -z "${HAS_USERS// }" ]]; then
  kubectl -n "$NS" cp "$GO_ROOT/migrations/init_schema.sql" "$PGPOD:/tmp/init_schema.sql"
  kubectl -n "$NS" exec "$PGPOD" -- sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f /tmp/init_schema.sql'
else
  echo "public.users already exists, skip init_schema"
fi

helm upgrade --install cuti-migrate "$ROOT/deploy/charts/runtime-migrate" -n "$NS" \
  -f "$ROOT/deploy/overlays/$ENV/runtime-migrate.yaml"

helm upgrade --install cuti-runtime "$ROOT/services/video-runtime/helm/cuti-videoagent" -n "$NS" \
  -f "$ROOT/deploy/overlays/$ENV/runtime.yaml" \
  --set image.tag="$TAG_RUNTIME" \
  --set env.VIDEO_RUNTIME_LOCAL_USER_ID="$USER_ID" \
  --set env.STORAGE_BACKEND=s3

helm upgrade --install cuti-media "$ROOT/services/media-service/helm/cuti-media-service" -n "$NS" \
  -f "$ROOT/deploy/overlays/$ENV/media.yaml" \
  --set image.tag="$TAG_MEDIA"

helm upgrade --install cuti-studio "$ROOT/deploy/charts/video-studio" -n "$NS" \
  -f "$ROOT/deploy/overlays/$ENV/studio.yaml" \
  --set image.tag="$TAG_STUDIO" \
  --set ingress.enabled=true \
  --set ingress.scheme=internet-facing

helm upgrade --install cuti-dsh "$ROOT/deploy/charts/deepseek-harness" -n "$NS" \
  -f "$ROOT/deploy/overlays/$ENV/dsh.yaml" \
  --set image.tag="$TAG_DSH" \
  --set env.VIDEO_RUNTIME_USER_ID="$USER_ID"

helm upgrade --install cuti-go "$GO_ROOT/helm/cuti-api-go" -n "$NS" \
  -f "$GO_ROOT/helm/values-${ENV}.yaml" \
  --set image.tag="$TAG_GO"

echo ">> wait rollouts"
for d in cuti-videoagent cuti-media-service cuti-studio cuti-dsh cuti-go; do
  kubectl -n "$NS" rollout status "deploy/$d" --timeout=300s
done

echo ">> ingress"
kubectl -n "$NS" get ingress -o wide
