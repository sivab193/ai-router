# Profiles and model hub

Phase 5 (profiles) and Phase 6 (model hub) extend the control plane beyond
one shared gateway: spend is attributable per project, and models are a
curated registry with one-click deploys. Both are operator-facing only —
they live behind the admin console and the admin bearer token, never on the
public inference hostname.

## Profiles

A profile is a billing and access boundary: one project, one budget, one
model allowlist, one LiteLLM Team. Every profile maps to exactly one
LiteLLM team; LiteLLM enforces budgets and model access at request time,
and the control plane owns the definitions and the spend rollup.

Starter profiles (seeded on first start; budgets are modest placeholders —
adjust them with `PATCH /control/v1/profiles/{id}`):

| Profile | Monthly budget | GPU budget | Allowlist |
| --- | --- | --- | --- |
| `personal` | $50 | $25 | `coding/*`, `local/*`, `system/*` |
| `aayvu` | $100 | $50 | `coding/*`, `local/*` |
| `splitllm` | $50 | $25 | `coding/*`, `local/*` |
| `everything-else` | $25 | $10 | `coding/fast`, `local/*` |

A `null` budget means uncapped. Allowlist entries are glob patterns:
`coding/*` matches `coding/fast` but not `local/x`.

### Keys

Create a key scoped to a profile from the control plane (the LiteLLM UI
still works for unscoped keys, but those are not attributed):

```bash
curl -X POST http://127.0.0.1:8088/control/v1/profiles/example/keys \
  -H "Authorization: Bearer $AI_ROUTER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"key_alias": "example-backend"}'
```

The raw key is shown once; the control plane stores only its hash. Active keys
are listed and revoked from the same dashboard, without exposing LiteLLM's UI. Key
creation fails closed: models outside the profile allowlist are rejected
(422), and a profile whose month spend has reached its budget gets no new
keys (409). If the profile has no LiteLLM team yet, the first key creation
creates it.

### Spend sync and alerts

Every profile is synchronized automatically every 15 minutes by default.
`POST /control/v1/profiles/{id}/sync` (or the **Sync spend** button in the
console) triggers the same work immediately:

1. Creates or updates the LiteLLM team from the profile definition.
2. Pulls the last 30 days of team spend from LiteLLM into
   `profile_usage_daily`.
3. Attributes today's allocated GPU cost from live hub deployments, then
   evaluates alerts.

Alerts fire once per period and are deduplicated by the database:

- `budget_50`, `budget_80`, `budget_100` — month spend crossed 50%, 80%,
  100% of the monthly budget.
- `token_anomaly` — today's tokens are at least 2x the 7-day daily mean
  (needs 3+ days of baseline history; otherwise it stays silent rather than
  guessing).

Unacknowledged alerts show on the profile card. Acknowledge one with
`POST /control/v1/profiles/{id}/alerts/{alert_id}/acknowledge`.

### Two cost ledgers

Provider spend and allocated GPU cost are recorded separately and never
merged into one number that could read as a cloud API invoice:

- **Provider spend** (`provider_spend_usd`) comes from LiteLLM team spend —
  actual variable API cost.
- **GPU cost** (`gpu_cost_usd`) is allocated from hub deployments:
  `hourly_cost_usd × active time`, attributed to the deployment's profile.

The dashboard shows both lines plus the total. The monthly budget covers the
total, while the GPU budget independently blocks new paid GPU deployments once
its own allocation is exhausted. Existing machines are not destroyed
automatically; teardown remains an explicit operator action.

### Migrating existing keys

Keys created before profiles existed carry tags, not profile membership.
Migrate them once:

```bash
# Discover keys from LiteLLM and map them by tag/alias:
curl -X POST http://127.0.0.1:8088/control/v1/profiles/migrate-keys \
  -H "Authorization: Bearer $AI_ROUTER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"discover": true}'

# Or supply keys explicitly (raw key or its hash; raw keys are hashed
# immediately and never stored):
curl -X POST http://127.0.0.1:8088/control/v1/profiles/migrate-keys \
  -H "Authorization: Bearer $AI_ROUTER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"keys": [{"key_alias": "aayvu-demo", "key": "sk-...", "tags": ["aayvu"]}]}'
```

Mapping rules: a tag or alias containing `aayvu` goes to `aayvu`,
`splitllm`/`split` to `splitllm`, `personal`/`dev`/`me` to `personal`.
Anything unmatched goes to `everything-else` and is listed in
`flagged_for_review` — visibly, never silently to `personal`. Already-mapped
keys are skipped unless `force: true`.

## Model hub

The hub is a curated shelf of models: a registry with immutable versions,
a storage index, KitOps packaging, and one-click deploys. It is not a
training platform (training stays in the compute pool) and not a second
router — serving always goes through ai-router; the hub only provisions the
runner that serves.

### Registry

Models are seeded on first start: `qwen-image-2.1`, `qwen3-coder-30b`,
`deepseek-v4.1-flash`, and the planned `aayvu-ft` placeholder. Add more with
`POST /control/v1/hub/models`; add versions with
`POST /control/v1/hub/models/{id}/versions`.

**Versions are immutable.** A SQLite trigger rejects any update to a
version's recorded fields — a changed weight file is a new version, and the
`latest` pointer moves to it. Only the `latest` flag itself may change.

Each version records `source_revision`, `quantization`, `size_bytes`,
`context_length`, `min_vram_mib`, `runtime`, `sha256`, and `locations` —
where the weights actually live (NAS path, Drive folder, ROG disk). The
registry indexes storage; it never forces a move. Unknown figures stay
`null` rather than becoming fake precise numbers.

### Packaging

Two layouts, same metadata:

- **KitOps/ModelKit** — `GET /control/v1/hub/models/{id}/versions/{v}/kitfile`
  renders a Kitfile the operator packs and pushes with the `kit` CLI. The
  control plane never shells out to `kit`.
- **Plain manifest** — `GET .../manifest` renders a `model.json` for the
  no-KitOps fallback: one directory per model version on the NAS holding
  this file next to the weights.

### Deploy targets

| Target | GPU | VRAM | $/hr | Provisioning |
| --- | --- | --- | --- | --- |
| `gcp-l4` | NVIDIA L4 | 24 GB | $0.71 | gcloud |
| `gcp-a100-40` | NVIDIA A100 40GB | 40 GB | $1.60 | gcloud |
| `gcp-a100-80` | NVIDIA A100 80GB | 80 GB | $2.10 | gcloud |
| `runpod-4090` | RTX 4090 | 24 GB | $0.34 | manual |
| `vast-4090` | RTX 4090 | 24 GB | $0.47 | manual |
| `colab-t4` | Tesla T4 | 15 GB | $0.00 | manual, ephemeral |
| `fleet-rog` | RTX 3060 Laptop | 6 GB | $0.00 | manual |
| `fleet-mini` | Apple M4 (shared) | 16 GB | $0.00 | manual |

Hourly costs are estimates checked 2026-11-20 and labelled as such in every
quote; verify against the provider before a long run. $0.00 means the owner
already pays for the hardware.

### Deploy flow

1. **Quote** — `POST /control/v1/hub/deployments/quote` with model, target,
   and profile. The quote shows the hourly cost (labelled estimate), the fit
   verdict, the profile, and the budget remaining. Quotes expire after 15
   minutes and are single-use.
2. **Fit check** — the model refuses to deploy when its recorded minimum
   VRAM exceeds the target GPU. Unknown minimum VRAM warns and requires an
   explicit `allow_unknown_fit` override. Cloud targets also refuse
   local-only weights (no Hugging Face source, no ModelKit ref) because
   they cannot reach his NAS, Drive, or ROG disk.
3. **Confirm** — `POST /control/v1/hub/deployments` with `quote_id` and
   `confirm: true`. Deploying into a budget-exhausted profile is rejected.
4. **Launch** — for GCP targets the response includes a `gcloud compute
   instances create` command and a startup script: the VM downloads the
   model from Hugging Face, starts vLLM in Docker bound to loopback, then
   enrolls itself as a runner named `hub-<deployment-id>`. For manual
   targets the response includes an install command to run on the target
   after vLLM is up.
5. **Registration** — when the runner enrolls, the deployment links to it
   and its alias (`local/<model>-<version>`) is added to the owning
   profile's allowlist, so keys scoped to that profile can use the model.
   The runner registers in LiteLLM through the normal connect path.

The enrollment token for a hub deploy is single-use with a two-hour window
instead of the usual fifteen minutes: the VM must download the model
before it can enroll, and large models take a while. It is passed to the VM
through GCE instance metadata; the VM sees no provider credentials.

**Auto-provision** (`AI_ROUTER_GCP_AUTO_PROVISION=1` plus
`AI_ROUTER_GCP_PROJECT`) runs the `gcloud` command from the control plane
instead of handing it to the operator. It needs the `gcloud` CLI where the
control plane runs — the stock Docker image does not include it, so the
default is manual provisioning. A failed `gcloud` call marks the deployment
`failed` with the error recorded; re-quote to retry.

### Teardown

`POST /control/v1/hub/deployments/{id}/teardown` removes routing first:
the relay connection closes, the LiteLLM model is unregistered, and the
runner is revoked — no request can reach the instance after this. Then, for
GCP deployments with auto-provision on, the VM is deleted; otherwise the
response includes the `gcloud compute instances delete` command for the
operator. Routing never outlives teardown.

### Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `AI_ROUTER_NAS_PATH` | — | NAS path where weights live (index only) |
| `AI_ROUTER_OCI_REGISTRY` | — | Registry for ModelKit artifacts |
| `AI_ROUTER_GCP_PROJECT` | — | GCP project for deploys |
| `AI_ROUTER_GCP_ZONE` | `us-central1-a` | GCP zone for deploys |
| `AI_ROUTER_GCP_AUTO_PROVISION` | `0` | Run `gcloud` from the control plane |
| `AI_ROUTER_VLLM_IMAGE` | `vllm/vllm-openai:latest` | vLLM image for cloud deploys |

### Control key routes

The route-restricted LiteLLM control key covers the profile routes:
`/team/new`, `/team/update`, `/team/delete`, `/team/info`,
`/team/daily/activity`, `/key/generate`, `/key/list`, and `/key/delete`.
After upgrading, re-run `scripts/provision-control-key.py` — it detects the
route change and re-provisions the key.

### Known limits

- Real-GPU end-to-end validation of the whole deploy path is still pending
  (needs a rented GPU and real spend). The quote/deploy/teardown flow is
  covered by tests, but no deployment has run against a live GPU yet.
- The sync pulls daily spend from LiteLLM's team activity endpoint; if a
  future LiteLLM version changes that endpoint, sync degrades to
  GPU-cost-only attribution and reports `spend_synced: false`.
- Colab sessions can end without warning; inference is stateless, so this
  is a warning in the target notes, not a block.
