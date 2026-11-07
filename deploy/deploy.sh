#!/usr/bin/env bash
# Runs on your computer. Copies the code to the VPS and rebuilds the container.
#   deploy.sh root@<server>                    update code, rebuild, restart
#   deploy.sh root@<server> --first <domain>   first time: folders, .env with a new ETA_KEY and Traefik settings
set -euo pipefail
HOST="${1:?usage: deploy.sh root@<server> [--first <domain>]}"; shift
FIRST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --first) FIRST="${2:?--first needs a domain}"; shift 2 ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
done
ROOT="$(cd "$(dirname "$0")/.." && pwd)"   # the waze-eta folder

ssh "$HOST" 'mkdir -p /docker/waze-eta/src /docker/waze-eta/data && chown 1000:1000 /docker/waze-eta/data'
rsync -az --delete --exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude .git --exclude .env \
  "$ROOT/" "$HOST:/docker/waze-eta/src/"
rsync -az "$ROOT/deploy/compose.yaml" "$HOST:/docker/waze-eta/compose.yaml"
if [ -n "$FIRST" ]; then
  ssh "$HOST" "bash -s '$FIRST'" <<'EOF'
set -euo pipefail
cd /docker/waze-eta
if [ ! -f .env ]; then
  # Traefik settings: the same ones the coach project detected (Hostinger template defaults otherwise).
  EP="$(grep -s '^TRAEFIK_ENTRYPOINT=' /docker/coach/.env | cut -d= -f2)"; RES="$(grep -s '^TRAEFIK_CERTRESOLVER=' /docker/coach/.env | cut -d= -f2)"
  { echo "ETA_KEY=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"; echo "WAZE_REGION=IL"
    echo "DOMAIN=$1"; echo "TRAEFIK_ENTRYPOINT=${EP:-websecure}"; echo "TRAEFIK_CERTRESOLVER=${RES:-letsencrypt}"; } > .env
  chmod 600 .env
  echo "wrote /docker/waze-eta/.env with a new ETA_KEY"
fi
EOF
fi
ssh "$HOST" 'cd /docker/waze-eta && docker compose build -q && docker compose up -d && sleep 3 && docker compose ps && docker compose logs --tail 10 eta'
