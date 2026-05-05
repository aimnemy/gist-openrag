#!/usr/bin/env bash
# Re-push local flow JSONs into Langflow's DB via its REST API.
# Needed after editing flows/*.json because Langflow caches flows in its own
# SQLite on first boot; it does not re-read the mounted JSONs on restart.
#
# Maps each flow file → the matching UUID from .env. Unknown files are skipped.

set -euo pipefail
cd "$(dirname "$0")/.."

LF_URL="${LF_URL:-http://localhost:7860}"
LF_USER="${LANGFLOW_SUPERUSER:-admin}"
LF_PW="$(grep ^LANGFLOW_SUPERUSER_PASSWORD= .env | cut -d= -f2-)"

flow_id_for() {
  case "$1" in
    openrag_url_mcp.json)  grep ^LANGFLOW_URL_INGEST_FLOW_ID= .env | cut -d= -f2- ;;
    ingestion_flow.json)   grep ^LANGFLOW_INGEST_FLOW_ID= .env | cut -d= -f2- ;;
    openrag_agent.json)    grep ^LANGFLOW_CHAT_FLOW_ID= .env | cut -d= -f2- ;;
    openrag_nudges.json)   grep ^NUDGES_FLOW_ID= .env | cut -d= -f2- ;;
    *) echo "" ;;
  esac
}

echo "[auth] logging in as $LF_USER"
TOKEN="$(curl -sf -X POST "$LF_URL/api/v1/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${LF_USER}&password=${LF_PW}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
[[ -n "$TOKEN" ]] || { echo "[error] login failed" >&2; exit 1; }

for fp in flows/*.json; do
  [[ -f "$fp" ]] || continue
  fname="${fp##*/}"
  fid="$(flow_id_for "$fname")"
  if [[ -z "$fid" ]]; then
    echo "[skip] $fname (no mapping in .env)"
    continue
  fi
  echo "[patch] $fname → $fid"
  # Langflow's PATCH accepts partial flow body; send `data` + `name` + `description`.
  payload=$(python3 - "$fp" <<'PY'
import json, sys, pathlib
doc = json.loads(pathlib.Path(sys.argv[1]).read_text())
out = {
    "name": doc.get("name"),
    "description": doc.get("description"),
    "data": doc.get("data"),
}
print(json.dumps(out))
PY
)
  code=$(curl -sw '%{http_code}' -o /tmp/lf_patch.out \
    -X PATCH "$LF_URL/api/v1/flows/$fid" \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    --data "$payload")
  if [[ "$code" == "200" ]]; then
    echo "   ✓ ok (HTTP 200)"
  else
    echo "   ✗ failed (HTTP $code): $(head -c 200 /tmp/lf_patch.out)"
  fi
done

echo "[done] flows re-imported"
