# Deploy ai-router

This guide deploys the Docker Compose stack on a stable host and publishes only
the Caddy edge through Cloudflare Tunnel.

## 1. Prepare the gateway

```bash
git clone https://github.com/sivab193/ai-router.git
cd ai-router
AI_ROUTER_DEPLOY_URL=https://ai.example.com ./scripts/start.sh
```

The host binds:

| Address | Purpose |
| --- | --- |
| `127.0.0.1:8088` | Public-facing Caddy origin for the tunnel |
| `127.0.0.1:4000` | Loopback-only LiteLLM management UI/API |

The Caddy origin permits `/v1/*`, `/control/*`, `/install/*`, `/healthz`, and
the root console. It returns 404 for LiteLLM management routes such as `/ui`,
`/key`, `/model`, `/user`, and `/config`.

## 2. Create the tunnel route

Copy [deploy/cloudflared.example.yml](../deploy/cloudflared.example.yml) to a
private location, replace the tunnel ID, credential path, and hostname, then:

```bash
cloudflared tunnel ingress validate --config /private/path/config.yml
cloudflared tunnel route dns YOUR_TUNNEL_ID ai.example.com
cloudflared tunnel --config /private/path/config.yml run
```

Keep the final catch-all `http_status:404` rule. Run cloudflared as a supervised
system service and preserve WebSocket upgrades. Do not commit the tunnel JSON or
private deployment config.

## 3. Verify exposure

```bash
curl -fsS https://ai.example.com/healthz
curl -i https://ai.example.com/ui
curl -i https://ai.example.com/model/info
curl -i https://ai.example.com/v1/models
```

Expected results are 200, 404, 404, and 401 respectively. The final 401 proves
the inference route is present but requires a LiteLLM key.

## Operations

- Back up the Postgres, Redis, and control-plane Docker volumes.
- Back up `LITELLM_SALT_KEY` separately; never rotate it casually.
- Store `.env` in a host secret manager where possible and keep mode `0600`.
- Give every application its own virtual key, model allowlist, budget, RPM, and
  TPM limits.
- Revoke runner identities that are lost or no longer rented.
- Patch the gateway OS, Docker, cloudflared, and the pinned container images.
- Test database and salt-key restoration before depending on the service.
- Consider a separate Access-protected admin hostname instead of exposing any
  management route on the inference hostname.

## Upgrade

Review release notes and security advisories before changing the pinned LiteLLM
version. Back up first, then:

```bash
docker compose pull
docker compose up --build -d --wait
uv run pytest
```

Do not use the LiteLLM master key from an application or expose port 4000 beyond
loopback.
