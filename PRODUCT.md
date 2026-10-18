# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegated by the user: Python/FastAPI for the control plane and runner relay, LiteLLM for the gateway, PostgreSQL and Redis for gateway state, and a dependency-light Python runner for broad VM/notebook compatibility.

## Users

The primary user is the owner of `ai.siv19.dev`, connecting personal applications and coding agents to commercial model APIs and open-weight models running on local, rented, or temporary GPU machines.

## Product Purpose

`ai-router` provides one OpenAI-compatible API and one operational view across hosted providers and self-hosted inference. A new vLLM machine should join with one enrollment token and one shell command, advertise what it can actually serve, and disappear from active routing promptly when its process or network connection ends.

## Positioning

Unlike a provider-only gateway, `ai-router` makes arbitrary short-lived compute a first-class model deployment through a runner-initiated connection, while retaining centralized keys, routing, accounting, and availability state.

## Operating Context

- Applications and coding agents call `https://ai.siv19.dev/v1` with scoped virtual keys.
- vLLM runs on laptops, GPU VMs, rented GPUs, or Colab-like notebook sessions.
- Operators enroll nodes by curl, then watch health, model metadata, load, and token counters in a browser.
- LiteLLM owns provider translation, client keys, budgets, rate limits, and request accounting.

## Capabilities and Constraints

- Runner installation must be possible with a single curl-to-shell command and an enrollment token.
- Runners initiate outbound connections; vLLM remains bound to loopback and needs no public inbound port.
- Availability must change automatically on connection/disconnection and heartbeat expiry.
- Model ID, vLLM version, context limit when exposed, supported routes, GPU details, load, and Prometheus token counters are discovered from vLLM rather than fabricated.
- Some capabilities are configuration-dependent and cannot be reliably inferred from the model name; unknown metadata must remain explicitly unknown.
- The first release is personal/single-team, not a public inference resale platform.

## Evidence on Hand

- The architecture and delivery plan live in `README.md` and `PLAN.md`.
- No production usage claims, customer evidence, logo, or existing visual identity exists; future work must not fabricate them.

## Product Principles

1. One endpoint for clients; one outbound connection for runners.
2. Report observed capability and availability, never optimistic guesses.
3. Provider secrets remain central and client access is revocable and scoped.
4. Offline compute fails closed and leaves routing quickly.
5. Hosted-token spend and allocated GPU cost remain distinct.
