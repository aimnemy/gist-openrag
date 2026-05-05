#!/usr/bin/env bash
# End-to-end: bootstrap network → fill secrets → start OpenRAG → sync data.
# Idempotent. Safe to re-run.
#
# Project name is pinned to `aimlab_openrag` so containers, networks, and
# volumes are grouped under that label regardless of CWD.
#
# Usage:
#   ./scripts/setup_and_sync.sh                # full run, syncs 500 docs
#   ./scripts/setup_and_sync.sh --limit 50     # fewer docs
#   ./scripts/setup_and_sync.sh --skip-sync    # setup only
#   ./scripts/setup_and_sync.sh --skip-up      # no docker compose up
#   LIMIT=all ./scripts/setup_and_sync.sh      # sync everything

set -euo pipefail
cd "$(dirname "$0")/.."

export COMPOSE_PROJECT_NAME=aimlab_openrag

SYNC=1
UP=1
DOCLING=1
SYNC_ARGS=(--limit 500)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-sync)    SYNC=0; shift ;;
    --skip-up)      UP=0; shift ;;
    --skip-docling) DOCLING=0; shift ;;
    --limit)        SYNC_ARGS=(--limit "$2"); shift 2 ;;
    --all)          SYNC_ARGS=(); shift ;;
    *)              SYNC_ARGS+=("$1"); shift ;;
  esac
done

bold() { printf '\033[1;36m== %s ==\033[0m\n' "$*"; }

bold "1/7  Bootstrap shared network + attach BeyondKM aliases"
./scripts/bootstrap_integration.sh

bold "2/7  Fill REPLACE_ME_* secrets in .env"
./scripts/generate_secrets.sh

if [[ $UP -eq 1 ]]; then
  bold "3/7  docker compose up -d  (project=$COMPOSE_PROJECT_NAME)"
  docker compose up -d
else
  bold "3/7  skipped (--skip-up)"
fi

bold "4/7  Wait for OpenSearch to be healthy"
OS_PW="$(grep ^OPENSEARCH_PASSWORD= .env | cut -d= -f2-)"
ok=0
for i in $(seq 1 60); do
  if curl -sk -u "admin:${OS_PW}" \
       "https://localhost:9200/_cluster/health" 2>/dev/null \
       | grep -qE '"status":"(green|yellow)"'; then
    ok=1
    printf '\r[ready] OpenSearch healthy after %ss\n' "$((i*5))"
    break
  fi
  printf '\r[wait] OpenSearch not ready yet (%ss elapsed)' "$((i*5))"
  sleep 5
done
if [[ $ok -eq 0 ]]; then
  echo ""
  echo "[error] OpenSearch did not become healthy in 5 minutes."
  echo "        Check: docker compose -p aimlab_openrag logs opensearch --tail 50"
  exit 1
fi

if [[ $SYNC -eq 1 ]]; then
  bold "5/7  Sync BeyondKM Weaviate → OpenRAG OpenSearch  (${SYNC_ARGS[*]:-all})"
  OPENSEARCH_PASSWORD="$OS_PW" \
    uv run --with requests --no-project \
      python scripts/sync_beyondkm_to_openrag.py "${SYNC_ARGS[@]}"
else
  bold "5/7  skipped (--skip-sync)"
fi

bold "6/7  Ensure OpenSearch Dashboards index pattern 'beyondkm_documents*'"
idx_resp="$(curl -s -o /dev/null -w '%{http_code}' \
  -X POST 'http://localhost:5601/api/saved_objects/index-pattern/beyondkm_documents?overwrite=true' \
  -H 'osd-xsrf: true' -H 'Content-Type: application/json' \
  -u "admin:${OS_PW}" \
  -d '{"attributes":{"title":"beyondkm_documents*","timeFieldName":null}}' || true)"
if [[ "$idx_resp" == "200" ]]; then
  echo "[ok] index pattern ready (POST 200)"
else
  echo "[warn] index-pattern API returned $idx_resp — open Dashboards → Stack Management → Index Patterns to create manually"
fi

if [[ $DOCLING -eq 1 ]]; then
  bold "7/7  Start docling-serve on host (native, for knowledge ingest)"
  ./scripts/docling_run.sh start
else
  bold "7/7  skipped (--skip-docling)"
fi

bold "done"
echo "OpenRAG frontend : http://localhost:3000"
echo "Langflow         : http://localhost:7860"
echo "OpenSearch       : https://localhost:9200 (admin / \$OPENSEARCH_PASSWORD)"
echo "Dashboards       : http://localhost:5601  → 'Discover' → pattern 'beyondkm_documents*'"
echo "docling-serve    : http://localhost:5001  (host — reachable from backend as host.docker.internal)"
