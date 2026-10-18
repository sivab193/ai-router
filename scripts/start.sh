#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  ./scripts/create-env.sh
fi

./scripts/ensure-env-secrets.sh

docker compose up -d --wait postgres redis litellm
python3 ./scripts/provision-control-key.py
docker compose up --build -d --wait

public_url="$(awk -F= '$1 == "AI_ROUTER_PUBLIC_URL" {print $2}' .env)"
echo "ai-router is healthy at ${public_url:-http://127.0.0.1:8088}"
echo "LiteLLM administration is loopback-only at http://127.0.0.1:4000/ui"
