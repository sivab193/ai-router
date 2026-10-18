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
