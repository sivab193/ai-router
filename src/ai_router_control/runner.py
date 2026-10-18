#!/usr/bin/env python3
"""Portable ai-router runner. Only the `websockets` package is required."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import http.client
import json
import os
import platform
import re
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import websockets


VERSION = "0.1.0"
METRIC_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>[-+0-9.eE]+)$"
)
METRICS = {
    "vllm:prompt_tokens_total": "prompt_tokens_total",
    "vllm:generation_tokens_total": "generation_tokens_total",
    "vllm:num_requests_running": "requests_running",
    "vllm:num_requests_waiting": "requests_waiting",
    "vllm:gpu_cache_usage_perc": "kv_cache_usage",
    "vllm:kv_cache_usage_perc": "kv_cache_usage",
}


def request(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 10,
) -> tuple[int, bytes, dict[str, str]]:
    headers = {"Accept": "application/json", "User-Agent": f"ai-router-runner/{VERSION}"}
    body = None
    if data is not None:
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    outgoing = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(outgoing, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {url} returned {error.code}: {detail[:500]}") from error


def get_json(base_url: str, path: str, api_key: str | None, timeout: float = 5) -> Any:
    _, body, _ = request(f"{base_url.rstrip('/')}{path}", token=api_key, timeout=timeout)
    return json.loads(body)


def get_text(base_url: str, path: str, api_key: str | None, timeout: float = 5) -> str:
    _, body, _ = request(f"{base_url.rstrip('/')}{path}", token=api_key, timeout=timeout)
    return body.decode("utf-8", "replace")


def discover_vllm(base_url: str, api_key: str | None, max_model_len: int | None) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "runtime": "vllm",
        "runner_version": VERSION,
        "observed_at": time.time(),
        "healthy": False,
        "models": [],
        "routes": [],
        "load": {},
        "metrics": {},
        "hardware": hardware_info(),
    }
    try:
        request(f"{base_url.rstrip('/')}/health", token=api_key, timeout=5)
        snapshot["healthy"] = True
    except Exception as error:
        snapshot["error"] = f"{type(error).__name__}: {error}"[:500]
        return snapshot
    with contextlib.suppress(Exception):
        snapshot["runtime_version"] = get_json(base_url, "/version", api_key).get("version")
    with contextlib.suppress(Exception):
        model_list = get_json(base_url, "/v1/models", api_key)
        models = model_list.get("data", []) if isinstance(model_list, dict) else []
        for model in models:
            if isinstance(model, dict):
                snapshot["models"].append(
                    {
                        "id": model.get("id"),
                        "owned_by": model.get("owned_by"),
                        "root": model.get("root"),
                        "max_model_len": model.get("max_model_len") or max_model_len,
                    }
                )
    with contextlib.suppress(Exception):
        spec = get_json(base_url, "/openapi.json", api_key)
        paths = spec.get("paths", {}) if isinstance(spec, dict) else {}
        snapshot["routes"] = sorted(path for path in paths if path.startswith("/v1/"))
    with contextlib.suppress(Exception):
        load = get_json(base_url, "/load", api_key)
        snapshot["load"] = load if isinstance(load, dict) else {}
    with contextlib.suppress(Exception):
        snapshot["metrics"] = parse_vllm_metrics(get_text(base_url, "/metrics", api_key))
    return snapshot


def parse_vllm_metrics(text: str) -> dict[str, float]:
    totals: dict[str, float] = {}
    for line in text.splitlines():
        match = METRIC_PATTERN.match(line.strip())
        if not match:
            continue
        public_name = METRICS.get(match.group("name"))
        if not public_name:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        totals[public_name] = totals.get(public_name, 0.0) + value
    return totals


def hardware_info() -> dict[str, Any]:
    result: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "architecture": platform.machine(),
    }
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        result["gpus"] = [
            {"name": parts[0], "memory_mib": int(parts[1]), "driver_version": parts[2]}
            for line in completed.stdout.splitlines()
            if len(parts := [item.strip() for item in line.split(",")]) >= 3
        ]
    except Exception:
        result["gpus"] = []
    return result


def enroll(args: argparse.Namespace, snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = {"name": args.name, "model_alias": args.model_alias, "snapshot": snapshot}
    _, body, _ = request(
        f"{args.control_url.rstrip('/')}/control/v1/runners/enroll",
        method="POST",
        data=payload,
        token=args.token,
        timeout=30,
    )
    state = json.loads(body)
    state["control_url"] = args.control_url.rstrip("/")
    save_state(args.state, state)
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    temporary.replace(path)


def load_state(path: Path, control_url: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    required = {"runner_id", "runner_token", "websocket_url"}
    if not required.issubset(state) or state.get("control_url") != control_url.rstrip("/"):
        return None
    return state


class Runner:
    def __init__(self, args: argparse.Namespace, state: dict[str, Any]):
        self.args = args
        self.state = state
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.send_lock = asyncio.Lock()
        self.websocket: Any = None

    async def run(self) -> None:
        retry = 1
        while True:
            try:
                async with websockets.connect(
                    self.state["websocket_url"],
                    additional_headers={"Authorization": f"Bearer {self.state['runner_token']}"},
                    max_size=16 * 1024 * 1024,
                    ping_interval=15,
                    ping_timeout=15,
                    open_timeout=30,
                ) as websocket:
                    self.websocket = websocket
                    retry = 1
                    print(
                        f"connected: {self.state['model_alias']} via {self.state['runner_id']}",
                        flush=True,
                    )
                    heartbeat = asyncio.create_task(self.heartbeat_loop())
                    try:
                        async for raw_message in websocket:
                            message = json.loads(raw_message)
                            message_type = message.get("type")
                            if message_type == "request":
                                task = asyncio.create_task(self.forward(message))
                                self.tasks[message["request_id"]] = task
                                task.add_done_callback(
                                    lambda _task, request_id=message["request_id"]: self.tasks.pop(
                                        request_id, None
                                    )
                                )
                            elif message_type == "cancel":
                                task = self.tasks.get(message.get("request_id"))
                                if task:
                                    task.cancel()
                    finally:
                        heartbeat.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await heartbeat
            except asyncio.CancelledError:
                raise
            except Exception as error:
                print(f"connection lost: {type(error).__name__}: {error}; retrying in {retry}s", flush=True)
                await asyncio.sleep(retry)
                retry = min(retry * 2, 30)

    async def heartbeat_loop(self) -> None:
        interval = int(self.state.get("heartbeat_interval_seconds", 10))
        while True:
            snapshot = await asyncio.to_thread(
                discover_vllm,
                self.args.vllm_url,
                self.args.vllm_api_key,
                self.args.max_model_len,
            )
            await self.send({"type": "heartbeat", "snapshot": snapshot})
            await asyncio.sleep(interval)

    async def send(self, message: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send(json.dumps(message, separators=(",", ":")))

    async def forward(self, message: dict[str, Any]) -> None:
        request_id = message["request_id"]
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        worker = asyncio.create_task(asyncio.to_thread(self._perform_request, message, events, loop))
        try:
            while True:
                event = await events.get()
                await self.send(event)
                if event["type"] in {"response_end", "response_error"}:
                    break
        except asyncio.CancelledError:
            return
        finally:
            with contextlib.suppress(Exception):
                await worker
            if not worker.done():
                worker.cancel()
        if worker.exception():
            await self.send(
                {
                    "type": "response_error",
                    "request_id": request_id,
                    "message": f"{type(worker.exception()).__name__}: {worker.exception()}",
                }
            )

    def _perform_request(
        self,
        message: dict[str, Any],
        events: asyncio.Queue[dict[str, Any]],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        request_id = message["request_id"]

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(events.put_nowait, event)

        try:
            base = urllib.parse.urlsplit(self.args.vllm_url)
            connection_type = http.client.HTTPSConnection if base.scheme == "https" else http.client.HTTPConnection
            connection = connection_type(base.hostname, base.port, timeout=self.args.request_timeout)
            path = f"{base.path.rstrip('/')}{message['path']}"
            if message.get("query"):
                path = f"{path}?{message['query']}"
            headers = dict(message.get("headers") or {})
            headers.pop("host", None)
            headers.pop("content-length", None)
            headers.pop("authorization", None)
            if self.args.vllm_api_key:
                headers["Authorization"] = f"Bearer {self.args.vllm_api_key}"
            body = base64.b64decode(message.get("body", ""))
            connection.request(message["method"], path, body=body, headers=headers)
            response = connection.getresponse()
            response_headers = {
                key: value
                for key, value in response.getheaders()
                if key.lower() not in {"connection", "content-length", "transfer-encoding"}
            }
            emit(
                {
                    "type": "response_start",
                    "request_id": request_id,
                    "status": response.status,
                    "headers": response_headers,
                }
            )
            while chunk := response.read1(64 * 1024):
                emit(
                    {
                        "type": "response_chunk",
                        "request_id": request_id,
                        "body": base64.b64encode(chunk).decode("ascii"),
                    }
                )
            emit({"type": "response_end", "request_id": request_id})
            connection.close()
        except Exception as error:
            emit(
                {
                    "type": "response_error",
                    "request_id": request_id,
                    "message": f"{type(error).__name__}: {error}"[:500],
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Connect a local vLLM server to ai-router")
    parser.add_argument("--control-url", default=os.getenv("AI_ROUTER_URL", "https://ai.siv19.dev"))
    parser.add_argument("--token", default=os.getenv("AI_ROUTER_ENROLLMENT_TOKEN"))
    parser.add_argument("--name", default=socket.gethostname())
    parser.add_argument("--model-alias")
    parser.add_argument("--vllm-url", default=os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--vllm-api-key", default=os.getenv("VLLM_API_KEY"))
    parser.add_argument("--max-model-len", type=int)
    parser.add_argument("--request-timeout", type=int, default=7200)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path(os.getenv("AI_ROUTER_RUNNER_STATE", "~/.config/ai-router/runner.json")).expanduser(),
    )
    parser.add_argument("--reset", action="store_true", help="discard saved runner identity and enroll again")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.reset and args.state.exists():
        args.state.unlink()
    snapshot = discover_vllm(args.vllm_url, args.vllm_api_key, args.max_model_len)
    if not snapshot.get("healthy"):
        raise SystemExit(f"vLLM is unavailable at {args.vllm_url}: {snapshot.get('error', 'health failed')}")
    if not snapshot.get("models"):
        raise SystemExit("vLLM /v1/models returned no model; wait for model loading to finish")
    state = load_state(args.state, args.control_url)
    if state is None:
        if not args.token:
            raise SystemExit("an enrollment --token is required the first time this runner connects")
        state = enroll(args, snapshot)
        print(f"enrolled: {state['runner_id']} as {state['model_alias']}", flush=True)
    try:
        asyncio.run(Runner(args, state).run())
    except KeyboardInterrupt:
        print("disconnected", flush=True)


if __name__ == "__main__":
    main()
