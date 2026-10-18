from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("AI_ROUTER_SMOKE_BASE_URL", "http://127.0.0.1:8088").rstrip("/")
SMOKE_NAME = os.getenv("AI_ROUTER_SMOKE_RUNNER_NAME", f"compose-smoke-{int(time.time())}")


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def request_json(
    path: str,
    *,
    token: str,
    method: str = "GET",
    data: dict | None = None,
) -> tuple[int, dict | list]:
    body = json.dumps(data).encode() if data is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "ai-router-smoke/0.1.0",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=body, headers=headers, method=method
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, json.load(response)


def wait_for_runner(admin_token: str, status: str, timeout: float = 45) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        _, runners = request_json("/control/v1/runners", token=admin_token)
        matching = [runner for runner in runners if runner["name"] == SMOKE_NAME]
        if matching:
            latest = matching[-1]
            if latest["status"] == status:
                return latest
        time.sleep(0.5)
    raise RuntimeError(f"runner did not become {status}; latest={latest}")


def main() -> None:
    env = load_env(ROOT / ".env")
    admin_token = env["AI_ROUTER_ADMIN_TOKEN"]
    master_key = env["LITELLM_MASTER_KEY"]

    _, enrollment = request_json(
        "/control/v1/enrollment-tokens",
        token=admin_token,
        method="POST",
        data={"name": SMOKE_NAME, "expires_in_seconds": 300},
    )

    with tempfile.TemporaryDirectory(prefix="ai-router-smoke-") as temp_dir:
        command = [
            sys.executable,
            str(ROOT / "src/ai_router_control/runner.py"),
            "--control-url",
            BASE_URL,
            "--token",
            enrollment["token"],
            "--name",
            SMOKE_NAME,
            "--model-alias",
            f"local/{SMOKE_NAME}",
            "--vllm-url",
            "http://127.0.0.1:18000",
            "--state",
            str(Path(temp_dir) / "runner.json"),
        ]
        runner = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        try:
            online = wait_for_runner(admin_token, "online")
            if online.get("gateway_sync_error"):
                raise RuntimeError(f"LiteLLM registration failed: {online['gateway_sync_error']}")
            if not online.get("litellm_model_id"):
                raise RuntimeError("LiteLLM did not return a dynamic model id")

            _, completion = request_json(
                "/v1/chat/completions",
                token=master_key,
                method="POST",
                data={
                    "model": f"local/{SMOKE_NAME}",
                    "messages": [{"role": "user", "content": "Reply with the smoke status."}],
                    "max_tokens": 20,
                },
            )
            content = completion["choices"][0]["message"]["content"]
            if content != "ai-router relay is working":
                raise RuntimeError(f"unexpected completion: {content!r}")
            print("request relayed through edge -> LiteLLM -> control -> runner -> vLLM")
            print(
                f"online model={online['model_alias']} context={online['snapshot']['models'][0]['max_model_len']}"
            )
        finally:
            runner.terminate()
            try:
                runner.wait(timeout=10)
            except subprocess.TimeoutExpired:
                runner.kill()
                runner.wait(timeout=5)
            if runner.stdout:
                output = runner.stdout.read().strip()
                if output:
                    print(output)

        offline = wait_for_runner(admin_token, "offline")
        print(f"disconnect propagated: {offline['name']} is offline")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {error.code}: {detail}") from error
