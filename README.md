# ai-router

[![CI](https://github.com/sivab193/ai-router/actions/workflows/ci.yml/badge.svg)](https://github.com/sivab193/ai-router/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`ai-router` is a self-hosted OpenRouter-style gateway for commercial model APIs
and vLLM servers running anywhere you control. Applications and coding agents
use one OpenAI-compatible endpoint:

```text
https://ai.siv19.dev/v1
```

The gateway combines [LiteLLM](https://github.com/BerriAI/litellm) with a small
control plane and an outbound runner relay. A rented GPU, VM, workstation, or
Colab session can join without a public IP, open inference port, VPN, or
Cloudflare account.

## Features

- One-time, expiring runner enrollment commands generated in the browser.
- An outbound WebSocket relay for normal and streaming OpenAI-compatible calls.
- vLLM discovery from health, version, models, load, OpenAPI, and Prometheus
  metrics endpoints.
- Immediate online/offline state and automatic LiteLLM model registration.
- Model IDs, context limits, GPU details, token counters, load, and last-seen
  state in a compact operations console.
- LiteLLM virtual keys, per-client budgets and rate limits, spend tracking,
  provider credentials, routing, retries, and fallbacks.
- Profiles (`personal`, `aayvu`, `splitllm`, `everything-else`) mapped onto
  LiteLLM Teams, with per-profile budgets, model allowlists, spend sync,
  and budget/token alerts.
- A tenant-aware capability catalog for language and vision models, embeddings,
  OCR, and document-intelligence services. Profiles can restrict both providers
  and individual services, so an organization can be Bedrock-only, Google-only,
  or use a curated mix without receiving provider credentials.
- A model hub: a registry of models with immutable versions, KitOps/ModelKit
  packaging, and one-click deploys to GCP, RunPod, Vast.ai, Colab, and the
  home fleet — with automatic registration into the router and one-click
  teardown.
- A least-privilege internal control key limited to model registration routes.
- A public edge that blocks LiteLLM management endpoints.

## How it works

```text
applications / Claude Code / Codex
                 |
                 | OpenAI-compatible request + scoped virtual key
                 v
            ai.siv19.dev
                 |
          +------+------+
          |             |
       LiteLLM       control plane + browser console
          |             ^
          |             | outbound authenticated WebSocket
          |             |
          +------> ai-router runner
                          |
                          | loopback only
                          v
                       vLLM :8000
```

LiteLLM calls an internal relay URL. The runner forwards the request to vLLM on
loopback and streams the response back. The GPU host never exposes vLLM to the
internet.

## Quick start

Requirements: Docker Compose, OpenSSL, Python 3, and loopback ports `4000` and
`8088`.

```bash
git clone https://github.com/sivab193/ai-router.git
cd ai-router
./scripts/start.sh
```

Open `http://127.0.0.1:8088` locally. For a hosted installation, reach the same
dashboard through a private identity-aware operator channel and enter
`AI_ROUTER_ADMIN_TOKEN` when prompted. LiteLLM's management UI is an internal
implementation detail; routine operation happens only in the AI Router dashboard.

The startup script creates independent random credentials, starts Postgres,
Redis, and LiteLLM, provisions the route-restricted control key, builds the
control plane, and waits for health checks. `.env` is mode `0600` and ignored by
Git.

For a public installation and Cloudflare Tunnel example, see
[docs/DEPLOY.md](docs/DEPLOY.md).
The provider-neutral system and trust-boundary diagram is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Connect a vLLM machine

Start vLLM on loopback:

```bash
vllm serve Qwen/Qwen3-Coder-30B-A3B-Instruct \
  --host 127.0.0.1 \
  --port 8000 \
  --served-model-name qwen3-coder
```

In the ai-router console, choose **Connect machine**, create an install command,
and paste it into the GPU machine. It looks like this:

```bash
curl -fsSL https://ai.siv19.dev/install/runner.sh | bash -s -- \
  --token aienr_... \
  --name rented-a100
```

Useful runner options:

```text
--vllm-url http://127.0.0.1:8000  # default
--vllm-api-key <key>              # or VLLM_API_KEY
--model-alias local/qwen-coder    # provider/service alias; default: local/<runner-name>
--max-model-len 65536             # fallback for older vLLM model cards
--reset                           # discard saved runner identity and re-enroll
```

The runner stores only its revocable identity at
`~/.config/ai-router/runner.json` with mode `0600`. It never receives provider
credentials, LiteLLM keys, or other runners' credentials.

## What vLLM reports

| Endpoint | Used for |
| --- | --- |
| `/health` | Readiness |
| `/version` | Runtime version |
| `/v1/models` | Model IDs and, on recent releases, context length |
| `/load` | Current server load |
| `/openapi.json` | Routes exposed by that vLLM build |
| `/metrics` | Token counters, requests, KV cache, and latency |

Older vLLM releases may omit context length; pass `--max-model-len`. Tool use,
reasoning parsers, multimodal behavior, and chat templates still depend on the
model and vLLM launch flags, so route discovery alone cannot promise semantic
compatibility.

## Connect applications

Create and revoke scoped virtual keys from the AI Router dashboard. Do not place
the LiteLLM master key in an application.

```bash
export OPENAI_BASE_URL=https://ai.siv19.dev/v1
export OPENAI_API_KEY=sk-your-virtual-key
```

The same key returns only the services permitted for its profile:

```bash
curl https://ai.siv19.dev/v1/catalog \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

The operator can synchronize configured LiteLLM model aliases into the catalog
with `POST /control/v1/services/sync`; outbound runners register their own
services when they enroll. OCR and document adapters can be registered through
the service control API and served through an outbound runner.

Claude Code, Codex, SDK, and curl examples are in
[docs/CONNECT_CLIENTS.md](docs/CONNECT_CLIENTS.md).

## Profiles and model hub

Spend is attributable per project through profiles, each mapped onto one
LiteLLM Team with its own budget, model allowlist, and keys; the console
shows provider spend and allocated GPU cost as separate ledgers. Models live
in a hub registry with immutable versions and deploy to a GPU target with a
quote-first, confirm-then-launch flow. See
[docs/PROFILES_AND_HUB.md](docs/PROFILES_AND_HUB.md).

## Security model

Provider credentials are encrypted by LiteLLM in Postgres. Virtual keys,
enrollment tokens, runner tokens, and relay proxy tokens are stored as hashes.
Redis requires authentication, message logging is disabled, exceptions are
redacted, containers drop unnecessary privilege, and the public edge permits
only explicit routes.

This is still a central trust boundary. A compromise of the gateway host,
Docker administrator, LiteLLM master key, or salt key can have broad impact.
Use a dedicated patched host, full-disk encryption, protected backups, a secret
manager, and separate virtual keys with budgets for every client. Read
[SECURITY.md](SECURITY.md) before a multi-user or business deployment.

The public inference hostname does not expose the console or administrative
control routes. The dashboard is reached through a private identity-aware
operator channel. The application bearer token remains a second layer and
should still be stored like a password.

## Development

```bash
uv sync --extra dev
uv run pytest
docker compose --env-file .env.example config --quiet
```

The roadmap and threat model are in [PLAN.md](PLAN.md). Contributions are
welcome under the [MIT license](LICENSE).
