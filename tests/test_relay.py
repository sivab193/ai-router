from __future__ import annotations

import asyncio
import base64

from ai_router_control.relay import RelayManager


class FakeWebSocket:
    def __init__(self):
        self.sent: asyncio.Queue[dict] = asyncio.Queue()
        self.closed = False

    async def send_json(self, message):
        await self.sent.put(message)

    async def close(self, code=1000, reason=""):
        self.closed = True


def test_request_and_stream_response_through_relay():
    async def exercise():
        manager = RelayManager()
        websocket = FakeWebSocket()
        connection = await manager.attach("run_1", websocket)
        begin = asyncio.create_task(
            manager.begin(
                "run_1",
                method="POST",
                path="/v1/chat/completions",
                query="",
                headers={"content-type": "application/json"},
                body=b'{"model":"qwen"}',
                start_timeout=1,
            )
        )
        outgoing = await asyncio.wait_for(websocket.sent.get(), timeout=1)
        assert outgoing["type"] == "request"
        assert base64.b64decode(outgoing["body"]) == b'{"model":"qwen"}'

        manager.deliver(
            "run_1",
            {
                "type": "response_start",
                "request_id": outgoing["request_id"],
                "status": 200,
                "headers": {"content-type": "text/event-stream"},
            },
        )
        relayed = await begin
        manager.deliver(
            "run_1",
            {
                "type": "response_chunk",
                "request_id": outgoing["request_id"],
                "body": base64.b64encode(b"data: one\n\n").decode(),
            },
        )
        manager.deliver(
            "run_1", {"type": "response_end", "request_id": outgoing["request_id"]}
        )
        chunks = [chunk async for chunk in relayed.chunks(idle_timeout=1)]
        assert chunks == [b"data: one\n\n"]
        assert await manager.detach("run_1", connection) is True

    asyncio.run(exercise())
