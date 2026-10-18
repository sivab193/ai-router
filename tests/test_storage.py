from __future__ import annotations

import time

from ai_router_control.storage import Store


def test_enrollment_token_is_single_use(tmp_path):
    store = Store(tmp_path / "control.db")
    store.create_enrollment("aienr_secret", "test", time.time() + 60)

    assert store.consume_enrollment("aienr_secret") is True
    assert store.consume_enrollment("aienr_secret") is False
    assert store.consume_enrollment("wrong") is False


def test_runner_secrets_are_hashed_and_status_changes(tmp_path):
    store = Store(tmp_path / "control.db")
    store.create_runner(
        runner_id="run_1",
        name="gpu-1",
        model_alias="local/qwen",
        upstream_model="Qwen/Qwen3",
        runner_token="airun_secret",
        snapshot={"models": [{"id": "Qwen/Qwen3"}]},
    )

    raw = store.get_runner("run_1")
    assert raw is not None
    assert raw["runner_token_hash"] != "airun_secret"
    assert store.authenticate_runner("run_1", "airun_secret") is not None
    assert store.authenticate_runner("run_1", "wrong") is None

    store.mark_online("run_1", "aiprx_secret")
    assert store.authenticate_proxy("run_1", "aiprx_secret") is True
    store.mark_offline("run_1")
    assert store.authenticate_proxy("run_1", "aiprx_secret") is False
    assert store.get_runner("run_1")["status"] == "offline"
