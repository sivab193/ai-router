"""Tests for Phase 5 profiles: domain logic, API, sync, and migration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ai_router_control.app import create_app
from ai_router_control.config import Settings
from ai_router_control.profiles import (
    budget_alert_kinds,
    burn_rate,
    migrate_key_tags,
    model_allowed,
    token_anomaly,
)

ADMIN = {"Authorization": "Bearer admin-test"}


def make_app(tmp_path):
    settings = Settings(
        admin_token="admin-test",
        database_path=tmp_path / "control.db",
        public_url="http://testserver",
        internal_url="http://control:8080",
        litellm_url=None,
        litellm_control_key=None,
        heartbeat_ttl_seconds=30,
        request_start_timeout_seconds=2,
        request_idle_timeout_seconds=2,
    )
    app = create_app(settings)
    return TestClient(app), app


class FakeGateway:
    """Stand-in for LiteLLMGateway; class-level knobs per test."""

    daily_rows: list[dict] = []
    keys: list[dict] = []
    deleted_keys: list[str] = []

    def __init__(self, settings):
        self.settings = settings
        self.synced_teams: list[dict] = []

    @property
    def enabled(self):
        return True

    async def sync_team(self, profile):
        self.synced_teams.append(profile)
        return f"team-{profile['id']}"

    async def delete_team(self, team_id):
        return None

    async def get_team_spend(self, team_id):
        return {"spend": 0.0, "max_budget": None, "models": []}

    async def get_team_daily_spend(self, team_id, start_date, end_date):
        return list(self.daily_rows)

    async def create_profile_key(self, **kwargs):
        return "sk-fake-profile-key"

    async def list_keys(self):
        return list(self.keys)

    async def delete_key(self, key_hash):
        self.deleted_keys.append(key_hash)

    async def register(self, runner, proxy_token):
        return None

    async def unregister(self, model_id):
        return None


def today_str(days_ago=0):
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# -- Pure domain logic -------------------------------------------------------


def test_model_allowed_glob():
    assert model_allowed(["coding/*", "local/*"], "coding/fast")
    assert model_allowed(["coding/*"], "coding/deep")
    assert not model_allowed(["coding/*"], "local/x")
    assert not model_allowed(["coding/fast"], "coding/deep")


def test_migrate_key_tags():
    assert migrate_key_tags(["aayvu"], "demo") == ("aayvu", False)
    assert migrate_key_tags([], "splitllm-key") == ("splitllm", False)
    assert migrate_key_tags(["personal"], "x") == ("personal", False)
    profile_id, unmatched = migrate_key_tags(["misc"], "random-key")
    assert profile_id == "everything-else"
    assert unmatched is True


def test_budget_alert_kinds():
    assert budget_alert_kinds(100.0, 49.0) == []
    assert budget_alert_kinds(100.0, 50.0) == ["budget_50"]
    assert budget_alert_kinds(100.0, 85.0) == ["budget_50", "budget_80"]
    assert budget_alert_kinds(100.0, 100.0) == ["budget_50", "budget_80", "budget_100"]
    assert budget_alert_kinds(None, 999.0) == []


def test_token_anomaly_needs_history_and_fires_at_2x():
    assert token_anomaly([("2026-11-17", 1000), ("2026-11-18", 1000)]) == (False, None)
    baseline = [("2026-11-17", 1000), ("2026-11-18", 1000), ("2026-11-19", 1000)]
    assert token_anomaly([*baseline, ("2026-11-20", 1500)])[0] is False
    assert token_anomaly([*baseline, ("2026-11-20", 2500)]) == (True, 2.5)


def test_burn_rate_projection():
    rate, projection = burn_rate([("2026-11-01", 2.0), ("2026-11-02", 2.0)], 2, 30)
    assert rate == 2.0
    assert projection == 60.0
    assert burn_rate([], 5, 30) == (None, None)


# -- Profile CRUD ------------------------------------------------------------


def test_starter_profiles_seeded(tmp_path):
    client, _ = make_app(tmp_path)
    response = client.get("/control/v1/profiles", headers=ADMIN)
    assert response.status_code == 200
    ids = {profile["id"] for profile in response.json()}
    assert ids == {"personal", "aayvu", "splitllm", "everything-else"}
    personal = next(p for p in response.json() if p["id"] == "personal")
    assert personal["monthly_budget_usd"] == 50.0
    assert personal["team_synced"] is False
    assert personal["month_spend_usd"] == 0.0


def test_profile_create_patch_and_validation(tmp_path):
    client, _ = make_app(tmp_path)
    created = client.post(
        "/control/v1/profiles",
        headers=ADMIN,
        json={
            "id": "side-project",
            "name": "Side Project",
            "monthly_budget_usd": 10.0,
            "allowed_models": ["coding/*"],
        },
    )
    assert created.status_code == 201
    assert created.json()["id"] == "side-project"

    duplicate = client.post(
        "/control/v1/profiles",
        headers=ADMIN,
        json={"id": "side-project", "name": "Again"},
    )
    assert duplicate.status_code == 409

    invalid = client.post(
        "/control/v1/profiles",
        headers=ADMIN,
        json={"id": "Bad ID!", "name": "Bad"},
    )
    assert invalid.status_code == 422

    updated = client.patch(
        "/control/v1/profiles/side-project",
        headers=ADMIN,
        json={"monthly_budget_usd": 20.0, "allowed_models": ["coding/*", "local/*"]},
    )
    assert updated.status_code == 200
    assert updated.json()["monthly_budget_usd"] == 20.0
    assert updated.json()["allowed_models"] == ["coding/*", "local/*"]

    assert client.get("/control/v1/profiles/nope", headers=ADMIN).status_code == 404


def test_profile_endpoints_require_admin(tmp_path):
    client, _ = make_app(tmp_path)
    assert client.get("/control/v1/profiles").status_code == 401
    assert client.post("/control/v1/profiles", json={}).status_code == 401


# -- Keys --------------------------------------------------------------------


def test_profile_key_requires_gateway_when_unconfigured(tmp_path):
    client, _ = make_app(tmp_path)
    response = client.post(
        "/control/v1/profiles/personal/keys",
        headers=ADMIN,
        json={"key_alias": "laptop"},
    )
    assert response.status_code == 503


def test_profile_key_allowlist_rejection(tmp_path):
    client, _ = make_app(tmp_path)
    response = client.post(
        "/control/v1/profiles/everything-else/keys",
        headers=ADMIN,
        json={"key_alias": "x", "models": ["coding/deep"]},
    )
    assert response.status_code == 422
    assert "coding/deep" in response.json()["detail"]


def test_profile_key_creation_and_budget_exhaustion(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_router_control.app.LiteLLMGateway", FakeGateway)
    client, app = make_app(tmp_path)
    created = client.post(
        "/control/v1/profiles/personal/keys",
        headers=ADMIN,
        json={"key_alias": "laptop"},
    )
    assert created.status_code == 201
    assert created.json()["key"] == "sk-fake-profile-key"
    assert created.json()["team_id"] == "team-personal"

    detail = client.get("/control/v1/profiles/personal", headers=ADMIN).json()
    assert detail["key_count"] == 1
    assert detail["team_synced"] is True
    assert "key_hash" not in detail["keys"][0]
    assert detail["keys"][0]["status"] == "active"
    assert "sk-fake-profile-key" not in str(detail)

    store = app.state.store
    store.record_daily_usage(
        "personal",
        today_str(),
        provider_spend_usd=50.0,
        gpu_cost_usd=0.0,
        prompt_tokens=0,
        completion_tokens=0,
        requests=1,
        source="test",
    )
    exhausted = client.post(
        "/control/v1/profiles/personal/keys",
        headers=ADMIN,
        json={"key_alias": "second"},
    )
    assert exhausted.status_code == 409


def test_profile_key_revocation_is_immediate_and_idempotent(tmp_path, monkeypatch):
    FakeGateway.deleted_keys = []
    monkeypatch.setattr("ai_router_control.app.LiteLLMGateway", FakeGateway)
    client, _ = make_app(tmp_path)
    assert client.post(
        "/control/v1/profiles/personal/keys",
        headers=ADMIN,
        json={"key_alias": "temporary"},
    ).status_code == 201
    key = client.get("/control/v1/profiles/personal", headers=ADMIN).json()["keys"][0]

    revoked = client.delete(
        f"/control/v1/profiles/personal/keys/{key['id']}", headers=ADMIN
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert FakeGateway.deleted_keys == [key["id"]]
    detail = client.get("/control/v1/profiles/personal", headers=ADMIN).json()
    assert detail["key_count"] == 0
    assert detail["keys"][0]["revoked_at"] is not None
    assert client.delete(
        f"/control/v1/profiles/personal/keys/{key['id']}", headers=ADMIN
    ).status_code == 409


# -- Sync and alerts ---------------------------------------------------------


def test_sync_gateway_disabled_still_evaluates_local_alerts(tmp_path):
    client, app = make_app(tmp_path)
    store = app.state.store
    for days_ago in (3, 2, 1):
        store.record_daily_usage(
            "personal",
            today_str(days_ago),
            provider_spend_usd=1.0,
            gpu_cost_usd=0.0,
            prompt_tokens=500,
            completion_tokens=500,
            requests=10,
            source="test",
        )
    store.record_daily_usage(
        "personal",
        today_str(),
        provider_spend_usd=1.0,
        gpu_cost_usd=0.0,
        prompt_tokens=2500,
        completion_tokens=2500,
        requests=10,
        source="test",
    )
    response = client.post("/control/v1/profiles/personal/sync", headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert body["gateway"] == "disabled"
    assert body["team_synced"] is False
    assert "token_anomaly" in body["alerts_fired"]

    # Second sync fires nothing new: alerts are deduplicated per period.
    again = client.post("/control/v1/profiles/personal/sync", headers=ADMIN)
    assert again.json()["alerts_fired"] == []

    alerts = client.get("/control/v1/profiles/personal", headers=ADMIN).json()[
        "unacknowledged_alerts"
    ]
    assert len(alerts) == 1
    alert_id = alerts[0]["id"]
    ack = client.post(
        f"/control/v1/profiles/personal/alerts/{alert_id}/acknowledge", headers=ADMIN
    )
    assert ack.status_code == 200
    assert (
        client.post(
            f"/control/v1/profiles/personal/alerts/{alert_id}/acknowledge", headers=ADMIN
        ).status_code
        == 404
    )


def test_sync_with_gateway_fires_budget_alert_once(tmp_path, monkeypatch):
    FakeGateway.daily_rows = [
        {
            "day": today_str(),
            "spend": 30.0,
            "prompt_tokens": 100,
            "completion_tokens": 100,
            "requests": 5,
        }
    ]
    monkeypatch.setattr("ai_router_control.app.LiteLLMGateway", FakeGateway)
    client, _ = make_app(tmp_path)
    response = client.post("/control/v1/profiles/personal/sync", headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert body["team_synced"] is True
    assert body["team_id"] == "team-personal"
    assert body["spend_synced"] is True
    assert body["alerts_fired"] == ["budget_50"]
    assert body["summary"]["month_provider_spend_usd"] == 30.0
    assert body["summary"]["budget_remaining_usd"] == 20.0

    again = client.post("/control/v1/profiles/personal/sync", headers=ADMIN)
    assert again.json()["alerts_fired"] == []
    FakeGateway.daily_rows = []


# -- Migration ---------------------------------------------------------------


def test_migrate_keys_maps_tags_and_flags_unmatched(tmp_path):
    client, app = make_app(tmp_path)
    response = client.post(
        "/control/v1/profiles/migrate-keys",
        headers=ADMIN,
        json={
            "keys": [
                {"key_alias": "aayvu-demo", "key": "sk-aayvu-1", "tags": ["aayvu"]},
                {"key_alias": "random-key", "key": "sk-random-1", "tags": []},
                {"key_alias": "no-secret", "tags": []},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert {item["profile_id"] for item in body["migrated"]} == {
        "aayvu",
        "everything-else",
    }
    assert len(body["flagged_for_review"]) == 1
    assert body["flagged_for_review"][0]["key_alias"] == "random-key"
    assert len(body["skipped"]) == 1  # no key or key_hash supplied

    detail = client.get("/control/v1/profiles/aayvu", headers=ADMIN).json()
    assert detail["key_count"] == 1
    assert detail["keys"][0]["migrated"] == 1

    # Re-running without force skips already-mapped keys.
    rerun = client.post(
        "/control/v1/profiles/migrate-keys",
        headers=ADMIN,
        json={"keys": [{"key_alias": "aayvu-demo", "key": "sk-aayvu-1", "tags": ["aayvu"]}]},
    )
    assert rerun.json()["migrated"] == []
    assert len(rerun.json()["skipped"]) == 1

    forced = client.post(
        "/control/v1/profiles/migrate-keys",
        headers=ADMIN,
        json={
            "force": True,
            "keys": [{"key_alias": "aayvu-demo", "key": "sk-aayvu-1", "tags": ["aayvu"]}],
        },
    )
    assert len(forced.json()["migrated"]) == 1


def test_migrate_keys_discover_needs_gateway(tmp_path):
    client, _ = make_app(tmp_path)
    response = client.post(
        "/control/v1/profiles/migrate-keys", headers=ADMIN, json={"discover": True}
    )
    assert response.status_code == 503


def test_migrate_keys_discover_via_gateway(tmp_path, monkeypatch):
    FakeGateway.keys = [
        {"key_alias": "old-split", "token": "hash-1", "tags": ["splitllm"]},
        {"key_alias": "mystery", "token": "hash-2", "tags": []},
    ]
    monkeypatch.setattr("ai_router_control.app.LiteLLMGateway", FakeGateway)
    client, _ = make_app(tmp_path)
    response = client.post(
        "/control/v1/profiles/migrate-keys", headers=ADMIN, json={"discover": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert {item["profile_id"] for item in body["migrated"]} == {
        "splitllm",
        "everything-else",
    }
    assert len(body["flagged_for_review"]) == 1
    FakeGateway.keys = []
