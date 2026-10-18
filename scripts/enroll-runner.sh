#!/usr/bin/env bash
set -euo pipefail

router_url="${AI_ROUTER_URL:-http://127.0.0.1:8080}"
runner_name="${1:-gpu-runner}"

if [[ -z "${AI_ROUTER_ADMIN_TOKEN:-}" ]]; then
  echo "Set AI_ROUTER_ADMIN_TOKEN in your shell." >&2
  exit 1
fi

curl -fsS "$router_url/control/v1/enrollment-tokens" \
  -H "Authorization: Bearer $AI_ROUTER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$runner_name\",\"expires_in_seconds\":900}"
echo
