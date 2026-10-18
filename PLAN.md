# ai-router implementation plan

## Decision

Start with **LiteLLM Proxy + PostgreSQL + Redis** as the gateway, with **vLLM** as the reference self-hosted inference runtime. Ship a lightweight `ai-router-runner` and control API around them rather than replacing the gateway. The MVP now uses an authenticated outbound WebSocket relay, so temporary runners do not need a public URL or a separately configured overlay network.

LiteLLM gives a single OpenAI-compatible endpoint for applications, virtual keys, spend tracking, budgets, rate limiting, provider routing, and custom OpenAI-compatible model endpoints. vLLM exposes an OpenAI-compatible inference server and has an official container image, making it a good default for NVIDIA GPU VMs. The gateway can therefore treat a remote vLLM server like any other provider.

### Why this is better than a fresh router

Building a reliable gateway means implementing protocol translation, streaming, retries, provider quirks, authentication, key rotation, accounting, concurrency limits, quotas, audit logs, and dashboards. These are the parts most likely to produce surprise bills or leak credentials. Use the battle-tested implementation for them; invest custom engineering in portable remote inference nodes and clear cost allocation.

## Scope and non-goals

### Version 1 includes

- `https://ai.siv19.dev/v1` as an OpenAI-compatible endpoint for apps and agents.
- Multiple commercial providers and aliases such as `coding/fast`, `coding/reasoning`, and `local/qwen`.
- Per-client virtual keys with allowed-model scopes, RPM/TPM limits, hard budget windows, revocation, and tags such as `app`, `environment`, and `agent`.
- Per-request usage records: input, output, cached/reasoning tokens when reported, latency, selected route, status, and USD cost.
- vLLM-backed runners on a GPU VM; outbound-only connectivity from the runner to the router.
- Fallback rules only among compatible models and only where the request can be safely retried.
- Dashboard/reporting for spend by app, virtual key, model alias, provider, and runner.

### Explicit non-goals for version 1

- Being a public multi-tenant OpenRouter replacement or reselling inference.
- Accepting arbitrary user-configured URLs as model targets.
- Auto-provisioning GPUs, training/fine-tuning, or multi-node model parallelism.
- Claiming a precise token cost for self-hosted inference without a declared allocation policy.

## System components

| Component | Responsibility | First choice |
| --- | --- | --- |
| Edge | DNS, TLS, DDoS protection, route access restrictions | Existing `ai.siv19.dev` edge setup |
| Gateway | OpenAI API, provider adapters, virtual keys, quotas, routing, request logging | LiteLLM Proxy |
| State | Gateway data, usage ledger, runner registry, audit records | PostgreSQL |
| Hot-path coordination | distributed rate limits, cache, health state | Redis |
| Inference runtime | serve open-weight model behind an OpenAI-compatible API | vLLM; SGLang/Ollama adapters later |
| Runner | start runtime, enroll, report health, maintain private connection | custom `ai-router-runner` |
| Runner relay | runner-initiated encrypted request/response path | ai-router authenticated WebSocket relay; TLS at the public edge |
| Control API | enroll/revoke runners and publish only validated endpoints to the gateway | custom small service |
| Observability | metrics, logs, cost reports, alerts | OpenTelemetry + Prometheus/Grafana initially |

## Runner connectivity model

Every runner receives a short-lived, single-use enrollment token. On boot it:

1. Starts vLLM bound to loopback only (for example `127.0.0.1:8000`).
2. Discovers the vLLM version, model card, routes, GPU metadata, load, and metrics from loopback.
3. Exchanges the enrollment token for a runner ID and revocable runner credential.
4. Opens an **outbound** authenticated WebSocket to the control plane.
5. Sends health/capacity heartbeats and relays streaming inference traffic; connection loss immediately removes the runner deployment from LiteLLM.

The control plane validates observed model metadata, saves the runner, and supplies a gateway deployment entry that points only to an internal relay URL. Provider credentials are never placed on a runner. A compromised runner can be revoked without changing any app key.

The MVP relay works for stable VMs and Colab/ephemeral sessions with the same install command. A private overlay remains a future optimization for very high-throughput stable fleets. Do not expose port 8000, a notebook URL, or a raw vLLM API publicly.

## Cost and token accounting

Maintain two clearly separate ledgers:

1. **Provider usage:** tokens and reported/known per-token price for OpenAI, Anthropic, OpenRouter, etc. This is the actual variable API spend.
2. **Self-hosted usage:** input/output tokens, requests, latency, runner ID, GPU type, wall-clock runtime, and an allocation policy. The default report should show `allocated_gpu_cost`, computed from `(VM/GPU hourly price + storage/network overhead) × active time`, then attributed by a documented rule (for example, output-token weighted while the runner is active).

For each request, preserve `provider_cost_usd`, `allocated_gpu_cost_usd`, `pricing_source`, and `pricing_version`. Never merge them into a label that implies a cloud API invoice.

## Phases

### Phase 0 — foundation and threat model (implemented baseline)

- Create deployment/secrets inventory: DNS ownership, host, database, Redis, object storage if needed, and CI identity.
- Define namespaces: `system/*`, `coding/*`, `local/*`, and provider-specific aliases.
- Define tenant tags and budgets: start with `personal`, `dev`, and each app/agent.
- Write a threat model covering leaked client keys, leaked provider keys, SSRF through custom endpoints, malicious runner enrollment, public vLLM exposure, replayed runner tokens, and unexpected fallback spend.
- Pin container image versions and establish a patch/update policy.

**Exit criterion:** a reviewed configuration matrix exists and no secret is committed to Git.

### Phase 1 — usable hosted-model gateway (2–3 days)

- Deploy LiteLLM, PostgreSQL, and Redis in a private network; expose only the edge/gateway route.
- Configure two providers and named model aliases; keep raw provider model names unavailable to ordinary client keys.
- Create per-app virtual keys with model scopes, hard monthly/weekly budgets, RPM/TPM limits, and tags.
- Point one sample app and one coding agent at `https://ai.siv19.dev/v1`.
- Enable usage logging, dashboard access only for administrators, structured request logs, metrics, and alerts for budget exhaustion/5xx/429.

**Exit criterion:** requests stream successfully; a test key cannot use a forbidden model, exceed its quota, or access gateway administration; usage can be filtered by app and key.

### Phase 2 — private GPU runner proof of concept (MVP implemented; real GPU validation pending)

- Implement the minimum runner as a Docker Compose profile: vLLM plus a private tunnel/overlay client.
- Run it first on one rented NVIDIA GPU VM with one small, licensed open-weight model.
- Build a narrow runner registry/control API: issue enrollment token, validate input, persist runner, list/revoke, health checks, and generate a gateway deployment configuration.
- Add the model as `local/<model-alias>` and route one test request through the gateway.
- Test runner shutdown, tunnel loss, revocation, and recovery; verify no direct public inference port responds.

**Exit criterion:** a fresh VM can be enrolled in under 15 minutes, serves through `ai.siv19.dev`, disappears within the heartbeat timeout, and leaves no exposed model port.

### Phase 3 — policies, reliability, and accounting (3–5 days)

- Implement policy aliases with explicit fallback ladders, timeout limits, maximum request cost, and circuit breakers.
- Add idempotency and safe retry rules; never retry streaming/tool side-effect requests unless the client opts in.
- Ingest VM runtime cost and calculate labelled allocated GPU cost.
- Add daily/weekly usage reports, per-agent dashboard views, and alerts for abnormal token volume, 429s, and runner cost.
- Capture traces and model/route decisions without storing prompt bodies by default.

**Exit criterion:** a failure drill routes only to approved fallbacks and reports an understandable reason/cost; daily report reconciles provider spend against invoices within the expected rounding delay.

### Phase 4 — portable runners and hardening (ongoing)

- Package runners for GPU VM, RunPod/Vast-style VM, local machine, and notebook/Colab environments.
- Add SGLang and Ollama runtime adapters only after their capability differences are described in runner metadata.
- Add mTLS, key rotation, signed runner release images, SBOM/vulnerability scanning, backups, restore drills, and least-privilege network policies.
- Evaluate multi-node inference only when a single-node model/throughput limit proves insufficient.

## API and data contract sketch

Keep standard inference endpoints owned by the gateway:

```text
POST /v1/chat/completions
POST /v1/responses
POST /v1/embeddings
GET  /v1/models
```

Put administrative runner APIs behind a separate protected surface, never the public client endpoint:

```text
POST   /control/v1/runners/enrollment-tokens
POST   /control/v1/runners/enroll
POST   /control/v1/runners/{id}/heartbeat
POST   /control/v1/runners/{id}/revoke
GET    /control/v1/runners
GET    /control/v1/usage
```

Minimal runner record: `id`, `owner`, `status`, `runtime`, `model_ids`, `capabilities`, `private_endpoint`, `public_key_fingerprint`, `last_heartbeat_at`, `gpu_type`, `gpu_count`, `hourly_cost_usd`, `max_concurrency`, and `version`.

## Security baseline

- Use separate admin, service, and client identities. Do not use the gateway master key in applications.
- Store cloud-provider keys only in a secret manager; encrypt at rest and rotate them.
- Validate every runner URL against an allowlist/private overlay; never proxy to arbitrary caller-supplied URLs (SSRF prevention).
- Use short-lived enrollment credentials, mTLS or mutually authenticated tunnels, runner revocation, and a bounded heartbeat TTL.
- Restrict admin UI/control APIs by SSO or identity-aware access; disable them from the broad public internet.
- Redact `Authorization`, provider keys, and prompt content from normal logs. Make prompt capture an explicit, time-limited debugging choice.
- Pin releases and subscribe to security advisories. Gateway software holds billable provider credentials, so patching is part of the operating model.

## Open-source options considered

| Option | Fit | Decision |
| --- | --- | --- |
| LiteLLM Proxy | Broad provider compatibility, virtual keys, spend/budgets, limits, a ready UI, and custom OpenAI-compatible endpoints | **Use as v1 gateway** |
| Bifrost | MIT gateway with OpenAI/Anthropic compatibility, virtual keys, routing, limits, and observability | Strong alternative/Pilot if a TypeScript/Bun stack is preferred |
| Kong AI Gateway | Good for organisations already operating Kong and needing API-gateway controls | Not needed for a personal-first project |
| Helicone | Excellent observability focus | Optional later; not a complete routing/control plane |
| vLLM | High-throughput, OpenAI-compatible open-weight inference server | **Use as default runner runtime** |
| Build gateway from scratch | Maximum control, maximum security/maintenance burden | Defer indefinitely; build only the runner/control-plane gap |

## Open questions to resolve before implementation

1. Where will the public router and state services run, and who manages `ai.siv19.dev` DNS/edge controls?
2. Is this strictly personal/single-team, or will other people receive client keys?
3. Which initial cloud providers and coding agents must work on day one?
4. Is a private overlay acceptable for persistent GPU VMs, and which reverse-tunnel service is acceptable for temporary notebook runners?
5. What is the monthly hard cap for provider spend and GPU rentals, and should the default action at cap be reject or use a free/local fallback?

## Primary sources

- [LiteLLM gateway overview](https://docs.litellm.ai/docs/simple_proxy) — OpenAI-compatible proxy, virtual keys, budgets, rate limits, and spend tracking.
- [LiteLLM budgets and limits](https://docs.litellm.ai/docs/proxy/users) — persistent database plus Redis requirements for correct multi-instance budget/rate enforcement.
- [LiteLLM custom API server](https://docs.litellm.ai/docs/providers/custom_llm_server) — custom and OpenAI-compatible internal model endpoints.
- [vLLM Docker deployment](https://docs.vllm.ai/en/v0.30.0/deployment/docker/) — official OpenAI-compatible inference container.
- [Bifrost repository](https://github.com/boelabs/bifrost) — alternative MIT gateway with virtual keys, routing, rate limits, and observability.
