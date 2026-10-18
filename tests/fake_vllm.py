from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def send_json(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
        elif self.path == "/version":
            self.send_json({"version": "test-vllm"})
        elif self.path == "/v1/models":
            self.send_json(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "test/Qwen-Coder",
                            "owned_by": "vllm",
                            "max_model_len": 65536,
                        }
                    ],
                }
            )
        elif self.path == "/load":
            self.send_json({"server_load": 0})
        elif self.path == "/openapi.json":
            self.send_json(
                {
                    "paths": {
                        "/v1/chat/completions": {},
                        "/v1/responses": {},
                        "/v1/models": {},
                    }
                }
            )
        elif self.path == "/metrics":
            body = b"vllm:prompt_tokens_total{model_name=\"test\"} 120\nvllm:generation_tokens_total{model_name=\"test\"} 42\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        model = payload.get("model", "test/Qwen-Coder")
        self.send_json(
            {
                "id": "chatcmpl_ai_router_smoke",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "ai-router relay is working",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 5,
                    "total_tokens": 9,
                },
            }
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 18000), Handler).serve_forever()
