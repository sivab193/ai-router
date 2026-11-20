from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    admin_token: str
    database_path: Path
    public_url: str
    internal_url: str
    litellm_url: str | None
    litellm_control_key: str | None
    heartbeat_ttl_seconds: int
    request_start_timeout_seconds: int
    request_idle_timeout_seconds: int
    # Model hub (Phase 6). All optional; the hub degrades gracefully
    # without them (local-only registry, operator-run provisioning).
    hub_nas_path: str | None = None
    oci_registry: str | None = None
    gcp_project: str | None = None
    gcp_zone: str = "us-central1-a"
    gcp_auto_provision: bool = False
    vllm_image: str = "vllm/vllm-openai:latest"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            admin_token=_required_env("AI_ROUTER_ADMIN_TOKEN"),
            database_path=Path(os.getenv("AI_ROUTER_DB_PATH", "data/control.db")),
            public_url=os.getenv("AI_ROUTER_PUBLIC_URL", "http://127.0.0.1:8080").rstrip("/"),
            internal_url=os.getenv("AI_ROUTER_INTERNAL_URL", "http://control:8080").rstrip("/"),
            litellm_url=os.getenv("LITELLM_URL", "http://litellm:4000").rstrip("/") or None,
            litellm_control_key=os.getenv("LITELLM_CONTROL_KEY") or None,
            heartbeat_ttl_seconds=_int_env("AI_ROUTER_HEARTBEAT_TTL_SECONDS", 30),
            request_start_timeout_seconds=_int_env("AI_ROUTER_REQUEST_START_TIMEOUT_SECONDS", 90),
            request_idle_timeout_seconds=_int_env("AI_ROUTER_REQUEST_IDLE_TIMEOUT_SECONDS", 300),
            hub_nas_path=os.getenv("AI_ROUTER_NAS_PATH") or None,
            oci_registry=os.getenv("AI_ROUTER_OCI_REGISTRY") or None,
            gcp_project=os.getenv("AI_ROUTER_GCP_PROJECT") or None,
            gcp_zone=os.getenv("AI_ROUTER_GCP_ZONE", "us-central1-a"),
            gcp_auto_provision=os.getenv("AI_ROUTER_GCP_AUTO_PROVISION", "0") == "1",
            vllm_image=os.getenv("AI_ROUTER_VLLM_IMAGE", "vllm/vllm-openai:latest"),
        )
