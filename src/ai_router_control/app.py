from __future__ import annotations

import asyncio
import hmac
import re
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse

from .config import Settings
from .gateway import LiteLLMGateway
from .relay import RelayManager, RunnerResponseError, RunnerUnavailable
from .schemas import EnrollmentTokenCreate, RunnerEnroll, RunnerHeartbeat, RunnerView
from .security import bearer_token, issue_token
from .storage import Store


HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "authorization",
}
RUNNER_PATH = re.compile(r"^(chat/completions|completions|responses|embeddings|models(?:/.*)?|tokenize|detokenize)$")
PACKAGE_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_env()
    store = Store(configured.database_path)
    relay = RelayManager()
    gateway = LiteLLMGateway(configured)
    app = FastAPI(title="ai-router control plane", version="0.1.0")
    app.state.settings = configured
    app.state.store = store
    app.state.relay = relay
    app.state.gateway = gateway

    def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
        token = bearer_token(authorization)
        if token is None or not hmac.compare_digest(token, configured.admin_token):
            raise HTTPException(status_code=401, detail="valid admin bearer token required")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return (PACKAGE_DIR / "static" / "dashboard.html").read_text(encoding="utf-8")

    @app.get("/install/runner.py", response_class=PlainTextResponse)
    async def runner_source() -> str:
        return (PACKAGE_DIR / "runner.py").read_text(encoding="utf-8")

    @app.get("/install/runner.sh", response_class=PlainTextResponse)
    async def runner_installer() -> str:
        source = (PACKAGE_DIR / "static" / "install-runner.sh").read_text(encoding="utf-8")
        return source.replace("__AI_ROUTER_URL__", configured.public_url)

    @app.get("/install/fonts/SpaceGrotesk-Variable.ttf", response_class=FileResponse)
    async def dashboard_font() -> Path:
        return PACKAGE_DIR / "static" / "fonts" / "SpaceGrotesk-Variable.ttf"

    @app.post("/control/v1/enrollment-tokens", dependencies=[Depends(require_admin)])
    async def create_enrollment(body: EnrollmentTokenCreate) -> dict[str, Any]:
        token = issue_token("aienr")
        expires_at = time.time() + body.expires_in_seconds
        store.create_enrollment(token, body.name, expires_at)
        command = (
            f"curl -fsSL {configured.public_url}/install/runner.sh | bash -s -- "
            f"--token {token} --name {shell_word(body.name)}"
        )
        return {"token": token, "expires_at": expires_at, "install_command": command}

    @app.post("/control/v1/runners/enroll")
    async def enroll_runner(
        body: RunnerEnroll, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, Any]:
        enrollment_token = bearer_token(authorization)
        if not enrollment_token or not store.consume_enrollment(enrollment_token):
            raise HTTPException(status_code=401, detail="invalid, expired, or already-used enrollment token")
        models = body.snapshot.get("models")
        if not isinstance(models, list) or not models or not isinstance(models[0], dict):
            raise HTTPException(status_code=422, detail="vLLM discovery returned no models")
        upstream_model = str(models[0].get("id") or "").strip()
        if not upstream_model:
            raise HTTPException(status_code=422, detail="vLLM model is missing an id")
        runner_id = f"run_{uuid.uuid4().hex[:20]}"
        runner_token = issue_token("airun")
        alias = body.model_alias or f"local/{slug(body.name)}"
        store.create_runner(
            runner_id=runner_id,
            name=body.name,
            model_alias=alias,
            upstream_model=upstream_model,
            runner_token=runner_token,
            snapshot=body.snapshot,
        )
        websocket_url = configured.public_url.replace("https://", "wss://").replace("http://", "ws://")
        return {
            "runner_id": runner_id,
            "runner_token": runner_token,
            "websocket_url": f"{websocket_url}/control/v1/runners/{runner_id}/connect",
            "heartbeat_interval_seconds": max(5, configured.heartbeat_ttl_seconds // 3),
            "model_alias": alias,
        }

    @app.get(
        "/control/v1/runners",
        response_model=list[RunnerView],
        dependencies=[Depends(require_admin)],
    )
    async def list_runners() -> list[dict[str, Any]]:
        result = []
        now = time.time()
        for runner in store.list_runners():
            if runner["revoked_at"] is not None:
                runner["status"] = "revoked"
            elif relay.is_connected(runner["id"]):
                runner["status"] = "online"
            elif (
                runner["status"] == "enrolled"
                and runner["last_seen_at"]
                and now - runner["last_seen_at"] <= configured.heartbeat_ttl_seconds
            ):
                runner["status"] = "connecting"
            else:
                runner["status"] = "offline"
            result.append(public_runner(runner))
        return result

    @app.post("/control/v1/runners/{runner_id}/revoke", dependencies=[Depends(require_admin)])
    async def revoke_runner(runner_id: str) -> dict[str, str]:
        runner = store.get_runner(runner_id)
        if runner is None:
            raise HTTPException(status_code=404, detail="runner not found")
        await relay.close(runner_id)
        await unregister_runner(store, gateway, runner)
        store.revoke(runner_id)
        return {"status": "revoked"}

    @app.websocket("/control/v1/runners/{runner_id}/connect")
    async def runner_connect(websocket: WebSocket, runner_id: str) -> None:
        token = bearer_token(websocket.headers.get("authorization"))
        runner = store.authenticate_runner(runner_id, token or "")
        if runner is None:
            await websocket.close(code=4401, reason="invalid runner token")
            return
        await websocket.accept()
        connection = await relay.attach(runner_id, websocket)
        proxy_token = issue_token("aiprx")
        store.mark_online(runner_id, proxy_token)
        await register_runner(store, gateway, store.get_runner(runner_id), proxy_token)
        try:
            while True:
                message = await websocket.receive_json()
                message_type = message.get("type")
                if message_type == "heartbeat":
                    heartbeat = RunnerHeartbeat.model_validate(message)
                    store.update_heartbeat(runner_id, heartbeat.snapshot)
                elif message_type in {
                    "response_start",
                    "response_chunk",
                    "response_end",
                    "response_error",
                }:
                    relay.deliver(runner_id, message)
        except WebSocketDisconnect:
            pass
        finally:
            detached = await relay.detach(runner_id, connection)
            if detached:
                current = store.get_runner(runner_id)
                if current:
                    await unregister_runner(store, gateway, current)
                store.mark_offline(runner_id)

    @app.api_route(
        "/runners/{runner_id}/v1/{path:path}",
        methods=["GET", "POST"],
    )
    async def proxy_runner(runner_id: str, path: str, request: Request) -> Response:
        if not RUNNER_PATH.fullmatch(path):
            raise HTTPException(status_code=404, detail="unsupported vLLM route")
        token = bearer_token(request.headers.get("authorization"))
        if not token or not store.authenticate_proxy(runner_id, token):
            raise HTTPException(status_code=401, detail="invalid internal runner proxy token")
        forwarded_headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP
        }
        try:
            relay_request = await relay.begin(
                runner_id,
                method=request.method,
                path=f"/v1/{path}",
                query=request.url.query,
                headers=forwarded_headers,
                body=await request.body(),
                start_timeout=configured.request_start_timeout_seconds,
            )
        except RunnerUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except (RunnerResponseError, TimeoutError, asyncio.TimeoutError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        response_headers = {
            key: value
            for key, value in relay_request.start.get("headers", {}).items()
            if key.lower() not in HOP_BY_HOP
        }
        return StreamingResponse(
            relay_request.chunks(configured.request_idle_timeout_seconds),
            status_code=int(relay_request.start.get("status", 502)),
            headers=response_headers,
            media_type=response_headers.get("content-type"),
        )

    return app


async def register_runner(
    store: Store,
    gateway: LiteLLMGateway,
    runner: dict[str, Any] | None,
    proxy_token: str,
) -> None:
    if runner is None:
        return
    try:
        model_id = await gateway.register(runner, proxy_token)
        store.set_gateway_sync(runner["id"], model_id, None)
    except Exception as error:  # keep relay alive when gateway is restarting
        store.set_gateway_sync(runner["id"], None, concise_error(error))


async def unregister_runner(store: Store, gateway: LiteLLMGateway, runner: dict[str, Any]) -> None:
    try:
        await gateway.unregister(runner.get("litellm_model_id"))
        store.set_gateway_sync(runner["id"], None, None)
    except Exception as error:
        store.set_gateway_sync(runner["id"], runner.get("litellm_model_id"), concise_error(error))


def public_runner(runner: dict[str, Any]) -> dict[str, Any]:
    allowed = set(RunnerView.model_fields)
    return {key: value for key, value in runner.items() if key in allowed}


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-._")
    return normalized[:64] or "runner"


def shell_word(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def concise_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:500]
