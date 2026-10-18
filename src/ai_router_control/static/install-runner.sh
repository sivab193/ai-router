#!/usr/bin/env bash
set -euo pipefail

AI_ROUTER_URL="${AI_ROUTER_URL:-__AI_ROUTER_URL__}"
INSTALL_ROOT="${AI_ROUTER_RUNNER_HOME:-$HOME/.local/share/ai-router}"
VENV_ROOT="$INSTALL_ROOT/venv"
RUNNER_PATH="$INSTALL_ROOT/runner.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ai-router needs Python 3.12 or newer." >&2
  exit 1
fi

mkdir -p "$INSTALL_ROOT"
chmod 700 "$INSTALL_ROOT"
curl -fsSL "$AI_ROUTER_URL/install/runner.py" -o "$RUNNER_PATH"
python3 -m venv "$VENV_ROOT"
"$VENV_ROOT/bin/python" -m pip install --quiet --disable-pip-version-check "websockets>=15,<16"

exec "$VENV_ROOT/bin/python" "$RUNNER_PATH" --control-url "$AI_ROUTER_URL" "$@"
