#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "deploy-release.sh must run as root" >&2
  exit 1
fi

archive="${1:-}"
revision="${2:-}"
if [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]]; then
  echo "revision must be a full Git commit SHA" >&2
  exit 1
fi
if [[ "$archive" != "/tmp/ai-router-${revision}.tar.gz" || ! -f "$archive" ]]; then
  echo "expected release archive at /tmp/ai-router-${revision}.tar.gz" >&2
  exit 1
fi

releases=/opt/ai-router-releases
release="$releases/$revision"
active=/opt/ai-router-current
previous=""
if [[ -L "$active" ]]; then
  previous="$(readlink -f "$active")"
fi

install -d -m 0750 "$release"
tar -xzf "$archive" -C "$release"

if [[ -n "$previous" && -f "$previous/.env" ]]; then
  install -m 0600 "$previous/.env" "$release/.env"
elif [[ -f /opt/ai-router/.env ]]; then
  install -m 0600 /opt/ai-router/.env "$release/.env"
else
  echo "no production .env found" >&2
  exit 1
fi

ln -sfn "$release" "${active}.new"
mv -Tf "${active}.new" "$active"

if ! env AI_ROUTER_DEPLOY_URL=https://ai.siv19.dev "$active/scripts/start.sh"; then
  if [[ -n "$previous" ]]; then
    ln -sfn "$previous" "${active}.new"
    mv -Tf "${active}.new" "$active"
    env AI_ROUTER_DEPLOY_URL=https://ai.siv19.dev "$active/scripts/start.sh" || true
  fi
  echo "deployment failed; restored previous release" >&2
  exit 1
fi

rm -f "$archive"
echo "deployed $revision"
