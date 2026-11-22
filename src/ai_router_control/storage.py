from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .hub import SEED_MODELS
from .profiles import STARTER_PROFILES
from .security import token_hash, token_matches


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS enrollment_tokens (
                    token_hash TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    used_at REAL
                );

                CREATE TABLE IF NOT EXISTS runners (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    model_alias TEXT NOT NULL,
                    upstream_model TEXT NOT NULL,
                    runner_token_hash TEXT NOT NULL,
                    proxy_token_hash TEXT,
                    status TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_seen_at REAL,
                    disconnected_at REAL,
                    revoked_at REAL,
                    litellm_model_id TEXT,
                    gateway_sync_error TEXT
                );

                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    monthly_budget_usd REAL,
                    gpu_budget_usd REAL,
                    rpm_limit INTEGER,
                    tpm_limit INTEGER,
                    monthly_token_ceiling INTEGER,
                    allowed_models_json TEXT NOT NULL DEFAULT '[]',
                    litellm_team_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profile_keys (
                    key_hash TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES profiles(id),
                    key_alias TEXT NOT NULL,
                    migrated INTEGER NOT NULL DEFAULT 0,
                    migration_note TEXT,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profile_usage_daily (
                    profile_id TEXT NOT NULL REFERENCES profiles(id),
                    day TEXT NOT NULL,
                    provider_spend_usd REAL NOT NULL DEFAULT 0,
                    gpu_cost_usd REAL NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    requests INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    PRIMARY KEY (profile_id, day)
                );

                CREATE TABLE IF NOT EXISTS profile_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL REFERENCES profiles(id),
                    kind TEXT NOT NULL,
                    period TEXT NOT NULL,
                    value REAL NOT NULL,
                    fired_at REAL NOT NULL,
                    acknowledged_at REAL,
                    UNIQUE (profile_id, kind, period)
                );

                CREATE TABLE IF NOT EXISTS hub_models (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    license TEXT,
                    format TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hub_versions (
                    model_id TEXT NOT NULL REFERENCES hub_models(id),
                    version TEXT NOT NULL,
                    source_revision TEXT,
                    quantization TEXT,
                    size_bytes INTEGER,
                    context_length INTEGER,
                    min_vram_mib INTEGER,
                    runtime TEXT NOT NULL,
                    sha256 TEXT,
                    modelkit_ref TEXT,
                    locations_json TEXT NOT NULL DEFAULT '[]',
                    is_latest INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (model_id, version)
                );

                CREATE TRIGGER IF NOT EXISTS hub_versions_immutable
                BEFORE UPDATE OF source_revision, quantization, size_bytes,
                    context_length, min_vram_mib, runtime, sha256,
                    modelkit_ref, locations_json, created_at
                ON hub_versions
                BEGIN
                    SELECT RAISE(ABORT, 'hub_versions rows are immutable; add a new version instead');
                END;

                CREATE TABLE IF NOT EXISTS hub_deployments (
                    id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL REFERENCES hub_models(id),
                    version TEXT NOT NULL,
                    target TEXT NOT NULL,
                    profile_id TEXT NOT NULL REFERENCES profiles(id),
                    alias TEXT NOT NULL,
                    hourly_cost_usd REAL NOT NULL,
                    status TEXT NOT NULL,
                    runner_id TEXT,
                    instance_id TEXT,
                    provision_command TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    torn_down_at REAL
                );

                CREATE TABLE IF NOT EXISTS hub_quotes (
                    id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    target TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    hourly_cost_usd REAL NOT NULL,
                    fit TEXT NOT NULL,
                    fit_reason TEXT NOT NULL,
                    budget_remaining_usd REAL,
                    expires_at REAL NOT NULL,
                    used_at REAL,
                    created_at REAL NOT NULL
                );
                """
            )
        self._seed_profiles()
        self._seed_hub_models()

    def _seed_profiles(self) -> None:
        now = time.time()
        with self._connect() as connection:
            for profile in STARTER_PROFILES:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO profiles(
                        id, name, description, monthly_budget_usd, gpu_budget_usd,
                        rpm_limit, tpm_limit, allowed_models_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.id,
                        profile.name,
                        profile.description,
                        profile.monthly_budget_usd,
                        profile.gpu_budget_usd,
                        profile.rpm_limit,
                        profile.tpm_limit,
                        json.dumps(list(profile.allowed_models)),
                        now,
                        now,
                    ),
                )

    def _seed_hub_models(self) -> None:
        now = time.time()
        with self._connect() as connection:
            for model in SEED_MODELS:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO hub_models(
                        id, display_name, source_type, source, license, format,
                        status, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        model.id,
                        model.display_name,
                        model.source_type,
                        model.source,
                        model.license,
                        model.format,
                        model.status,
                        model.notes,
                        now,
                    ),
                )
                for version in model.versions:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO hub_versions(
                            model_id, version, source_revision, quantization,
                            size_bytes, context_length, min_vram_mib, runtime,
                            sha256, modelkit_ref, locations_json, is_latest, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            model.id,
                            version.version,
                            version.source_revision,
                            version.quantization,
                            version.size_bytes,
                            version.context_length,
                            version.min_vram_mib,
                            version.runtime,
                            version.sha256,
                            version.modelkit_ref,
                            json.dumps(list(version.locations)),
                            now,
                        ),
                    )

    def create_enrollment(self, raw_token: str, name: str, expires_at: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO enrollment_tokens(token_hash, name, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash(raw_token), name, time.time(), expires_at),
            )

    def consume_enrollment(self, raw_token: str) -> bool:
        return self.consume_enrollment_details(raw_token) is not None

    def get_enrollment(self, raw_token: str) -> dict[str, Any] | None:
        """Return an unconsumed, unexpired enrollment token record."""
        digest = token_hash(raw_token)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token_hash, name, created_at, expires_at, used_at FROM enrollment_tokens "
                "WHERE token_hash = ?",
                (digest,),
            ).fetchone()
        if row is None or row["used_at"] is not None or row["expires_at"] <= time.time():
            return None
        return dict(row)

    def consume_enrollment_details(self, raw_token: str) -> dict[str, Any] | None:
        """Consume a token once and return the identity it was issued for."""
        digest = token_hash(raw_token)
        now = time.time()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT token_hash, name, created_at, expires_at, used_at FROM enrollment_tokens WHERE token_hash = ?",
                (digest,),
            ).fetchone()
            if row is None or row["used_at"] is not None or row["expires_at"] <= now:
                return None
            updated = connection.execute(
                "UPDATE enrollment_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                (now, digest),
            )
            return dict(row) if updated.rowcount == 1 else None

    def create_runner(
        self,
        *,
        runner_id: str,
        name: str,
        model_alias: str,
        upstream_model: str,
        runner_token: str,
        snapshot: dict[str, Any],
    ) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runners(
                    id, name, model_alias, upstream_model, runner_token_hash,
                    status, snapshot_json, created_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, 'enrolled', ?, ?, ?)
                """,
                (
                    runner_id,
                    name,
                    model_alias,
                    upstream_model,
                    token_hash(runner_token),
                    json.dumps(snapshot, separators=(",", ":")),
                    now,
                    now,
                ),
            )

    def get_runner(self, runner_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runners WHERE id = ?", (runner_id,)).fetchone()
        return self._decode_runner(row) if row else None

    def authenticate_runner(self, runner_id: str, raw_token: str) -> dict[str, Any] | None:
        runner = self.get_runner(runner_id)
        if not runner or runner["revoked_at"] is not None:
            return None
        return runner if token_matches(raw_token, runner["runner_token_hash"]) else None

    def authenticate_proxy(self, runner_id: str, raw_token: str) -> bool:
        runner = self.get_runner(runner_id)
        expected = runner.get("proxy_token_hash") if runner else None
        return bool(expected and runner["revoked_at"] is None and token_matches(raw_token, expected))

    def mark_online(self, runner_id: str, proxy_token: str) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runners
                SET status = 'online', proxy_token_hash = ?, last_seen_at = ?, disconnected_at = NULL,
                    gateway_sync_error = NULL
                WHERE id = ? AND revoked_at IS NULL
                """,
                (token_hash(proxy_token), now, runner_id),
            )

    def update_heartbeat(self, runner_id: str, snapshot: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE runners SET last_seen_at = ?, snapshot_json = ?, status = 'online' WHERE id = ? AND revoked_at IS NULL",
                (time.time(), json.dumps(snapshot, separators=(",", ":")), runner_id),
            )

    def mark_offline(self, runner_id: str) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runners
                SET status = CASE WHEN revoked_at IS NULL THEN 'offline' ELSE 'revoked' END,
                    proxy_token_hash = NULL, disconnected_at = ?
                WHERE id = ?
                """,
                (now, runner_id),
            )

    def revoke(self, runner_id: str) -> bool:
        now = time.time()
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE runners SET status = 'revoked', revoked_at = ?, proxy_token_hash = NULL WHERE id = ?",
                (now, runner_id),
            )
        return result.rowcount == 1

    def set_gateway_sync(self, runner_id: str, model_id: str | None, error: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE runners SET litellm_model_id = ?, gateway_sync_error = ? WHERE id = ?",
                (model_id, error, runner_id),
            )

    def list_runners(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM runners ORDER BY created_at DESC").fetchall()
        return [self._decode_runner(row) for row in rows]

    # -- Profiles (Phase 5) -------------------------------------------------

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM profiles ORDER BY id").fetchall()
        return [self._decode_profile(row) for row in rows]

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        return self._decode_profile(row) if row else None

    def create_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO profiles(
                    id, name, description, monthly_budget_usd, gpu_budget_usd,
                    rpm_limit, tpm_limit, monthly_token_ceiling,
                    allowed_models_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile["id"],
                    profile["name"],
                    profile.get("description", ""),
                    profile.get("monthly_budget_usd"),
                    profile.get("gpu_budget_usd"),
                    profile.get("rpm_limit"),
                    profile.get("tpm_limit"),
                    profile.get("monthly_token_ceiling"),
                    json.dumps(profile.get("allowed_models", [])),
                    now,
                    now,
                ),
            )
        return self.get_profile(profile["id"])  # type: ignore[return-value]

    def update_profile(self, profile_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "name",
            "description",
            "monthly_budget_usd",
            "gpu_budget_usd",
            "rpm_limit",
            "tpm_limit",
            "monthly_token_ceiling",
            "allowed_models",
        }
        assignments = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "allowed_models":
                assignments.append("allowed_models_json = ?")
                values.append(json.dumps(value))
            else:
                assignments.append(f"{key} = ?")
                values.append(value)
        if not assignments:
            return self.get_profile(profile_id)
        values.append(time.time())
        values.append(profile_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE profiles SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
                values,
            )
        return self.get_profile(profile_id)

    def set_profile_team(self, profile_id: str, team_id: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE profiles SET litellm_team_id = ?, updated_at = ? WHERE id = ?",
                (team_id, time.time(), profile_id),
            )

    def add_profile_key(
        self,
        raw_key: str,
        profile_id: str,
        key_alias: str,
        migrated: bool = False,
        migration_note: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO profile_keys(
                    key_hash, profile_id, key_alias, migrated, migration_note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash(raw_key),
                    profile_id,
                    key_alias,
                    1 if migrated else 0,
                    migration_note,
                    time.time(),
                ),
            )

    def add_profile_key_by_hash(
        self,
        key_hash: str,
        profile_id: str,
        key_alias: str,
        migrated: bool = False,
        migration_note: str | None = None,
    ) -> None:
        """Store a mapping for a key whose hash is already known (migration)."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO profile_keys(
                    key_hash, profile_id, key_alias, migrated, migration_note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    key_hash,
                    profile_id,
                    key_alias,
                    1 if migrated else 0,
                    migration_note,
                    time.time(),
                ),
            )

    def get_profile_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM profile_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
        return dict(row) if row else None

    def list_profile_keys(self, profile_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM profile_keys WHERE profile_id = ? ORDER BY created_at",
                (profile_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_daily_usage(
        self,
        profile_id: str,
        day: str,
        *,
        provider_spend_usd: float,
        gpu_cost_usd: float,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        requests: int,
        source: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO profile_usage_daily(
                    profile_id, day, provider_spend_usd, gpu_cost_usd,
                    prompt_tokens, completion_tokens, requests, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, day) DO UPDATE SET
                    provider_spend_usd = excluded.provider_spend_usd,
                    gpu_cost_usd = excluded.gpu_cost_usd,
                    prompt_tokens = excluded.prompt_tokens,
                    completion_tokens = excluded.completion_tokens,
                    requests = excluded.requests,
                    source = excluded.source
                """,
                (
                    profile_id,
                    day,
                    provider_spend_usd,
                    gpu_cost_usd,
                    prompt_tokens,
                    completion_tokens,
                    requests,
                    source,
                ),
            )

    def get_daily_usage(self, profile_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM profile_usage_daily
                WHERE profile_id = ? ORDER BY day DESC LIMIT ?
                """,
                (profile_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def month_spend(self, profile_id: str, year_month: str) -> tuple[float, float]:
        """(provider spend, gpu cost) recorded for a YYYY-MM month."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(provider_spend_usd), 0) AS provider,
                       COALESCE(SUM(gpu_cost_usd), 0) AS gpu
                FROM profile_usage_daily
                WHERE profile_id = ? AND substr(day, 1, 7) = ?
                """,
                (profile_id, year_month),
            ).fetchone()
        return float(row["provider"]), float(row["gpu"])

    def record_alert(self, profile_id: str, kind: str, period: str, value: float) -> bool:
        """Record an alert; returns True only when it fired for the first time."""
        with self._connect() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO profile_alerts(
                    profile_id, kind, period, value, fired_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (profile_id, kind, period, value, time.time()),
            )
            return result.rowcount == 1

    def list_alerts(self, profile_id: str, unacknowledged_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM profile_alerts WHERE profile_id = ?"
        if unacknowledged_only:
            query += " AND acknowledged_at IS NULL"
        query += " ORDER BY fired_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, (profile_id,)).fetchall()
        return [dict(row) for row in rows]

    def acknowledge_alert(self, alert_id: int) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE profile_alerts SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
                (time.time(), alert_id),
            )
        return result.rowcount == 1

    # -- Model hub (Phase 6) ------------------------------------------------

    def list_hub_models(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM hub_models ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def get_hub_model(self, model_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hub_models WHERE id = ?", (model_id,)
            ).fetchone()
        return dict(row) if row else None

    def create_hub_model(self, model: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO hub_models(
                    id, display_name, source_type, source, license, format,
                    status, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model["id"],
                    model["display_name"],
                    model["source_type"],
                    model["source"],
                    model.get("license"),
                    model["format"],
                    model.get("status", "active"),
                    model.get("notes", ""),
                    time.time(),
                ),
            )
        return self.get_hub_model(model["id"])  # type: ignore[return-value]

    def list_hub_versions(self, model_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM hub_versions WHERE model_id = ? ORDER BY created_at",
                (model_id,),
            ).fetchall()
        return [self._decode_version(row) for row in rows]

    def get_hub_version(self, model_id: str, version: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hub_versions WHERE model_id = ? AND version = ?",
                (model_id, version),
            ).fetchone()
        return self._decode_version(row) if row else None

    def get_latest_version(self, model_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hub_versions WHERE model_id = ? AND is_latest = 1",
                (model_id,),
            ).fetchone()
        return self._decode_version(row) if row else None

    def add_hub_version(self, model_id: str, version: dict[str, Any]) -> dict[str, Any]:
        """Insert a new immutable version and move the `latest` pointer."""
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE hub_versions SET is_latest = 0 WHERE model_id = ?",
                (model_id,),
            )
            connection.execute(
                """
                INSERT INTO hub_versions(
                    model_id, version, source_revision, quantization,
                    size_bytes, context_length, min_vram_mib, runtime,
                    sha256, modelkit_ref, locations_json, is_latest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    model_id,
                    version["version"],
                    version.get("source_revision"),
                    version.get("quantization"),
                    version.get("size_bytes"),
                    version.get("context_length"),
                    version.get("min_vram_mib"),
                    version["runtime"],
                    version.get("sha256"),
                    version.get("modelkit_ref"),
                    json.dumps(version.get("locations", [])),
                    time.time(),
                ),
            )
        return self.get_hub_version(model_id, version["version"])  # type: ignore[return-value]

    def create_deployment(self, deployment: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO hub_deployments(
                    id, model_id, version, target, profile_id, alias,
                    hourly_cost_usd, status, provision_command, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deployment["id"],
                    deployment["model_id"],
                    deployment["version"],
                    deployment["target"],
                    deployment["profile_id"],
                    deployment["alias"],
                    deployment["hourly_cost_usd"],
                    deployment["status"],
                    deployment.get("provision_command"),
                    now,
                    now,
                ),
            )
        return self.get_deployment(deployment["id"])  # type: ignore[return-value]

    def get_deployment(self, deployment_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hub_deployments WHERE id = ?", (deployment_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_deployments(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM hub_deployments ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_deployment(self, deployment_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "status",
            "runner_id",
            "instance_id",
            "provision_command",
            "error",
            "torn_down_at",
        }
        assignments = []
        values: list[Any] = []
        for key, value in fields.items():
            if key in allowed:
                assignments.append(f"{key} = ?")
                values.append(value)
        if not assignments:
            return self.get_deployment(deployment_id)
        values.append(time.time())
        values.append(deployment_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE hub_deployments SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
                values,
            )
        return self.get_deployment(deployment_id)

    def link_deployment_runner(self, deployment_id: str, runner_id: str) -> dict[str, Any] | None:
        return self.update_deployment(
            deployment_id, {"runner_id": runner_id, "status": "running"}
        )

    def create_quote(self, quote: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO hub_quotes(
                    id, model_id, version, target, profile_id, hourly_cost_usd,
                    fit, fit_reason, budget_remaining_usd, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quote["id"],
                    quote["model_id"],
                    quote["version"],
                    quote["target"],
                    quote["profile_id"],
                    quote["hourly_cost_usd"],
                    quote["fit"],
                    quote["fit_reason"],
                    quote.get("budget_remaining_usd"),
                    quote["expires_at"],
                    time.time(),
                ),
            )
        return self.get_quote(quote["id"])  # type: ignore[return-value]

    def get_quote(self, quote_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hub_quotes WHERE id = ?", (quote_id,)
            ).fetchone()
        return dict(row) if row else None

    def consume_quote(self, quote_id: str) -> bool:
        """Mark a quote used if it exists, is unexpired, and unused."""
        now = time.time()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT expires_at, used_at FROM hub_quotes WHERE id = ?", (quote_id,)
            ).fetchone()
            if row is None or row["used_at"] is not None or row["expires_at"] <= now:
                return False
            updated = connection.execute(
                "UPDATE hub_quotes SET used_at = ? WHERE id = ? AND used_at IS NULL",
                (now, quote_id),
            )
            return updated.rowcount == 1

    @staticmethod
    def _decode_profile(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["allowed_models"] = json.loads(result.pop("allowed_models_json"))
        return result

    @staticmethod
    def _decode_version(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["locations"] = json.loads(result.pop("locations_json"))
        result["is_latest"] = bool(result["is_latest"])
        return result

    @staticmethod
    def _decode_runner(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["snapshot"] = json.loads(result.pop("snapshot_json"))
        return result
