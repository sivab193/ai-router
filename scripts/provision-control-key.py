#!/usr/bin/env python3
"""Provision the route-restricted LiteLLM key used by the control plane."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


USER_ID = "ai-router-control-plane"
ALLOWED_ROUTES = ["/model/new", "/model/delete"]


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name] = value
    return values


def request(
    base_url: str,
    master_key: str,
    method: str,
    path: str,
    payload: dict | None = None,
) -> tuple[int, dict]:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {master_key}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}", data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        if error.code == 404:
            return error.code, {"detail": detail}
        raise RuntimeError(f"LiteLLM {path} returned HTTP {error.code}: {detail[:500]}") from error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--litellm-url", default="http://127.0.0.1:4000")
    args = parser.parse_args()

    env = load_env(args.env_file)
    master_key = env.get("LITELLM_MASTER_KEY", "")
    control_key = env.get("LITELLM_CONTROL_KEY", "")
    if not master_key or not control_key:
        raise SystemExit("LITELLM_MASTER_KEY and LITELLM_CONTROL_KEY must exist in .env")

    query = urllib.parse.urlencode({"user_id": USER_ID})
    status, _ = request(args.litellm_url, master_key, "GET", f"/user/info?{query}")
    if status == 404:
        request(
            args.litellm_url,
            master_key,
            "POST",
            "/user/new",
            {
                "user_id": USER_ID,
                "user_email": "control-plane@ai-router.internal",
                "user_alias": "ai-router control plane",
                "user_role": "proxy_admin",
                "auto_create_key": False,
            },
        )

    key_hash = hashlib.sha256(control_key.encode()).hexdigest()
    query = urllib.parse.urlencode({"key": key_hash})
    status, key_info = request(args.litellm_url, master_key, "GET", f"/key/info?{query}")
    if status != 404:
        stored = key_info.get("info", key_info)
        if (
            stored.get("key_type") != "default"
            or stored.get("allowed_routes") != ALLOWED_ROUTES
            or stored.get("user_id") != USER_ID
        ):
            request(
                args.litellm_url,
                master_key,
                "POST",
                "/key/delete",
                {"keys": [control_key]},
            )
            status = 404
    if status == 404:
        request(
            args.litellm_url,
            master_key,
            "POST",
            "/key/generate",
            {
                "key": control_key,
                "key_alias": "ai-router-control-plane",
                "key_type": "default",
                "allowed_routes": ALLOWED_ROUTES,
                "user_id": USER_ID,
                "metadata": {"purpose": "dynamic vLLM runner registration"},
            },
        )
    else:
        request(
            args.litellm_url,
            master_key,
            "POST",
            "/key/update",
            {
                "key": control_key,
                "key_alias": "ai-router-control-plane",
                "allowed_routes": ALLOWED_ROUTES,
                "user_id": USER_ID,
            },
        )

    print("Provisioned LiteLLM control key with /model/new and /model/delete only.")


if __name__ == "__main__":
    main()
