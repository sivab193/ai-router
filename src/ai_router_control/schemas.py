from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator


ALIAS_PATTERN = re.compile(r"^local/[a-z0-9][a-z0-9._/-]{0,126}$")


class EnrollmentTokenCreate(BaseModel):
    name: str = Field(default="runner", min_length=1, max_length=80)
    expires_in_seconds: int = Field(default=900, ge=60, le=86400)


class RunnerEnroll(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    model_alias: str | None = None
    snapshot: dict[str, Any]

    @field_validator("model_alias")
    @classmethod
    def validate_alias(cls, value: str | None) -> str | None:
        if value is not None and not ALIAS_PATTERN.fullmatch(value):
            raise ValueError("model_alias must match local/<lowercase-name>")
        return value


class RunnerHeartbeat(BaseModel):
    snapshot: dict[str, Any]


class RunnerView(BaseModel):
    id: str
    name: str
    model_alias: str
    upstream_model: str
    status: str
    created_at: float
    last_seen_at: float | None
    disconnected_at: float | None
    revoked_at: float | None
    litellm_model_id: str | None
    gateway_sync_error: str | None
    snapshot: dict[str, Any]


# -- Profiles (Phase 5) -----------------------------------------------------


class ProfileCreate(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    gpu_budget_usd: float | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    monthly_token_ceiling: int | None = Field(default=None, ge=1)
    allowed_models: list[str] = Field(default_factory=list)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    gpu_budget_usd: float | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    monthly_token_ceiling: int | None = Field(default=None, ge=1)
    allowed_models: list[str] | None = None


class ProfileKeyCreate(BaseModel):
    key_alias: str = Field(min_length=1, max_length=80)
    models: list[str] | None = None
    max_budget: float | None = Field(default=None, ge=0)


class MigrationKey(BaseModel):
    key_alias: str = Field(min_length=1, max_length=80)
    key: str | None = None
    key_hash: str | None = None
    tags: list[str] = Field(default_factory=list)


class MigrationRequest(BaseModel):
    keys: list[MigrationKey] | None = None
    discover: bool = False
    force: bool = False


# -- Model hub (Phase 6) ----------------------------------------------------


class HubModelCreate(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)
    source_type: str = Field(pattern="^(huggingface|local|url)$")
    source: str = Field(min_length=1, max_length=500)
    license: str | None = Field(default=None, max_length=200)
    format: str = Field(pattern="^(gguf|safetensors|awq|fp8|other)$")
    status: str = Field(default="active", pattern="^(active|planned)$")
    notes: str = Field(default="", max_length=2000)


class HubVersionCreate(BaseModel):
    version: str = Field(min_length=1, max_length=128)
    source_revision: str | None = Field(default=None, max_length=200)
    quantization: str | None = Field(default=None, max_length=60)
    size_bytes: int | None = Field(default=None, ge=0)
    context_length: int | None = Field(default=None, ge=1)
    min_vram_mib: int | None = Field(default=None, ge=0)
    runtime: str = Field(pattern="^(vllm|ollama|llama.cpp|comfyui|other)$")
    sha256: str | None = Field(default=None, max_length=64)
    modelkit_ref: str | None = Field(default=None, max_length=500)
    locations: list[dict[str, Any]] = Field(default_factory=list)


class DeployQuoteRequest(BaseModel):
    model_id: str
    version: str | None = None
    target: str
    profile_id: str


class DeploymentCreate(BaseModel):
    quote_id: str
    confirm: bool = False
    alias: str | None = None
    allow_unknown_fit: bool = False
