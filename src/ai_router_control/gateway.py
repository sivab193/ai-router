from __future__ import annotations

import uuid
from typing import Any

import httpx

from .config import Settings


class LiteLLMGateway:
    def __init__(self, settings: Settings):
        self.url = settings.litellm_url
        self.control_key = settings.litellm_control_key
        self.internal_url = settings.internal_url

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.control_key)

    async def register(self, runner: dict[str, Any], proxy_token: str) -> str | None:
        if not self.enabled:
            return None
        snapshot = runner["snapshot"]
        model = _primary_model(snapshot)
        max_model_len = model.get("max_model_len")
        model_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-router:{runner['id']}"))
        payload = {
            "model_name": runner["model_alias"],
            "litellm_params": {
                "model": f"openai/{runner['upstream_model']}",
                "api_base": f"{self.internal_url}/runners/{runner['id']}/v1",
                "api_key": proxy_token,
                "stream_timeout": 300,
            },
            "model_info": {
                "id": model_id,
                "ai_router_runner_id": runner["id"],
                "display_name": runner["model_alias"],
                "mode": "chat",
                "max_input_tokens": max_model_len,
                "max_model_len": max_model_len,
                "allowed_fails": 0,
                "cooldown_time": 5,
            },
        }
        headers = {"Authorization": f"Bearer {self.control_key}"}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.url}/model/new", headers=headers, json=payload)
            response.raise_for_status()
            return model_id

    async def unregister(self, model_id: str | None) -> None:
        if not self.enabled or not model_id:
            return
        headers = {"Authorization": f"Bearer {self.control_key}"}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.url}/model/delete", headers=headers, json={"id": model_id}
            )
            if response.status_code not in (200, 404):
                response.raise_for_status()


def _primary_model(snapshot: dict[str, Any]) -> dict[str, Any]:
    models = snapshot.get("models")
    if isinstance(models, list) and models and isinstance(models[0], dict):
        return models[0]
    return {}
