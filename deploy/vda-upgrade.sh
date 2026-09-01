#!/usr/bin/env bash
# Roll existing vda-dev / vda-prod app releases. Does not install Postgres/Redis,
# does not touch live namespaces `dev` / `prod`, does not recreate Ingress/ALB.
#
#   ./deploy/vda-upgrade.sh dev --tag dev
#   ./deploy/vda-upgrade.sh prod --tag main --studio
#   ./deploy/vda-upgrade.sh prod --tag main --runtime --media
set -euo pipefail

ENV="${1:?usage: $0 dev|prod --tag TAG [--all|--studio|--runtime|--media|--dsh]}"
shift
case "$ENV" in
  dev|prod) ;;
  *) echo "env must be dev or prod" >&2; exit 1 ;;
esac

NS="vda-${ENV}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG=""
DO_STUDIO=0
DO_RUNTIME=0
DO_MEDIA=0
DO_DSH=0
ANY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) TAG="${2:?}"; shift 2 ;;
    --all) DO_STUDIO=1; DO_RUNTIME=1; DO_MEDIA=1; DO_DSH=1; ANY=1; shift ;;
    --studio) DO_STUDIO=1; ANY=1; shift ;;
    --runtime) DO_RUNTIME=1; ANY=1; shift ;;
    --media) DO_MEDIA=1; ANY=1; shift ;;
    --dsh) DO_DSH=1; ANY=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$TAG" ]]; then
  echo "--tag is required (ECR tag: dev or main)" >&2
  exit 1
fi
if [[ "$ANY" -eq 0 ]]; then
  DO_STUDIO=1
  DO_RUNTIME=1
  DO_MEDIA=1
  DO_DSH=1
fi

if [[ "$NS" != "vda-dev" && "$NS" != "vda-prod" ]]; then
  echo "refusing namespace $NS" >&2
  exit 1
fi

echo "namespace=$NS tag=$TAG studio=$DO_STUDIO runtime=$DO_RUNTIME media=$DO_MEDIA dsh=$DO_DSH"

if [[ "$DO_RUNTIME" -eq 1 ]]; then
  helm upgrade cuti-runtime "$ROOT/services/video-runtime/helm/cuti-videoagent" -n "$NS" \
    -f "$ROOT/deploy/overlays/$ENV/runtime.yaml" \
    --force-conflicts \
    --set image.tag="$TAG"
fi
if [[ "$DO_MEDIA" -eq 1 ]]; then
  helm upgrade cuti-media "$ROOT/services/media-service/helm/cuti-media-service" -n "$NS" \
    -f "$ROOT/deploy/overlays/$ENV/media.yaml" \
    --force-conflicts \
    --set image.tag="$TAG"
fi
if [[ "$DO_STUDIO" -eq 1 ]]; then
  helm upgrade cuti-studio "$ROOT/deploy/charts/video-studio" -n "$NS" \
    -f "$ROOT/deploy/overlays/$ENV/studio.yaml" \
    --force-conflicts \
    --set image.tag="$TAG"
fi
if [[ "$DO_DSH" -eq 1 ]]; then
  helm upgrade cuti-dsh "$ROOT/deploy/charts/deepseek-harness" -n "$NS" \
    -f "$ROOT/deploy/overlays/$ENV/dsh.yaml" \
    --force-conflicts \
    --set image.tag="$TAG"
fi

[[ "$DO_RUNTIME" -eq 1 ]] && kubectl -n "$NS" rollout status deploy/cuti-videoagent --timeout=300s
# Media rolling update may wait for cluster-autoscaler to add a node (ASG max 12).
[[ "$DO_MEDIA" -eq 1 ]] && kubectl -n "$NS" rollout status deploy/cuti-media-service --timeout=600s
[[ "$DO_STUDIO" -eq 1 ]] && kubectl -n "$NS" rollout status deploy/cuti-studio --timeout=180s
[[ "$DO_DSH" -eq 1 ]] && kubectl -n "$NS" rollout status deploy/cuti-dsh --timeout=300s

echo "ok $NS"
