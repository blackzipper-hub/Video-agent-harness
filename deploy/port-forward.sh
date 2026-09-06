#!/usr/bin/env bash
# Open the stack on this machine. Public DNS is unchanged.
#
#   ./deploy/port-forward.sh              # vda-dev Studio → http://127.0.0.1:3000
#   ./deploy/port-forward.sh prod 3001    # vda-prod Studio → http://127.0.0.1:3001
#   ./deploy/port-forward.sh compose      # jump-host colleague-video compose
set -euo pipefail
TARGET="${1:-dev}"

compose_forward() {
  local host="${COMPOSE_SSH_HOST:-songsong_ap_southeast_2}"
  echo "Jump-host compose ($host) → this machine"
  echo "  http://127.0.0.1:3000   Studio"
  echo "  http://127.0.0.1:8001   Runtime"
  echo "  http://127.0.0.1:3080   DSH"
  echo "  http://127.0.0.1:8443   Go"
  echo "  http://127.0.0.1:18080  Media"
  exec ssh -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -L 127.0.0.1:3000:127.0.0.1:3000 \
    -L 127.0.0.1:8001:127.0.0.1:8001 \
    -L 127.0.0.1:3080:127.0.0.1:3080 \
    -L 127.0.0.1:8443:127.0.0.1:8443 \
    -L 127.0.0.1:18080:127.0.0.1:18080 \
    "$host"
}

case "$TARGET" in
  compose|local)
    compose_forward
    ;;
  dev|prod)
    PORT="${2:-3000}"
    NS="vda-${TARGET}"
    echo "Studio → http://127.0.0.1:${PORT}  (namespace $NS)"
    echo "  /            Video Studio"
    echo "  /api/video   Runtime"
    echo "  /chat-v1     Runtime BFF"
    echo "  /api         Go"
    exec kubectl -n "$NS" port-forward --address 127.0.0.1 svc/video-studio "${PORT}:80"
    ;;
  *)
    echo "usage: $0 [dev|prod|compose] [localPort]" >&2
    exit 1
    ;;
esac
