#!/usr/bin/env bash
# Resolves the live PostgREST container IP and writes scripts/policy.yaml ready to apply.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/policy-template.yaml"
OUTPUT="$SCRIPT_DIR/policy.yaml"

POSTGREST_CONTAINER="${SOURCE_ETL_POSTGREST_CONTAINER:-source-etls-postgrest}"
OPENSHELL_NETWORK="${SOURCE_ETL_OPENSHELL_NETWORK:-}"

# Pick the IP on SOURCE_ETL_OPENSHELL_NETWORK when set, otherwise whichever
# openshell-cluster-* network the container is attached to.
_get_ip() {
  NETWORK="$OPENSHELL_NETWORK" docker inspect "$1" --format '{{json .NetworkSettings.Networks}}' \
    | python3 -c "
import os, sys, json
nets = json.load(sys.stdin)
target = os.environ.get('NETWORK', '').strip()
if target:
    ip = nets.get(target, {}).get('IPAddress', '')
else:
    ip = next((v['IPAddress'] for k, v in nets.items() if k.startswith('openshell-cluster-')), '')
print(ip)
"
}

POSTGREST_IP=$(_get_ip "$POSTGREST_CONTAINER")

if [[ -z "$POSTGREST_IP" ]]; then
  echo "error: could not find $POSTGREST_CONTAINER on ${OPENSHELL_NETWORK:-an openshell-cluster-* network}" >&2
  exit 1
fi

PHOENIX_POLICY=""
if [[ -n "${PHOENIX_COLLECTOR_ENDPOINT:-}" ]]; then
  PHOENIX_POLICY="$(
    python3 - "$PHOENIX_COLLECTOR_ENDPOINT" <<'PY'
import sys
from urllib.parse import urlparse

endpoint = sys.argv[1]
parsed = urlparse(endpoint)
if not parsed.scheme or not parsed.hostname:
    raise SystemExit(f"invalid PHOENIX_COLLECTOR_ENDPOINT: {endpoint}")
port = parsed.port or (443 if parsed.scheme == "https" else 80)
print(f"""  phoenix_collector:
    name: phoenix_collector
    endpoints:
    - host: '{parsed.hostname}'
      port: {port}
      protocol: rest
      enforcement: enforce
      rules:
      - allow:
          method: POST
          path: /**
    binaries:
    - path: /usr/bin/python3.11""")
PY
  )"
fi

PHOENIX_POLICY="$PHOENIX_POLICY" python3 - "$TEMPLATE" "$OUTPUT" "$POSTGREST_IP" <<'PY'
import os
import sys
from pathlib import Path

template, output, postgrest_ip = sys.argv[1:4]
content = Path(template).read_text(encoding="utf-8")
content = content.replace("__POSTGREST_IP__", postgrest_ip)
content = content.replace("__OPTIONAL_PHOENIX_POLICY__", os.environ.get("PHOENIX_POLICY", ""))
Path(output).write_text(content, encoding="utf-8")
PY

echo "Written $OUTPUT"
echo "  postgrest: ${POSTGREST_IP}:3000"
if [[ -n "$PHOENIX_POLICY" ]]; then
  echo "  phoenix:   ${PHOENIX_COLLECTOR_ENDPOINT}"
fi
