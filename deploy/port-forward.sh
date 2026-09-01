#!/usr/bin/env bash
# Studio on this machine. Public DNS is unchanged.
#
#   ./deploy/port-forward.sh           # vda-dev → http://127.0.0.1:3000
#   ./deploy/port-forward.sh prod 3001 # vda-prod → http://127.0.0.1:3001
set -euo pipefail
ENV="${1:-dev}"
PORT="${2:-3000}"
case "$ENV" in
  dev|prod) ;;
  *) echo "usage: $0 [dev|prod] [localPort]" >&2; exit 1 ;;
esac
NS="vda-${ENV}"
echo "Studio → http://127.0.0.1:${PORT}  (namespace $NS)"
echo "  /            Video Studio"
echo "  /api/video   Runtime"
echo "  /chat-v1     Runtime BFF"
echo "  /api         Go"
exec kubectl -n "$NS" port-forward --address 127.0.0.1 svc/video-studio "${PORT}:80"
