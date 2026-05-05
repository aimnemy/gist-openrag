#!/usr/bin/env bash
# Bootstrap the aimlab_openrag ↔ cad_beyondkm integration.
#
# Run once, before `docker compose up`, while the cad_beyondkm stack is already
# running. Idempotent — safe to re-run.
#
# What it does:
#   1. Creates the external Docker network `beyondkm_shared` (if missing).
#   2. Attaches running cad_beyondkm services to that network.
#   3. Creates the `langflow` database in the shared Postgres (if missing).

set -euo pipefail

NETWORK=beyondkm_shared
# Container names as created by cad_beyondkm's compose (project-prefixed, -1 suffix).
BEYONDKM_CONTAINERS=(
  cad_beyondkm-weaviate-1
  cad_beyondkm-memgraph-1
  cad_beyondkm-redis-1
  cad_beyondkm-azurite-1
  cad_beyondkm-api-1
  cad_beyondkm-postgres-1   # optional — only exists if local PG is enabled
)
# Network aliases so OpenRAG can resolve them as bare service names.
# (Associative arrays need bash 4+, macOS ships bash 3.2 — using a case.)
alias_for() {
  case "$1" in
    cad_beyondkm-weaviate-1) echo weaviate ;;
    cad_beyondkm-memgraph-1) echo memgraph ;;
    cad_beyondkm-redis-1)    echo redis ;;
    cad_beyondkm-azurite-1)  echo azurite ;;
    cad_beyondkm-api-1)      echo beyondkm-api ;;
    cad_beyondkm-postgres-1) echo postgres ;;
    *) echo "$1" ;;
  esac
}

log() { printf '\033[1;34m[bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[bootstrap]\033[0m %s\n' "$*" >&2; }

# 1. Network
if docker network inspect "$NETWORK" >/dev/null 2>&1; then
  log "network '$NETWORK' already exists"
else
  log "creating network '$NETWORK'"
  docker network create "$NETWORK"
fi

# 2. Attach BeyondKM containers with service-name aliases so OpenRAG can
#    reach them as `weaviate`, `memgraph`, etc. on beyondkm_shared.
for name in "${BEYONDKM_CONTAINERS[@]}"; do
  cid=$(docker ps --filter "name=^${name}$" --format '{{.ID}}' | head -n1)
  if [[ -z "$cid" ]]; then
    warn "container '$name' not running — skipping"
    continue
  fi
  alias_name="$(alias_for "$name")"
  members=$(docker network inspect "$NETWORK" \
              --format '{{range .Containers}}{{.Name}} {{end}}')
  if grep -qw "$name" <<<"$members"; then
    log "'$name' already on '$NETWORK'"
  else
    log "attaching '$name' as alias '$alias_name'"
    docker network connect --alias "$alias_name" "$NETWORK" "$cid"
  fi
done

log "done — next: cd \$(dirname \$0)/.. && docker compose up -d"
