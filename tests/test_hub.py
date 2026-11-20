"""Tests for Phase 6 model hub: registry, quotes, deploy, teardown."""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ai_router_control.app import create_app
from ai_router_control.config import Settings
from ai_router_control.hub import (
    TARGETS,
    deploy_alias,
    fit_check,
    gcp_create_command,
    gpu_cost_for_day,
    instance_name,
    render_gcp_startup_script,
    source_reachable_from_target,
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


def snapshot():
    return {
        "runtime": "vllm",
        "runtime_version": "0.test",
        "healthy": True,
        "models": [{"id": "Qwen/Qwen3-Coder-30B-A3B-Instruct", "max_model_len": 32768}],
        "routes": ["/v1/chat/completions", "/v1/models"],
        "metrics": {"prompt_tokens_total": 42},
        "hardware": {"gpus": [{"name": "Test GPU", "memory_mib": 24576}]},
    }


def quote(client, model_id, target, profile_id="personal", version=None):
    body = {"model_id": model_id, "target": target, "profile_id": profile_id}
    if version:
        body["version"] = version
    response = client.post("/control/v1/hub/deployments/quote", headers=ADMIN, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def deploy(client, quote_id, **extra):
    body = {"quote_id": quote_id, "confirm": True, **extra}
    return client.post("/control/v1/hub/deployments", headers=ADMIN, json=body)


# -- Pure domain logic -------------------------------------------------------


def test_deploy_alias_and_instance_name():
    assert deploy_alias("qwen3-coder-30b", "bf16") == "local/qwen3-coder-30b-bf16"
    assert instance_name("dep_0123456789abcdef") == "ai-router-dep-0123456789abcdef"


def test_fit_check():
    target = TARGETS["gcp-l4"]
    assert fit_check(None, target)[0] == "unknown"
    assert fit_check(24 * 1024, target)[0] == "fits"
    assert fit_check(40 * 1024, target)[0] == "refused"


def test_source_reachable_from_target():
    assert source_reachable_from_target("huggingface", None, "gcp-l4")[0] is True
    assert source_reachable_from_target("local", None, "gcp-l4")[0] is False
    assert source_reachable_from_target("local", "registry/x:1", "gcp-l4")[0] is True
    assert source_reachable_from_target("local", None, "fleet-rog")[0] is True


def test_gcp_create_command():
    command = gcp_create_command(
        instance_name="ai-router-dep-abc",
        zone="us-central1-a",
        target=TARGETS["gcp-l4"],
        project="demo-project",
        startup_script_path="/tmp/start.sh",
        enrollment_token="aienr-secret",
    )
    joined = " ".join(command)
    assert "type=nvidia-l4,count=1" in joined
    assert "--machine-type g2-standard-4" in joined
    assert "ai-router-enrollment-token=aienr-secret" in joined
    assert "--metadata-from-file startup-script=/tmp/start.sh" in joined
    with pytest.raises(ValueError):
        gcp_create_command(
            instance_name="x",
            zone="us-central1-a",
            target=TARGETS["runpod-4090"],
            project=None,
            startup_script_path="/tmp/start.sh",
            enrollment_token="t",
        )


def test_startup_script_carries_no_provider_credentials():
    script = render_gcp_startup_script(
        public_url="https://ai.example.dev",
        runner_name="hub-dep-abc",
        model_source="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        served_name="local/qwen3-coder-30b-bf16",
    )
    assert 'PUBLIC_URL="https://ai.example.dev"' in script
    assert 'curl -fsSL "$PUBLIC_URL/install/runner.sh"' in script
    assert '--name "hub-dep-abc"' in script
    assert "Qwen/Qwen3-Coder-30B-A3B-Instruct" in script
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in script
    assert "api_key" not in script.lower()
    assert "127.0.0.1" in script  # vLLM bound to loopback only


def test_gpu_cost_for_day():
    now = time.time()
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    deployments = [
        {
            "profile_id": "personal",
            "status": "running",
            "created_at": now - 3600,
            "torn_down_at": None,
            "hourly_cost_usd": 0.71,
        },
        {
            "profile_id": "personal",
            "status": "torn_down",
            "created_at": now - 7200,
            "torn_down_at": now - 3600,
            "hourly_cost_usd": 1.60,
        },
        {
            "profile_id": "aayvu",
            "status": "running",
            "created_at": now - 3600,
            "torn_down_at": None,
            "hourly_cost_usd": 99.0,
        },
        {
            "profile_id": "personal",
            "status": "failed",
            "created_at": now - 3600,
            "torn_down_at": None,
            "hourly_cost_usd": 99.0,
        },
    ]
    cost = gpu_cost_for_day(deployments, "personal", day_start, now)
    assert cost == pytest.approx(0.71 + 1.60, abs=0.01)
    assert gpu_cost_for_day(deployments, "aayvu", day_start, now) == pytest.approx(99.0, abs=0.01)


# -- Registry ----------------------------------------------------------------


def test_seed_models_and_targets(tmp_path):
    client, _ = make_app(tmp_path)
    models = client.get("/control/v1/hub/models", headers=ADMIN)
    assert models.status_code == 200
    ids = {model["id"] for model in models.json()}
    assert ids == {"qwen-image-2.1", "qwen3-coder-30b", "deepseek-v4.1-flash", "aayvu-ft"}
    qwen_image = next(m for m in models.json() if m["id"] == "qwen-image-2.1")
    assert qwen_image["latest_version"]["version"] == "q8_0"
    assert qwen_image["latest_version"]["min_vram_mib"] == 6 * 1024
    planned = next(m for m in models.json() if m["id"] == "aayvu-ft")
    assert planned["status"] == "planned"
    assert planned["version_count"] == 0

    targets = client.get("/control/v1/hub/targets", headers=ADMIN)
    assert targets.status_code == 200
    assert len(targets.json()) == 8
    gcp_l4 = next(t for t in targets.json() if t["id"] == "gcp-l4")
    assert gcp_l4["hourly_cost_usd"] == 0.71
    assert gcp_l4["provision"] == "gcloud"


def test_create_model_and_versions_moves_latest(tmp_path):
    client, _ = make_app(tmp_path)
    created = client.post(
        "/control/v1/hub/models",
        headers=ADMIN,
        json={
            "id": "tiny-model",
            "display_name": "Tiny Model",
            "source_type": "huggingface",
            "source": "org/tiny",
            "format": "safetensors",
        },
    )
    assert created.status_code == 201
    assert client.post(
        "/control/v1/hub/models",
        headers=ADMIN,
        json={
            "id": "tiny-model",
            "display_name": "Tiny Model",
            "source_type": "huggingface",
            "source": "org/tiny",
            "format": "safetensors",
        },
    ).status_code == 409
    assert client.post(
        "/control/v1/hub/models",
        headers=ADMIN,
        json={
            "id": "Bad ID!",
            "display_name": "Bad",
            "source_type": "huggingface",
            "source": "org/bad",
            "format": "safetensors",
        },
    ).status_code == 422

    v1 = client.post(
        "/control/v1/hub/models/tiny-model/versions",
        headers=ADMIN,
        json={"version": "v1", "runtime": "vllm", "min_vram_mib": 4096},
    )
    assert v1.status_code == 201
    assert v1.json()["is_latest"] is True
    assert client.post(
        "/control/v1/hub/models/tiny-model/versions",
        headers=ADMIN,
        json={"version": "v1", "runtime": "vllm"},
    ).status_code == 409

    v2 = client.post(
        "/control/v1/hub/models/tiny-model/versions",
        headers=ADMIN,
        json={"version": "v2", "runtime": "vllm", "min_vram_mib": 4096},
    )
    assert v2.status_code == 201
    detail = client.get("/control/v1/hub/models/tiny-model", headers=ADMIN).json()
    assert [v["version"] for v in detail["versions"]] == ["v1", "v2"]
    assert detail["versions"][0]["is_latest"] is False
    assert detail["versions"][1]["is_latest"] is True


def test_hub_versions_are_immutable(tmp_path):
    _, app = make_app(tmp_path)
    store = app.state.store
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE hub_versions SET size_bytes = 1 WHERE model_id = 'qwen-image-2.1'"
            )
        # Flipping the latest pointer is the one allowed mutation.
        connection.execute(
            "UPDATE hub_versions SET is_latest = 0 WHERE model_id = 'qwen-image-2.1'"
        )


def test_kitfile_and_manifest(tmp_path):
    client, _ = make_app(tmp_path)
    kitfile = client.get(
        "/control/v1/hub/models/qwen-image-2.1/versions/q8_0/kitfile", headers=ADMIN
    )
    assert kitfile.status_code == 200
    assert "manifestVersion: v1.0.0" in kitfile.text
    assert "qwen-image-2.1" in kitfile.text

    manifest = client.get(
        "/control/v1/hub/models/qwen-image-2.1/versions/q8_0/manifest", headers=ADMIN
    )
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["model_id"] == "qwen-image-2.1"
    assert body["version"] == "q8_0"
    assert body["runtime"] == "comfyui"
    assert body["locations"][0]["kind"] == "rog"


# -- Quotes and deploys ------------------------------------------------------


def test_quote_fit_verdicts(tmp_path):
    client, _ = make_app(tmp_path)
    # 6 GB model on the 6 GB ROG: fits (HF source, fleet target).
    fits = quote(client, "qwen-image-2.1", "fleet-rog")
    assert fits["fit"] == "fits"
    assert fits["hourly_cost_usd"] == 0.0
    assert fits["alias"] == "local/qwen-image-2.1-q8_0"
    assert fits["budget_remaining_usd"] == 50.0

    # Unknown VRAM: unknown, not a refusal.
    unknown = quote(client, "qwen3-coder-30b", "gcp-l4")
    assert unknown["fit"] == "unknown"

    # Local-only weights on a cloud target: refused.
    refused = quote(client, "deepseek-v4.1-flash", "gcp-l4")
    assert refused["fit"] == "refused"
    assert "local-only" in refused["fit_reason"]

    # Same weights on a fleet target: reachable, unknown VRAM only.
    fleet = quote(client, "deepseek-v4.1-flash", "fleet-rog")
    assert fleet["fit"] == "unknown"

    assert client.post(
        "/control/v1/hub/deployments/quote",
        headers=ADMIN,
        json={"model_id": "qwen-image-2.1", "target": "nope", "profile_id": "personal"},
    ).status_code == 422


def test_deploy_requires_confirm_and_quote_is_single_use(tmp_path):
    client, _ = make_app(tmp_path)
    q = quote(client, "qwen-image-2.1", "gcp-l4")
    assert q["fit"] == "fits"

    no_confirm = client.post(
        "/control/v1/hub/deployments",
        headers=ADMIN,
        json={"quote_id": q["id"], "confirm": False},
    )
    assert no_confirm.status_code == 422

    created = deploy(client, q["id"])
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "awaiting_launch"
    assert body["alias"] == "local/qwen-image-2.1-q8_0"
    assert "gcloud compute instances create" in body["provision_command"]
    assert "ai-router-enrollment-token=" in body["provision_command"]
    assert body["runner_name"].startswith("hub-dep_")
    assert body["runner_name"] in body["startup_script"]
    assert "vllm" in body["startup_script"].lower()

    # The quote is consumed: a second deploy with it is gone, not a duplicate.
    assert deploy(client, q["id"]).status_code == 410


def test_deploy_refused_and_unknown_fit(tmp_path):
    client, _ = make_app(tmp_path)
    refused = quote(client, "deepseek-v4.1-flash", "gcp-l4")
    assert deploy(client, refused["id"]).status_code == 422

    unknown = quote(client, "qwen3-coder-30b", "gcp-l4")
    assert unknown["fit"] == "unknown"
    assert deploy(client, unknown["id"]).status_code == 422
    override = deploy(client, unknown["id"], allow_unknown_fit=True)
    assert override.status_code == 201


def test_deploy_budget_exhausted(tmp_path):
    client, app = make_app(tmp_path)
    app.state.store.record_daily_usage(
        "personal",
        datetime.now(UTC).strftime("%Y-%m-%d"),
        provider_spend_usd=50.0,
        gpu_cost_usd=0.0,
        prompt_tokens=0,
        completion_tokens=0,
        requests=1,
        source="test",
    )
    q = quote(client, "qwen-image-2.1", "gcp-l4")
    assert q["budget_remaining_usd"] == 0.0
    assert deploy(client, q["id"]).status_code == 409


def test_enrollment_links_deployment_and_opens_allowlist(tmp_path):
    client, _ = make_app(tmp_path)
    q = quote(client, "qwen-image-2.1", "fleet-rog")
    created = deploy(client, q["id"])
    assert created.status_code == 201
    deployment = created.json()
    token = re.search(r"--token (\S+)", deployment["install_command"]).group(1)

    enrolled = client.post(
        "/control/v1/runners/enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": deployment["runner_name"], "snapshot": snapshot()},
    )
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["model_alias"] == deployment["alias"]
    assert enrolled.json()["model_alias"] == "local/qwen-image-2.1-q8_0"

    detail = client.get(
        f"/control/v1/hub/deployments/{deployment['id']}", headers=ADMIN
    ).json()
    assert detail["status"] == "enrolled"
    assert detail["runner_id"] == enrolled.json()["runner_id"]

    profile = client.get("/control/v1/profiles/personal", headers=ADMIN).json()
    assert enrolled.json()["model_alias"] in profile["allowed_models"]


def test_teardown_removes_routing_and_runner(tmp_path):
    client, _ = make_app(tmp_path)
    q = quote(client, "qwen-image-2.1", "fleet-rog")
    deployment = deploy(client, q["id"]).json()
    token = re.search(r"--token (\S+)", deployment["install_command"]).group(1)
    enrolled = client.post(
        "/control/v1/runners/enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": deployment["runner_name"], "snapshot": snapshot()},
    )
    assert enrolled.status_code == 200

    torn = client.post(
        f"/control/v1/hub/deployments/{deployment['id']}/teardown", headers=ADMIN
    )
    assert torn.status_code == 200, torn.text
    body = torn.json()
    assert body["status"] == "torn_down"
    assert body["routing_removed"] is True
    assert body["runner_removed"] is True
    assert body["torn_down_at"] is not None

    runners = client.get("/control/v1/runners", headers=ADMIN).json()
    runner = next(r for r in runners if r["id"] == enrolled.json()["runner_id"])
    assert runner["status"] == "revoked"

    assert (
        client.post(
            f"/control/v1/hub/deployments/{deployment['id']}/teardown", headers=ADMIN
        ).status_code
        == 409
    )


def test_teardown_before_launch_is_safe(tmp_path):
    client, _ = make_app(tmp_path)
    q = quote(client, "qwen-image-2.1", "gcp-l4")
    deployment = deploy(client, q["id"]).json()
    torn = client.post(
        f"/control/v1/hub/deployments/{deployment['id']}/teardown", headers=ADMIN
    )
    assert torn.status_code == 200
    assert torn.json()["runner_removed"] is False
    assert torn.json()["instance_delete"]["attempted"] is False


def test_mark_launched_transitions(tmp_path):
    client, _ = make_app(tmp_path)
    q = quote(client, "qwen-image-2.1", "gcp-l4")
    deployment = deploy(client, q["id"]).json()
    launched = client.post(
        f"/control/v1/hub/deployments/{deployment['id']}/launched",
        headers=ADMIN,
        json={"instance_id": "ai-router-dep-xyz"},
    )
    assert launched.status_code == 200
    assert launched.json()["status"] == "provisioning"
    assert launched.json()["instance_id"] == "ai-router-dep-xyz"
    assert (
        client.post(
            f"/control/v1/hub/deployments/{deployment['id']}/launched", headers=ADMIN, json={}
        ).status_code
        == 409
    )
