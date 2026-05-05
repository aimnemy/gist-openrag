#!/usr/bin/env bash
# Fill the REPLACE_ME_* secrets in .env with strong dev values.
# Idempotent: only rewrites placeholders, leaves real values alone.

set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=.env
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[error] $ENV_FILE not found" >&2
  exit 1
fi

gen_urlsafe() { python3 -c "import secrets; print(secrets.token_urlsafe(32))"; }
gen_b64_32()  { python3 -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"; }
gen_pw()      { python3 -c "import secrets,string; a=string.ascii_letters+string.digits+'!@#%^*'; print(''.join(secrets.choice(a) for _ in range(20)))"; }

replace() {
  local key="$1" value="$2"
  if grep -q "^${key}=REPLACE_ME" "$ENV_FILE"; then
    # Use | as sed delimiter since values may contain / and +
    python3 -c "
import sys, pathlib
p = pathlib.Path('$ENV_FILE')
lines = p.read_text().splitlines()
out = []
for ln in lines:
    if ln.startswith('${key}=') and 'REPLACE_ME' in ln:
        out.append('${key}=$value')
    else:
        out.append(ln)
p.write_text('\n'.join(out) + '\n')
"
    echo "[secrets] filled $key"
  else
    echo "[secrets] $key already set — skipping"
  fi
}

replace LANGFLOW_SECRET_KEY            "$(gen_urlsafe)"
replace LANGFLOW_SUPERUSER_PASSWORD    "$(gen_pw)"
replace OPENRAG_ENCRYPTION_KEY         "$(gen_b64_32)"

echo "[secrets] done. Review $ENV_FILE before 'docker compose up -d'."
