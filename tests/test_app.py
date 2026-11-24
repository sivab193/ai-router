from __future__ import annotations

from fastapi.testclient import TestClient

from ai_router_control.app import create_app, service_from_gateway_model
from ai_router_control.config import Settings


def make_client(tmp_path):
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
    return TestClient(create_app(settings))


def test_settings_fail_closed_without_admin_token(monkeypatch):
    monkeypatch.delenv("AI_ROUTER_ADMIN_TOKEN", raising=False)
    try:
        Settings.from_env()
    except RuntimeError as error:
        assert "AI_ROUTER_ADMIN_TOKEN" in str(error)
    else:
        raise AssertionError("missing admin token should stop startup")


def test_gateway_models_normalize_into_catalog_services():
    service = service_from_gateway_model(
        {
            "id": "bedrock/claude-sonnet",
            "owned_by": "bedrock",
            "model_info": {
                "display_name": "Claude Sonnet",
                "input_modalities": ["text", "image"],
                "max_input_tokens": 200000,
                "supports_function_calling": True,
            },
        }
    )
    assert service is not None
    assert service["provider"] == "bedrock"
    assert service["kind"] == "vision_model"
    assert service["capabilities"] == ["chat", "text", "tools", "vision"]


def enrollment(client: TestClient):
    response = client.post(
        "/control/v1/enrollment-tokens",
        headers={"Authorization": "Bearer admin-test"},
        json={"name": "gpu-one", "expires_in_seconds": 300},
    )
    assert response.status_code == 200
    return response.json()


def snapshot():
    return {
        "runtime": "vllm",
        "runtime_version": "0.test",
        "healthy": True,
        "models": [{"id": "Qwen/Qwen3-Coder", "max_model_len": 32768}],
        "routes": ["/v1/chat/completions", "/v1/models"],
        "metrics": {"prompt_tokens_total": 42},
        "hardware": {"gpus": [{"name": "Test GPU", "memory_mib": 24576}]},
    }


def test_dashboard_assets_are_bundled(tmp_path):
    client = make_client(tmp_path)
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Connect machine" in dashboard.text
    assert "SpaceGrotesk-Variable.ttf" in dashboard.text

    font = client.get("/install/fonts/SpaceGrotesk-Variable.ttf")
    assert font.status_code == 200
    assert len(font.content) > 100_000


def test_catalog_is_filtered_by_organization_profile(tmp_path):
    client = make_client(tmp_path)
    store = client.app.state.store
    store.upsert_service(
        {
            "id": "bedrock/claude",
            "display_name": "Claude on Bedrock",
            "provider": "bedrock",
            "kind": "language_model",
            "capabilities": ["text", "vision"],
            "endpoints": ["/v1/chat/completions"],
        }
    )
    store.upsert_service(
        {
            "id": "vertex/gemini",
            "display_name": "Gemini on Vertex AI",
            "provider": "google_vertex",
            "kind": "vision_model",
            "capabilities": ["text", "vision"],
            "endpoints": ["/v1/chat/completions"],
        }
    )
    store.update_profile(
        "aayvu",
        {
            "allowed_providers": ["bedrock"],
            "allowed_services": ["bedrock/claude", "vertex/gemini"],
        },
    )
    store.add_profile_key("sk-aayvu", "aayvu", "aayvu-test")

    denied = client.get("/v1/catalog")
    assert denied.status_code == 401
    catalog = client.get(
        "/v1/catalog", headers={"Authorization": "Bearer sk-aayvu"}
    )
    assert catalog.status_code == 200
    assert catalog.json()["profile_id"] == "aayvu"
    assert catalog.json()["providers"] == ["bedrock"]
    assert [item["id"] for item in catalog.json()["data"]] == ["bedrock/claude"]


def test_admin_can_manage_document_services(tmp_path):
    client = make_client(tmp_path)
    headers = {"Authorization": "Bearer admin-test"}
    service = {
        "id": "google/document-ai",
        "display_name": "Google Document AI",
        "provider": "google_cloud",
        "kind": "document_intelligence",
        "capabilities": ["ocr", "layout", "tables", "forms"],
        "endpoints": ["/v1/documents/process"],
        "metadata": {"processor": "form-parser"},
    }
    created = client.put(
        "/control/v1/services/google/document-ai", headers=headers, json=service
    )
    assert created.status_code == 200
    assert created.json()["kind"] == "document_intelligence"
    assert client.get("/control/v1/services", headers=headers).json()[0]["id"] == service["id"]
    assert client.delete(
        "/control/v1/services/google/document-ai", headers=headers
    ).json() == {"deleted": True}


def test_document_execution_enforces_profile_policy(tmp_path):
    client = make_client(tmp_path)
    store = client.app.state.store
    store.upsert_service(
        {
            "id": "google/document-ai",
            "display_name": "Google Document AI",
            "provider": "google_cloud",
            "kind": "document_intelligence",
            "capabilities": ["ocr", "layout"],
            "endpoints": ["/v1/documents/process"],
            "metadata": {"internal_adapter_url": "must-not-leak"},
        }
    )
    store.update_profile(
        "aayvu",
        {"allowed_providers": ["bedrock"], "allowed_services": ["google/document-ai"]},
    )
    store.add_profile_key("sk-docs", "aayvu", "documents")
    headers = {"Authorization": "Bearer sk-docs"}

    blocked = client.post(
        "/v1/documents/process?service=google/document-ai", headers=headers, content=b"pdf"
    )
    assert blocked.status_code == 403

    store.update_profile("aayvu", {"allowed_providers": ["google_cloud"]})
    unavailable = client.post(
        "/v1/documents/process?service=google/document-ai", headers=headers, content=b"pdf"
    )
    assert unavailable.status_code == 503

    catalog = client.get("/v1/catalog", headers=headers).json()
    assert "metadata" not in catalog["data"][0]


def test_admin_auth_and_single_use_enrollment(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/control/v1/runners").status_code == 401
    issued = enrollment(client)
    assert "curl -fsSL" in issued["install_command"]

    body = {"name": "gpu-one", "model_alias": "local/qwen-coder", "snapshot": snapshot()}
    response = client.post(
        "/control/v1/runners/enroll",
        headers={"Authorization": f"Bearer {issued['token']}"},
        json=body,
    )
    assert response.status_code == 200
    enrolled = response.json()
    assert enrolled["model_alias"] == "local/qwen-coder"
    assert enrolled["websocket_url"].startswith("ws://testserver/")

    reused = client.post(
        "/control/v1/runners/enroll",
        headers={"Authorization": f"Bearer {issued['token']}"},
        json=body,
    )
    assert reused.status_code == 401


def test_enrollment_token_is_bound_to_runner_name(tmp_path):
    client = make_client(tmp_path)
    issued = enrollment(client)
    response = client.post(
        "/control/v1/runners/enroll",
        headers={"Authorization": f"Bearer {issued['token']}"},
        json={"name": "different-runner", "snapshot": snapshot()},
    )
    assert response.status_code == 403

    # An identity mismatch does not burn the token.
    reused = client.post(
        "/control/v1/runners/enroll",
        headers={"Authorization": f"Bearer {issued['token']}"},
        json={"name": "gpu-one", "snapshot": snapshot()},
    )
    assert reused.status_code == 200


def test_runner_connection_updates_availability(tmp_path):
    client = make_client(tmp_path)
    issued = enrollment(client)
    enrolled = client.post(
        "/control/v1/runners/enroll",
        headers={"Authorization": f"Bearer {issued['token']}"},
        json={"name": "gpu-one", "snapshot": snapshot()},
    ).json()
    headers = {"Authorization": f"Bearer {enrolled['runner_token']}"}

    with client.websocket_connect(
        f"/control/v1/runners/{enrolled['runner_id']}/connect", headers=headers
    ) as websocket:
        websocket.send_json({"type": "heartbeat", "snapshot": snapshot()})
        online = client.get(
            "/control/v1/runners", headers={"Authorization": "Bearer admin-test"}
        ).json()
        assert online[0]["status"] == "online"
        assert online[0]["snapshot"]["models"][0]["max_model_len"] == 32768

    offline = client.get(
        "/control/v1/runners", headers={"Authorization": "Bearer admin-test"}
    ).json()
    assert offline[0]["status"] == "offline"
