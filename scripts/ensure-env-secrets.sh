#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f .env ]]; then
  echo ".env does not exist; run ./scripts/create-env.sh first." >&2
  exit 1
fi

umask 077

append_if_missing() {
  local name="$1"
  local value="$2"
  if ! grep -q "^${name}=" .env; then
    printf '\n%s=%s\n' "$name" "$value" >> .env
    echo "Added $name to .env."
  fi
}

append_if_missing "REDIS_PASSWORD" "$(openssl rand -hex 24)"
append_if_missing "LITELLM_CONTROL_KEY" "sk-airouter-control-$(openssl rand -hex 32)"

if [[ -n "${AI_ROUTER_DEPLOY_URL:-}" ]]; then
  escaped_url="${AI_ROUTER_DEPLOY_URL//|/\\|}"
  if grep -q '^AI_ROUTER_PUBLIC_URL=' .env; then
    sed -i.bak "s|^AI_ROUTER_PUBLIC_URL=.*$|AI_ROUTER_PUBLIC_URL=${escaped_url}|" .env
    rm -f .env.bak
  else
    printf '\nAI_ROUTER_PUBLIC_URL=%s\n' "$AI_ROUTER_DEPLOY_URL" >> .env
  fi
  echo "Updated AI_ROUTER_PUBLIC_URL in .env."
fi

chmod 600 .env
