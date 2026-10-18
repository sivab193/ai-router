from __future__ import annotations

import hashlib
import hmac
import secrets


def issue_token(prefix: str, size: int = 32) -> str:
    return f"{prefix}_{secrets.token_urlsafe(size)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(token_hash(token), expected_hash)


def bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, separator, value = header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not value:
        return None
    return value.strip()
