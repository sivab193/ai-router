from __future__ import annotations

import asyncio
import calendar
import hmac
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse

from .config import Settings
from .gateway import GatewayError, LiteLLMGateway
from .hub import (
    PRICING_SOURCE,
    TARGETS,
    deploy_alias,
    fit_check,
    gcp_create_command,
    gcp_delete_command,
    gpu_cost_for_day,
    instance_name,
    render_gcp_startup_script,
    render_kitfile,
    render_model_manifest,
    source_reachable_from_target,
    validate_model_id,
    validate_version,
)
from .profiles import (
    budget_alert_kinds,
    burn_rate,
    disallowed_models,
    migrate_key_tags,
    token_anomaly,
    validate_profile_id,
)
from .relay import RelayManager, RunnerResponseError, RunnerUnavailable
from .schemas import (
    DeploymentCreate,
    DeployQuoteRequest,
    EnrollmentTokenCreate,
    HubModelCreate,
    HubVersionCreate,
    MigrationRequest,
    ProfileCreate,
    ProfileKeyCreate,
    ProfileUpdate,
    RunnerEnroll,
    RunnerHeartbeat,
    RunnerView,
)
from .security import bearer_token, issue_token, token_hash
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
QUOTE_TTL_SECONDS = 900
HUB_ENROLLMENT_TTL_SECONDS = 7200


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_env()
    store = Store(configured.database_path)
    relay = RelayManager()
    gateway = LiteLLMGateway(configured)
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        task = None
        if configured.spend_sync_interval_seconds > 0:
            task = asyncio.create_task(scheduled_spend_sync())
            application.state.spend_sync_task = task
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="ai-router control plane", version="0.1.0", lifespan=lifespan)
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
        models = body.snapshot.get("models")
        if not isinstance(models, list) or not models or not isinstance(models[0], dict):
            raise HTTPException(status_code=422, detail="vLLM discovery returned no models")
        upstream_model = str(models[0].get("id") or "").strip()
        if not upstream_model:
            raise HTTPException(status_code=422, detail="vLLM model is missing an id")
        if not enrollment_token:
            raise HTTPException(status_code=401, detail="invalid, expired, or already-used enrollment token")
        enrollment = store.get_enrollment(enrollment_token)
        if enrollment is None:
            raise HTTPException(status_code=401, detail="invalid, expired, or already-used enrollment token")
        if body.name != enrollment["name"]:
            raise HTTPException(status_code=403, detail="enrollment token is bound to another runner name")
        enrollment = store.consume_enrollment_details(enrollment_token)
        if enrollment is None:
            raise HTTPException(status_code=401, detail="invalid, expired, or already-used enrollment token")
        runner_id = f"run_{uuid.uuid4().hex[:20]}"
        runner_token = issue_token("airun")
        alias = body.model_alias or f"local/{slug(body.name)}"
        if body.name.startswith("hub-"):
            # Hub runners serve the deployment's quoted alias, not a
            # name-derived one: the quote is what the profile allowlist
            # and the operator both expect.
            deployment = store.get_deployment(body.name[4:])
            if deployment is not None:
                alias = deployment["alias"]
        store.create_runner(
            runner_id=runner_id,
            name=body.name,
            model_alias=alias,
            upstream_model=upstream_model,
            runner_token=runner_token,
            snapshot=body.snapshot,
        )
        if body.name.startswith("hub-"):
            await link_hub_deployment(store, gateway, body.name[4:], runner_id, alias)
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

    # -- Profiles (Phase 5) -------------------------------------------------

    def profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC)
        month = now.strftime("%Y-%m")
        provider_spend, gpu_cost = store.month_spend(profile["id"], month)
        budget = profile.get("monthly_budget_usd")
        gpu_budget = profile.get("gpu_budget_usd")
        total = provider_spend + gpu_cost
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        daily = store.get_daily_usage(profile["id"], limit=31)
        daily_spend = [
            (row["day"], row["provider_spend_usd"] + row["gpu_cost_usd"]) for row in daily
        ]
        rate, projection = burn_rate(daily_spend, now.day, days_in_month)
        return {
            **profile,
            "month": month,
            "month_provider_spend_usd": provider_spend,
            "month_gpu_cost_usd": gpu_cost,
            "month_spend_usd": total,
            "daily_burn_rate_usd": rate,
            "projected_month_spend_usd": projection,
            "budget_remaining_usd": (budget - total) if budget is not None else None,
            "gpu_budget_remaining_usd": (gpu_budget - gpu_cost) if gpu_budget is not None else None,
            "team_synced": bool(profile.get("litellm_team_id")),
            "unacknowledged_alerts": store.list_alerts(profile["id"], unacknowledged_only=True),
            "key_count": sum(
                item.get("revoked_at") is None for item in store.list_profile_keys(profile["id"])
            ),
        }

    @app.get("/control/v1/profiles", dependencies=[Depends(require_admin)])
    async def list_profiles() -> list[dict[str, Any]]:
        return [profile_summary(profile) for profile in store.list_profiles()]

    @app.post("/control/v1/profiles", dependencies=[Depends(require_admin)], status_code=201)
    async def create_profile(body: ProfileCreate) -> dict[str, Any]:
        try:
            profile_id = validate_profile_id(body.id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if store.get_profile(profile_id):
            raise HTTPException(status_code=409, detail="profile already exists")
        data = body.model_dump()
        data["id"] = profile_id
        return profile_summary(store.create_profile(data))

    @app.get("/control/v1/profiles/{profile_id}", dependencies=[Depends(require_admin)])
    async def get_profile(profile_id: str) -> dict[str, Any]:
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        summary = profile_summary(profile)
        summary["daily_usage"] = store.get_daily_usage(profile_id)
        summary["keys"] = [public_profile_key(item) for item in store.list_profile_keys(profile_id)]
        return summary

    @app.patch("/control/v1/profiles/{profile_id}", dependencies=[Depends(require_admin)])
    async def update_profile(profile_id: str, body: ProfileUpdate) -> dict[str, Any]:
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        updates = {key: value for key, value in body.model_dump().items() if value is not None}
        updated = store.update_profile(profile_id, updates)
        assert updated is not None
        result = profile_summary(updated)
        if gateway.enabled and updated.get("litellm_team_id"):
            try:
                await gateway.sync_team(updated)
            except GatewayError as error:
                # Local profile is the source of truth; the team re-syncs
                # on the next explicit sync. Surface the failure, don't hide it.
                result["team_sync_error"] = concise_error(error)
        return result

    @app.post(
        "/control/v1/profiles/{profile_id}/keys",
        dependencies=[Depends(require_admin)],
        status_code=201,
    )
    async def create_profile_key(profile_id: str, body: ProfileKeyCreate) -> dict[str, Any]:
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        models = body.models if body.models is not None else profile["allowed_models"]
        disallowed = disallowed_models(profile["allowed_models"], models)
        if disallowed:
            raise HTTPException(
                status_code=422,
                detail=f"models outside the profile allowlist: {', '.join(disallowed)}",
            )
        budget = profile.get("monthly_budget_usd")
        if budget is not None:
            provider_spend, gpu_cost = store.month_spend(profile_id, datetime.now(UTC).strftime("%Y-%m"))
            if provider_spend + gpu_cost >= budget:
                raise HTTPException(status_code=409, detail="profile monthly budget is exhausted")
        if not gateway.enabled:
            raise HTTPException(status_code=503, detail="LiteLLM gateway is not configured")
        team_id = profile.get("litellm_team_id")
        if not team_id:
            try:
                team_id = await gateway.sync_team(profile)
            except GatewayError as error:
                raise HTTPException(status_code=502, detail=f"LiteLLM team sync failed: {error}") from error
            store.set_profile_team(profile_id, team_id)
        try:
            raw_key = await gateway.create_profile_key(
                team_id=team_id,
                key_alias=body.key_alias,
                models=models,
                max_budget=body.max_budget,
                tpm_limit=profile.get("tpm_limit"),
                rpm_limit=profile.get("rpm_limit"),
                tags=[f"profile:{profile_id}"],
            )
        except GatewayError as error:
            raise HTTPException(status_code=502, detail=f"LiteLLM key creation failed: {error}") from error
        store.add_profile_key(raw_key, profile_id, body.key_alias)
        return {
            "key": raw_key,
            "key_alias": body.key_alias,
            "profile_id": profile_id,
            "team_id": team_id,
            "models": models,
            "note": "shown once; the control plane stores only a hash of the key",
        }

    @app.delete(
        "/control/v1/profiles/{profile_id}/keys/{key_id}",
        dependencies=[Depends(require_admin)],
    )
    async def revoke_profile_key(profile_id: str, key_id: str) -> dict[str, Any]:
        key = store.get_profile_key_by_hash(key_id)
        if key is None or key["profile_id"] != profile_id:
            raise HTTPException(status_code=404, detail="profile key not found")
        if key.get("revoked_at") is not None:
            raise HTTPException(status_code=409, detail="profile key is already revoked")
        if not gateway.enabled:
            raise HTTPException(status_code=503, detail="LiteLLM gateway is not configured")
        try:
            await gateway.delete_key(key_id)
        except GatewayError as error:
            raise HTTPException(status_code=502, detail=f"LiteLLM key revocation failed: {error}") from error
        store.revoke_profile_key(key_id)
        updated = store.get_profile_key_by_hash(key_id)
        assert updated is not None
        return public_profile_key(updated)

    @app.post("/control/v1/profiles/{profile_id}/sync", dependencies=[Depends(require_admin)])
    async def sync_profile(profile_id: str) -> dict[str, Any]:
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        result: dict[str, Any] = {
            "profile_id": profile_id,
            "team_synced": False,
            "spend_synced": False,
            "alerts_fired": [],
        }
        team_id = profile.get("litellm_team_id")
        if not gateway.enabled:
            result["gateway"] = "disabled"
        else:
            try:
                team_id = await gateway.sync_team(profile)
                store.set_profile_team(profile_id, team_id)
                result["team_synced"] = True
                result["team_id"] = team_id
            except GatewayError as error:
                result["team_sync_error"] = concise_error(error)
            if team_id:
                today = datetime.now(UTC).date()
                start = (today - timedelta(days=29)).isoformat()
                rows = await gateway.get_team_daily_spend(team_id, start, today.isoformat())
                if rows is not None:
                    for row in rows:
                        store.record_daily_usage(
                            profile_id,
                            row["day"],
                            provider_spend_usd=row["spend"],
                            gpu_cost_usd=0.0,
                            prompt_tokens=row.get("prompt_tokens"),
                            completion_tokens=row.get("completion_tokens"),
                            requests=row["requests"],
                            source="litellm-daily",
                        )
                    result["spend_synced"] = True
                    result["days_synced"] = len(rows)
        # GPU cost attribution for today (separate ledger, never mixed in
        # with provider spend; see docs/PROFILES_AND_HUB.md).
        deployments = store.list_deployments()
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        gpu_today = gpu_cost_for_day(
            deployments, profile_id, today_start.timestamp(), time.time()
        )
        today_str = today_start.strftime("%Y-%m-%d")
        existing_today = next(
            (row for row in store.get_daily_usage(profile_id, limit=1) if row["day"] == today_str),
            None,
        )
        if existing_today is not None:
            store.record_daily_usage(
                profile_id,
                today_str,
                provider_spend_usd=existing_today["provider_spend_usd"],
                gpu_cost_usd=gpu_today,
                prompt_tokens=existing_today.get("prompt_tokens"),
                completion_tokens=existing_today.get("completion_tokens"),
                requests=existing_today["requests"],
                source=existing_today["source"],
            )
        elif gpu_today > 0:
            store.record_daily_usage(
                profile_id,
                today_str,
                provider_spend_usd=0.0,
                gpu_cost_usd=gpu_today,
                prompt_tokens=None,
                completion_tokens=None,
                requests=0,
                source="hub-gpu",
            )
        result["gpu_cost_today_usd"] = gpu_today
        month = datetime.now(UTC).strftime("%Y-%m")
        provider_spend, gpu_cost = store.month_spend(profile_id, month)
        fired: list[str] = []
        for kind in budget_alert_kinds(profile.get("monthly_budget_usd"), provider_spend + gpu_cost):
            if store.record_alert(profile_id, kind, month, provider_spend + gpu_cost):
                fired.append(kind)
        daily = store.get_daily_usage(profile_id, limit=8)
        if len(daily) >= 2:
            series = [
                (
                    row["day"],
                    (row.get("prompt_tokens") or 0) + (row.get("completion_tokens") or 0),
                )
                for row in reversed(daily)
            ]
            is_anomaly, _multiple = token_anomaly(series)
            if is_anomaly and store.record_alert(
                profile_id, "token_anomaly", series[-1][0], float(series[-1][1])
            ):
                fired.append("token_anomaly")
        result["alerts_fired"] = fired
        result["summary"] = profile_summary(store.get_profile(profile_id))
        return result

    async def scheduled_spend_sync() -> None:
        interval = configured.spend_sync_interval_seconds
        while interval > 0:
            await asyncio.sleep(interval)
            for profile in store.list_profiles():
                try:
                    await sync_profile(profile["id"])
                except Exception:
                    # One unavailable gateway or malformed response must not
                    # stop later profiles or future sync cycles.
                    continue

    @app.post("/control/v1/profiles/migrate-keys", dependencies=[Depends(require_admin)])
    async def migrate_keys(body: MigrationRequest) -> dict[str, Any]:
        if body.discover:
            if not gateway.enabled:
                raise HTTPException(status_code=503, detail="LiteLLM gateway is not configured")
            discovered = await gateway.list_keys()
            if discovered is None:
                raise HTTPException(status_code=502, detail="LiteLLM /key/list is unavailable")
            keys = [
                {
                    "key_alias": str(item.get("key_alias") or item.get("key_name") or "unnamed"),
                    "key_hash": item.get("token") or item.get("key_hash"),
                    "tags": item.get("tags") or [],
                }
                for item in discovered
            ]
        elif body.keys is not None:
            keys = [item.model_dump() for item in body.keys]
        else:
            raise HTTPException(status_code=422, detail="supply keys or set discover=true")
        migrated, skipped, flagged = [], [], []
        for entry in keys:
            alias = entry["key_alias"]
            tags = entry.get("tags") or []
            profile_id, unmatched = migrate_key_tags(tags, alias)
            note = (
                "no profile tag matched; flagged for review"
                if unmatched
                else f"matched {profile_id} from tags/alias"
            )
            key_hash = entry.get("key_hash")
            raw_key = entry.get("key")
            if key_hash is None and raw_key is not None:
                key_hash = token_hash(raw_key)
            if key_hash is None:
                skipped.append({"key_alias": alias, "reason": "no key or key_hash supplied"})
                continue
            existing = store.get_profile_key_by_hash(key_hash)
            if existing is not None and not body.force:
                skipped.append({"key_alias": alias, "reason": f"already mapped to {existing['profile_id']}"})
                continue
            store.add_profile_key_by_hash(key_hash, profile_id, alias, migrated=True, migration_note=note)
            record = {"key_alias": alias, "profile_id": profile_id, "note": note}
            migrated.append(record)
            if unmatched:
                flagged.append(record)
        return {"migrated": migrated, "skipped": skipped, "flagged_for_review": flagged}

    @app.post(
        "/control/v1/profiles/{profile_id}/alerts/{alert_id}/acknowledge",
        dependencies=[Depends(require_admin)],
    )
    async def acknowledge_profile_alert(profile_id: str, alert_id: int) -> dict[str, str]:
        if store.get_profile(profile_id) is None:
            raise HTTPException(status_code=404, detail="profile not found")
        if not store.acknowledge_alert(alert_id):
            raise HTTPException(status_code=404, detail="alert not found or already acknowledged")
        return {"status": "acknowledged"}

    # -- Model hub (Phase 6) ------------------------------------------------

    def resolve_hub_version(
        model_id: str, version: str | None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        model = store.get_hub_model(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="model not found")
        resolved = (
            store.get_hub_version(model_id, version)
            if version
            else store.get_latest_version(model_id)
        )
        if resolved is None:
            raise HTTPException(status_code=404, detail="version not found")
        return model, resolved

    @app.get("/control/v1/hub/targets", dependencies=[Depends(require_admin)])
    async def list_targets() -> list[dict[str, Any]]:
        return [asdict(target) for target in TARGETS.values()]

    @app.get("/control/v1/hub/models", dependencies=[Depends(require_admin)])
    async def list_hub_models() -> list[dict[str, Any]]:
        result = []
        for model in store.list_hub_models():
            versions = store.list_hub_versions(model["id"])
            result.append(
                {
                    **model,
                    "latest_version": store.get_latest_version(model["id"]),
                    "version_count": len(versions),
                }
            )
        return result

    @app.post("/control/v1/hub/models", dependencies=[Depends(require_admin)], status_code=201)
    async def create_hub_model(body: HubModelCreate) -> dict[str, Any]:
        try:
            model_id = validate_model_id(body.id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if store.get_hub_model(model_id):
            raise HTTPException(status_code=409, detail="model already exists")
        data = body.model_dump()
        data["id"] = model_id
        return store.create_hub_model(data)

    @app.get("/control/v1/hub/models/{model_id}", dependencies=[Depends(require_admin)])
    async def get_hub_model(model_id: str) -> dict[str, Any]:
        model = store.get_hub_model(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="model not found")
        return {**model, "versions": store.list_hub_versions(model_id)}

    @app.post(
        "/control/v1/hub/models/{model_id}/versions",
        dependencies=[Depends(require_admin)],
        status_code=201,
    )
    async def add_hub_version(model_id: str, body: HubVersionCreate) -> dict[str, Any]:
        if store.get_hub_model(model_id) is None:
            raise HTTPException(status_code=404, detail="model not found")
        try:
            version = validate_version(body.version)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if store.get_hub_version(model_id, version):
            raise HTTPException(status_code=409, detail="version already exists; versions are immutable")
        data = body.model_dump()
        data["version"] = version
        return store.add_hub_version(model_id, data)

    @app.get(
        "/control/v1/hub/models/{model_id}/versions/{version}/kitfile",
        dependencies=[Depends(require_admin)],
        response_class=PlainTextResponse,
    )
    async def get_kitfile(model_id: str, version: str) -> str:
        model, resolved = resolve_hub_version(model_id, version)
        return render_kitfile(model, resolved)

    @app.get("/control/v1/hub/models/{model_id}/versions/{version}/manifest", dependencies=[Depends(require_admin)])
    async def get_manifest(model_id: str, version: str) -> dict[str, Any]:
        model, resolved = resolve_hub_version(model_id, version)
        return render_model_manifest(model, resolved)

    @app.post("/control/v1/hub/deployments/quote", dependencies=[Depends(require_admin)])
    async def quote_deployment(body: DeployQuoteRequest) -> dict[str, Any]:
        model, version = resolve_hub_version(body.model_id, body.version)
        target = TARGETS.get(body.target)
        if target is None:
            raise HTTPException(status_code=422, detail=f"unknown deploy target: {body.target}")
        profile = store.get_profile(body.profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        fit, fit_reason = fit_check(version.get("min_vram_mib"), target)
        if fit != "refused":
            reachable, reach_reason = source_reachable_from_target(
                model["source_type"], version.get("modelkit_ref"), target.id
            )
            if not reachable:
                fit, fit_reason = "refused", reach_reason
        month = datetime.now(UTC).strftime("%Y-%m")
        provider_spend, gpu_cost = store.month_spend(body.profile_id, month)
        budget = profile.get("monthly_budget_usd")
        remaining = (budget - provider_spend - gpu_cost) if budget is not None else None
        gpu_budget = profile.get("gpu_budget_usd")
        gpu_remaining = (gpu_budget - gpu_cost) if gpu_budget is not None else None
        if gpu_remaining is not None and gpu_remaining <= 0 and target.hourly_cost_usd > 0:
            fit, fit_reason = "refused", "profile GPU budget is exhausted"
        quote = store.create_quote(
            {
                "id": f"quote_{uuid.uuid4().hex[:16]}",
                "model_id": model["id"],
                "version": version["version"],
                "target": target.id,
                "profile_id": profile["id"],
                "hourly_cost_usd": target.hourly_cost_usd,
                "fit": fit,
                "fit_reason": fit_reason,
                "budget_remaining_usd": remaining,
                "expires_at": time.time() + QUOTE_TTL_SECONDS,
            }
        )
        return {
            **quote,
            "model_display_name": model["display_name"],
            "alias": deploy_alias(model["id"], version["version"]),
            "target_label": target.name,
            "gpu": target.gpu,
            "vram_mib": target.vram_mib,
            "pricing_source": PRICING_SOURCE,
            "target_notes": target.notes,
            "provision": target.provision,
            "month_spend_usd": provider_spend + gpu_cost,
            "month_gpu_cost_usd": gpu_cost,
            "gpu_budget_remaining_usd": gpu_remaining,
        }

    @app.post("/control/v1/hub/deployments", dependencies=[Depends(require_admin)], status_code=201)
    async def create_deployment(body: DeploymentCreate) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(
                status_code=422, detail="confirm=true is required: review the quote first"
            )
        quote = store.get_quote(body.quote_id)
        if quote is None:
            raise HTTPException(status_code=404, detail="quote not found")
        if quote["fit"] == "refused":
            raise HTTPException(status_code=422, detail=f"deployment refused: {quote['fit_reason']}")
        if quote["fit"] == "unknown" and not body.allow_unknown_fit:
            raise HTTPException(
                status_code=422,
                detail="model VRAM is unknown for this target; set allow_unknown_fit=true to proceed anyway",
            )
        profile = store.get_profile(quote["profile_id"])
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        budget = profile.get("monthly_budget_usd")
        if budget is not None:
            month = datetime.now(UTC).strftime("%Y-%m")
            provider_spend, gpu_cost = store.month_spend(profile["id"], month)
            if provider_spend + gpu_cost >= budget:
                raise HTTPException(status_code=409, detail="profile monthly budget is exhausted")
        gpu_budget = profile.get("gpu_budget_usd")
        if gpu_budget is not None:
            month = datetime.now(UTC).strftime("%Y-%m")
            _provider_spend, gpu_cost = store.month_spend(profile["id"], month)
            if gpu_cost >= gpu_budget:
                raise HTTPException(status_code=409, detail="profile GPU budget is exhausted")
        if not store.consume_quote(body.quote_id):
            raise HTTPException(status_code=410, detail="quote expired or already used")
        model, version = resolve_hub_version(quote["model_id"], quote["version"])
        target = TARGETS[quote["target"]]
        deployment_id = f"dep_{uuid.uuid4().hex[:16]}"
        alias = body.alias or deploy_alias(model["id"], version["version"])
        runner_name = f"hub-{deployment_id}"
        # Single-use enrollment token with a 2h window: the VM must download
        # the model before it can enroll, and large models take a while.
        token = issue_token("aienr")
        store.create_enrollment(token, runner_name, time.time() + HUB_ENROLLMENT_TTL_SECONDS)
        startup_script = render_gcp_startup_script(
            public_url=configured.public_url,
            runner_name=runner_name,
            model_source=model["source"],
            served_name=alias,
            vllm_image=configured.vllm_image,
            max_model_len=version.get("context_length"),
        )
        deployment = store.create_deployment(
            {
                "id": deployment_id,
                "model_id": model["id"],
                "version": version["version"],
                "target": target.id,
                "profile_id": profile["id"],
                "alias": alias,
                "hourly_cost_usd": target.hourly_cost_usd,
                "status": "awaiting_launch",
            }
        )
        response: dict[str, Any] = {
            **deployment,
            "runner_name": runner_name,
            "startup_script": startup_script,
        }
        if target.provision == "gcloud":
            instance = instance_name(deployment_id)
            command = gcp_create_command(
                instance_name=instance,
                zone=configured.gcp_zone,
                target=target,
                project=configured.gcp_project,
                startup_script_path=f"/tmp/{instance}-startup.sh",
                enrollment_token=token,
            )
            response["instance_name"] = instance
            response["provision_command"] = " ".join(shlex.quote(part) for part in command)
            if configured.gcp_auto_provision and configured.gcp_project and shutil.which("gcloud"):
                script_path = Path(tempfile.gettempdir()) / f"{instance}-startup.sh"
                script_path.write_text(startup_script, encoding="utf-8")
                auto_command = gcp_create_command(
                    instance_name=instance,
                    zone=configured.gcp_zone,
                    target=target,
                    project=configured.gcp_project,
                    startup_script_path=str(script_path),
                    enrollment_token=token,
                )
                result = await asyncio.to_thread(
                    lambda: subprocess.run(auto_command, capture_output=True, text=True, timeout=300)
                )
                if result.returncode == 0:
                    store.update_deployment(
                        deployment_id,
                        {
                            "status": "provisioning",
                            "instance_id": instance,
                            "provision_command": " ".join(shlex.quote(part) for part in auto_command),
                        },
                    )
                    response["status"] = "provisioning"
                    response["instance_id"] = instance
                    response["next_step"] = "VM is provisioning; it enrolls itself on boot"
                else:
                    error = (result.stderr or result.stdout or "gcloud failed")[:500]
                    store.update_deployment(deployment_id, {"status": "failed", "error": error})
                    response["status"] = "failed"
                    response["error"] = error
                    response["next_step"] = "gcloud failed; fix the error and re-quote to retry"
            else:
                store.update_deployment(
                    deployment_id, {"provision_command": response["provision_command"]}
                )
                response["next_step"] = (
                    "save startup_script to the path in provision_command, then run "
                    "provision_command; the VM enrolls itself on boot"
                )
        else:
            response["install_command"] = (
                f"curl -fsSL {configured.public_url}/install/runner.sh | bash -s -- "
                f"--token {token} --name {shell_word(runner_name)}"
            )
            response["next_step"] = (
                f"start vLLM serving {model['source']} on the {target.name} target, "
                "then run install_command on it"
            )
        return response

    @app.get("/control/v1/hub/deployments", dependencies=[Depends(require_admin)])
    async def list_deployments() -> list[dict[str, Any]]:
        return store.list_deployments()

    @app.get("/control/v1/hub/deployments/{deployment_id}", dependencies=[Depends(require_admin)])
    async def get_deployment(deployment_id: str) -> dict[str, Any]:
        deployment = store.get_deployment(deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="deployment not found")
        return deployment

    @app.post(
        "/control/v1/hub/deployments/{deployment_id}/launched",
        dependencies=[Depends(require_admin)],
    )
    async def mark_launched(
        deployment_id: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        deployment = store.get_deployment(deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="deployment not found")
        if deployment["status"] != "awaiting_launch":
            raise HTTPException(
                status_code=409,
                detail=f"deployment is {deployment['status']}, not awaiting_launch",
            )
        fields: dict[str, Any] = {"status": "provisioning"}
        instance_id = (body or {}).get("instance_id")
        if instance_id:
            fields["instance_id"] = instance_id
        updated = store.update_deployment(deployment_id, fields)
        assert updated is not None
        return updated

    @app.post(
        "/control/v1/hub/deployments/{deployment_id}/teardown",
        dependencies=[Depends(require_admin)],
    )
    async def teardown_deployment(deployment_id: str) -> dict[str, Any]:
        deployment = store.get_deployment(deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="deployment not found")
        if deployment["status"] == "torn_down":
            raise HTTPException(status_code=409, detail="deployment is already torn down")
        # Routing dies first: close the relay, unregister the LiteLLM model,
        # revoke the runner. No request can reach the instance after this.
        runner_removed = False
        runner_id = deployment.get("runner_id")
        if runner_id:
            runner = store.get_runner(runner_id)
            if runner is not None:
                await relay.close(runner_id)
                await unregister_runner(store, gateway, runner)
                store.revoke(runner_id)
                runner_removed = True
        instance_delete: dict[str, Any] = {"attempted": False}
        target = TARGETS.get(deployment["target"])
        instance_id = deployment.get("instance_id")
        if target is not None and target.provision == "gcloud" and instance_id:
            command = gcp_delete_command(instance_id, configured.gcp_zone, configured.gcp_project)
            instance_delete["command"] = " ".join(shlex.quote(part) for part in command)
            if configured.gcp_auto_provision and configured.gcp_project and shutil.which("gcloud"):
                instance_delete["attempted"] = True
                result = await asyncio.to_thread(
                    lambda: subprocess.run(command, capture_output=True, text=True, timeout=300)
                )
                instance_delete["ok"] = result.returncode == 0
                if result.returncode != 0:
                    instance_delete["error"] = (result.stderr or result.stdout or "gcloud delete failed")[:500]
            else:
                instance_delete["ok"] = None
                instance_delete["note"] = "auto-provision is off: run the command to stop billing"
        updated = store.update_deployment(
            deployment_id,
            {
                "status": "torn_down",
                "torn_down_at": time.time(),
                "error": instance_delete.get("error") if instance_delete.get("ok") is False else None,
            },
        )
        assert updated is not None
        return {
            **updated,
            "runner_removed": runner_removed,
            "instance_delete": instance_delete,
            "routing_removed": True,
        }

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
        if runner["name"].startswith("hub-"):
            deployment = store.get_deployment(runner["name"][4:])
            if deployment is not None and deployment["status"] in (
                "enrolled",
                "provisioning",
                "awaiting_launch",
            ):
                store.update_deployment(deployment["id"], {"status": "running"})
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


async def link_hub_deployment(
    store: Store,
    gateway: LiteLLMGateway,
    deployment_id: str,
    runner_id: str,
    alias: str,
) -> None:
    """Link an enrolled hub runner to its deployment and open the profile.

    The deployment's alias is added to the owning profile's allowlist so
    keys scoped to that profile can use the new model. A failed team sync
    is non-fatal: the allowlist is stored locally and the team re-syncs
    on the next explicit profile sync.
    """
    deployment = store.get_deployment(deployment_id)
    if deployment is None:
        return
    store.update_deployment(deployment_id, {"runner_id": runner_id, "status": "enrolled"})
    profile = store.get_profile(deployment["profile_id"])
    if profile is None or alias in profile["allowed_models"]:
        return
    updated = store.update_profile(
        profile["id"], {"allowed_models": [*profile["allowed_models"], alias]}
    )
    if updated is not None and gateway.enabled and updated.get("litellm_team_id"):
        try:
            await gateway.sync_team(updated)
        except Exception:
            pass


def public_runner(runner: dict[str, Any]) -> dict[str, Any]:
    allowed = set(RunnerView.model_fields)
    return {key: value for key, value in runner.items() if key in allowed}


def public_profile_key(key: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": key["key_hash"],
        "profile_id": key["profile_id"],
        "key_alias": key["key_alias"],
        "migrated": bool(key.get("migrated")),
        "migration_note": key.get("migration_note"),
        "created_at": key["created_at"],
        "revoked_at": key.get("revoked_at"),
        "status": "revoked" if key.get("revoked_at") is not None else "active",
    }


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-._")
    return normalized[:64] or "runner"


def shell_word(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def concise_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:500]
