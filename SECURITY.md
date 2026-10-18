# Security policy

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use GitHub's
**Security → Report a vulnerability** flow for this repository so details stay
private until a fix is available.

Include the affected version, reproduction steps, impact, and any suggested
mitigation. Please avoid accessing data that is not your own.

## Secret handling

Never commit `.env`, runner state, tunnel credentials, database snapshots, or
real API keys. If a credential may have leaked, revoke or rotate it immediately;
removing it from Git history is not sufficient.

`LITELLM_SALT_KEY` protects encrypted provider credentials in the database. Keep
it stable, backed up separately, and restricted to the gateway host. Losing it
can make stored provider credentials unrecoverable.

## Security model

The public Caddy edge exposes inference and the ai-router control/runner routes.
LiteLLM management endpoints are intentionally blocked there and are available
only on `127.0.0.1:4000`. Applications must use scoped virtual keys, never the
LiteLLM master key.

Host root, Docker-admin access, or the LiteLLM master key remains a high-impact
trust boundary. Use a dedicated patched host, full-disk encryption, protected
backups, and a secret manager for serious multi-user deployments.
