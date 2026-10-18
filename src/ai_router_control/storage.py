from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

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
                """
            )

    def create_enrollment(self, raw_token: str, name: str, expires_at: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO enrollment_tokens(token_hash, name, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash(raw_token), name, time.time(), expires_at),
            )

    def consume_enrollment(self, raw_token: str) -> bool:
        digest = token_hash(raw_token)
        now = time.time()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT token_hash, expires_at, used_at FROM enrollment_tokens WHERE token_hash = ?",
                (digest,),
            ).fetchone()
            if row is None or row["used_at"] is not None or row["expires_at"] <= now:
                return False
            updated = connection.execute(
                "UPDATE enrollment_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                (now, digest),
            )
            return updated.rowcount == 1

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

    @staticmethod
    def _decode_runner(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["snapshot"] = json.loads(result.pop("snapshot_json"))
        return result
