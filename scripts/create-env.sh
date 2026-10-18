#!/usr/bin/env bash
set -euo pipefail

if [[ -e .env ]]; then
  echo ".env already exists; refusing to overwrite it." >&2
  exit 1
fi

umask 077
postgres_password="$(openssl rand -hex 24)"
redis_password="$(openssl rand -hex 24)"
litellm_master_key="sk-$(openssl rand -hex 32)"
litellm_salt_key="sk-$(openssl rand -hex 32)"
litellm_control_key="sk-airouter-control-$(openssl rand -hex 32)"
admin_token="adm-$(openssl rand -hex 32)"

sed \
  -e "s|__POSTGRES_PASSWORD__|$postgres_password|" \
  -e "s|__REDIS_PASSWORD__|$redis_password|" \
  -e "s|__LITELLM_MASTER_KEY__|$litellm_master_key|" \
  -e "s|__LITELLM_SALT_KEY__|$litellm_salt_key|" \
  -e "s|__LITELLM_CONTROL_KEY__|$litellm_control_key|" \
  -e "s|__AI_ROUTER_ADMIN_TOKEN__|$admin_token|" \
  .env.example > .env

chmod 600 .env
echo "Created .env with private random credentials."
