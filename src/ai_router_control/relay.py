from __future__ import annotations

import asyncio
import base64
import contextlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


class RunnerUnavailable(RuntimeError):
    pass


class RunnerResponseError(RuntimeError):
    pass


@dataclass(slots=True)
class RelayRequest:
    request_id: str
    connection: "RunnerConnection"
    queue: asyncio.Queue[dict[str, Any]]
    start: dict[str, Any]

    async def chunks(self, idle_timeout: int):
        try:
            while True:
                message = await asyncio.wait_for(self.queue.get(), timeout=idle_timeout)
                message_type = message.get("type")
                if message_type == "response_chunk":
                    yield base64.b64decode(message.get("body", ""))
                elif message_type == "response_end":
                    break
                elif message_type == "response_error":
                    raise RunnerResponseError(message.get("message", "runner request failed"))
        finally:
            self.connection.pending.pop(self.request_id, None)
            with contextlib.suppress(Exception):
                await self.connection.send({"type": "cancel", "request_id": self.request_id})


@dataclass(slots=True)
class RunnerConnection:
    runner_id: str
    websocket: WebSocket
    pending: dict[str, asyncio.Queue[dict[str, Any]]] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, message: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(message)

    def deliver(self, message: dict[str, Any]) -> None:
        request_id = message.get("request_id")
        queue = self.pending.get(request_id)
        if queue is not None:
            queue.put_nowait(message)

    def fail_all(self, reason: str) -> None:
        for queue in tuple(self.pending.values()):
            queue.put_nowait({"type": "response_error", "message": reason})


class RelayManager:
    def __init__(self):
        self._connections: dict[str, RunnerConnection] = {}
        self._lock = asyncio.Lock()

    async def attach(self, runner_id: str, websocket: WebSocket) -> RunnerConnection:
        connection = RunnerConnection(runner_id=runner_id, websocket=websocket)
        async with self._lock:
            previous = self._connections.get(runner_id)
            self._connections[runner_id] = connection
        if previous is not None:
            previous.fail_all("runner opened a replacement connection")
            with contextlib.suppress(Exception):
                await previous.websocket.close(code=4001, reason="replaced by a newer connection")
        return connection

    async def detach(self, runner_id: str, connection: RunnerConnection) -> bool:
        async with self._lock:
            if self._connections.get(runner_id) is not connection:
                return False
            del self._connections[runner_id]
        connection.fail_all("runner disconnected")
        return True

    async def close(self, runner_id: str, code: int = 4003, reason: str = "runner revoked") -> None:
        async with self._lock:
            connection = self._connections.pop(runner_id, None)
        if connection:
            connection.fail_all(reason)
            with contextlib.suppress(Exception):
                await connection.websocket.close(code=code, reason=reason)

    def is_connected(self, runner_id: str) -> bool:
        return runner_id in self._connections

    def deliver(self, runner_id: str, message: dict[str, Any]) -> None:
        connection = self._connections.get(runner_id)
        if connection is not None:
            connection.deliver(message)

    async def begin(
        self,
        runner_id: str,
        *,
        method: str,
        path: str,
        query: str,
        headers: dict[str, str],
        body: bytes,
        start_timeout: int,
    ) -> RelayRequest:
        connection = self._connections.get(runner_id)
        if connection is None:
            raise RunnerUnavailable("runner is offline")
        request_id = uuid.uuid4().hex
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        connection.pending[request_id] = queue
        try:
            await connection.send(
                {
                    "type": "request",
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "query": query,
                    "headers": headers,
                    "body": base64.b64encode(body).decode("ascii"),
                }
            )
            start = await asyncio.wait_for(queue.get(), timeout=start_timeout)
        except Exception:
            connection.pending.pop(request_id, None)
            raise
        if start.get("type") == "response_error":
            connection.pending.pop(request_id, None)
            raise RunnerResponseError(start.get("message", "runner request failed"))
        if start.get("type") != "response_start":
            connection.pending.pop(request_id, None)
            raise RunnerResponseError("runner returned an invalid response")
        return RelayRequest(request_id=request_id, connection=connection, queue=queue, start=start)
