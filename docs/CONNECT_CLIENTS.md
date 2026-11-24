# Connect clients to ai-router

All clients need a LiteLLM virtual key and a model alias. Create the key in LiteLLM's Admin UI with only the models, rate limits, and budget that client should receive.

## Discover permitted providers and services

`GET /v1/catalog` is the application-facing discovery contract. It accepts the
same profile key as inference and returns only providers and services allowed by
that profile. Catalog entries may represent language or vision models,
embeddings, rerankers, OCR engines, or document-intelligence services.

```bash
curl https://ai.siv19.dev/v1/catalog \
  -H "Authorization: Bearer $AI_ROUTER_API_KEY"
```

Applications should store the returned service `id` as the stable alias. They
must not infer access from a provider name or expose the control-plane admin
token. An organization with `allowed_providers: ["bedrock"]`, for example,
cannot discover Google services even if those services exist globally.

Document adapters and custom OCR runtimes use the same outbound-runner trust
model as vLLM. A compatible runner advertises an `ocr`,
`document_intelligence`, or `vision_model` service and serves
`/v1/documents/process` on loopback. Applications submit the chosen catalog
alias as the `service` query parameter:

```bash
curl -X POST \
  "https://ai.siv19.dev/v1/documents/process?service=google/document-ai" \
  -H "Authorization: Bearer $AI_ROUTER_API_KEY" \
  -H "Content-Type: application/pdf" \
  --data-binary @document.pdf
```

## OpenAI SDKs and applications

```bash
export OPENAI_BASE_URL=https://ai.siv19.dev/v1
export OPENAI_API_KEY=sk-your-virtual-key
```

```python
from openai import OpenAI

client = OpenAI()
response = client.chat.completions.create(
    model="local/qwen-coder",
    messages=[{"role": "user", "content": "Say hello."}],
)
print(response.choices[0].message.content)
```

## Claude Code

Claude Code speaks the Anthropic Messages protocol. LiteLLM translates it to the selected backend.

```bash
export LITELLM_API_KEY=sk-your-virtual-key
export ANTHROPIC_BASE_URL=https://ai.siv19.dev
export ANTHROPIC_AUTH_TOKEN="$LITELLM_API_KEY"
export ANTHROPIC_MODEL=local/qwen-coder
unset ANTHROPIC_API_KEY
claude
```

The local vLLM model still needs a compatible chat template and tool-call parser for full Claude Code behavior. A successful text response alone does not prove agent-tool compatibility.

## Codex CLI and Codex in ChatGPT Desktop

Add this to `~/.codex/config.toml`:

```toml
model = "local/qwen-coder"
model_provider = "ai-router"

[features]
api_key_model_discovery = true
suppress_unstable_features_warning = true

[model_providers.ai-router]
name = "ai-router"
base_url = "https://ai.siv19.dev/v1"
model_catalog_url = "https://ai.siv19.dev/v1/models"
env_key = "AI_ROUTER_API_KEY"
wire_api = "responses"
stream_idle_timeout_ms = 7200000
stream_max_retries = 5
request_max_retries = 4
```

Then launch from a shell containing the virtual key:

```bash
export AI_ROUTER_API_KEY=sk-your-virtual-key
codex
```

Codex requires the OpenAI Responses API. Use a vLLM version/model combination that exposes `/v1/responses`, and test tool calls plus follow-up turns before relying on it for long coding runs.

## Direct curl test

```bash
curl https://ai.siv19.dev/v1/chat/completions \
  -H "Authorization: Bearer $AI_ROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "local/qwen-coder",
    "messages": [{"role": "user", "content": "Reply with connected."}]
  }'
```
