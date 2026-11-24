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

On the inference hostname, the Caddy origin permits `/v1/*`, `/install/*`,
`/healthz`, the runner enrollment endpoint, and authenticated runner WebSocket
connections. The root console, control routes, and LiteLLM management routes
such as `/ui`, `/key`, `/model`, `/user`, and `/config` remain unavailable on
the public hostname. Operators reach the loopback dashboard through a private,
identity-aware channel supplied by their hosting platform.

## 2. Create the tunnel route

Copy [deploy/cloudflared.example.yml](../deploy/cloudflared.example.yml) to a
private location, replace the tunnel ID, credential path, and hostname, then:

```bash
cloudflared tunnel --config /private/path/config.yml ingress validate
cloudflared tunnel route dns YOUR_TUNNEL_ID ai.example.com
cloudflared tunnel --config /private/path/config.yml run
```

Keep the final catch-all `http_status:404` rule. Run cloudflared as a supervised
system service and preserve WebSocket upgrades. Do not commit the tunnel JSON or
private deployment config.

Do not add an administrative hostname to this tunnel. Keep
`AI_ROUTER_ADMIN_TOKEN` enabled as defense in depth on the private dashboard.

## 3. Verify exposure

```bash
curl -fsS https://ai.example.com/healthz
curl -i https://ai.example.com/
curl -i https://ai.example.com/control/v1/runners
curl -i https://ai.example.com/ui
curl -i https://ai.example.com/model/info
curl -i https://ai.example.com/v1/models
```

Expected results are 200, 404, 404, 404, 404, and 401 respectively. The
inference 401 proves the route is present but requires a LiteLLM key.

## Operations

- Back up the Postgres, Redis, and control-plane Docker volumes.
- Back up `LITELLM_SALT_KEY` separately; never rotate it casually.
- Store `.env` in a host secret manager where possible and keep mode `0600`.
- Give every application its own virtual key, model allowlist, budget, RPM, and
  TPM limits.
- Revoke runner identities that are lost or no longer rented.
- Patch the gateway OS, Docker, cloudflared, and the pinned container images.
- Test database and salt-key restoration before depending on the service.
- Periodically review private operator access, identity-provider MFA, active
  sessions, and account membership.

## Upgrade

Review release notes and security advisories before changing the pinned LiteLLM
version. Back up first, then:

```bash
docker compose pull
docker compose up --build -d --wait
uv run pytest
```

After upgrading to a version with new control-plane routes (profiles and
the model hub added the LiteLLM team/key routes), re-run the control-key
provisioning script — it detects the route change and re-provisions the key:

```bash
python3 scripts/provision-control-key.py
```

Do not use the LiteLLM master key from an application or expose port 4000 beyond
loopback.
