from __future__ import annotations

import uuid
from typing import Any

import httpx

from .config import Settings


class GatewayError(RuntimeError):
    """A LiteLLM call failed or the gateway is not configured."""


class LiteLLMGateway:
    def __init__(self, settings: Settings):
        self.url = settings.litellm_url
        self.control_key = settings.litellm_control_key
        self.internal_url = settings.internal_url

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.control_key)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise GatewayError("LiteLLM gateway is not configured")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.control_key}"}

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.url}{path}", headers=self._headers(), json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise GatewayError(f"LiteLLM {path} returned HTTP {response.status_code}") from error
            return response.json()

    async def _get(self, path: str) -> dict[str, Any] | None:
        """Best-effort GET: returns None on 404 or any failure."""
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(f"{self.url}{path}", headers=self._headers())
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, dict) else None
        except httpx.HTTPError:
            return None

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

    # -- Profiles: LiteLLM Teams (Phase 5) ----------------------------------

    async def sync_team(self, profile: dict[str, Any]) -> str:
        """Create or update the LiteLLM team for a profile. Returns team_id."""
        team_id = profile.get("litellm_team_id")
        payload = {
            "team_alias": f"ai-router:{profile['id']}",
            "models": profile["allowed_models"],
            "max_budget": profile.get("monthly_budget_usd"),
            "tpm_limit": profile.get("tpm_limit"),
            "rpm_limit": profile.get("rpm_limit"),
            "metadata": {
                "ai_router_profile_id": profile["id"],
                "description": profile.get("description", ""),
            },
        }
        if team_id:
            payload["team_id"] = team_id
            await self._post("/team/update", payload)
            return team_id
        created = await self._post("/team/new", payload)
        new_team_id = created.get("team_id") or created.get("id")
        if not new_team_id:
            raise GatewayError("LiteLLM /team/new returned no team_id")
        return str(new_team_id)

    async def delete_team(self, team_id: str | None) -> None:
        if not self.enabled or not team_id:
            return
        await self._post("/team/delete", {"team_ids": [team_id]})

    async def get_team_spend(self, team_id: str) -> dict[str, Any] | None:
        """Team totals from LiteLLM: spend, max_budget. None when unknown."""
        data = await self._get(f"/team/info?team_id={team_id}")
        if data is None:
            return None
        return {
            "spend": float(data.get("spend") or 0.0),
            "max_budget": data.get("max_budget"),
            "models": data.get("models") or [],
        }

    async def get_team_daily_spend(
        self, team_id: str, start_date: str, end_date: str
    ) -> list[dict[str, Any]] | None:
        """Daily spend rows from LiteLLM, or None when the endpoint is unavailable."""
        data = await self._get(
            f"/team/daily/activity?team_ids={team_id}&start_date={start_date}&end_date={end_date}"
        )
        if data is None:
            return None
        results = data.get("results") or data.get("data") or []
        rows = []
        for item in results:
            if isinstance(item, dict) and item.get("date"):
                rows.append(
                    {
                        "day": str(item["date"])[:10],
                        "spend": float(item.get("spend") or 0.0),
                        "prompt_tokens": item.get("prompt_tokens"),
                        "completion_tokens": item.get("completion_tokens"),
                        "requests": int(item.get("api_requests") or item.get("requests") or 0),
                    }
                )
        return rows

    async def create_profile_key(
        self,
        *,
        team_id: str,
        key_alias: str,
        models: list[str],
        max_budget: float | None,
        tpm_limit: int | None,
        rpm_limit: int | None,
        tags: list[str],
    ) -> str:
        """Mint a virtual key scoped to a team. Returns the raw key (shown once)."""
        created = await self._post(
            "/key/generate",
            {
                "team_id": team_id,
                "key_alias": key_alias,
                "models": models,
                "max_budget": max_budget,
                "tpm_limit": tpm_limit,
                "rpm_limit": rpm_limit,
                "tags": tags,
                "metadata": {"ai_router_managed": True},
            },
        )
        key = created.get("key")
        if not key:
            raise GatewayError("LiteLLM /key/generate returned no key")
        return str(key)

    async def list_keys(self) -> list[dict[str, Any]] | None:
        """All virtual keys (for the one-time tag migration). None when unavailable."""
        data = await self._get("/key/list")
        if data is None:
            return None
        keys = data.get("keys") or data.get("data") or []
        return [item for item in keys if isinstance(item, dict)]

    async def list_models(self) -> list[dict[str, Any]]:
        """Return the standard model catalog visible to the control key."""
        data = await self._get("/v1/models")
        if data is None:
            raise GatewayError("LiteLLM /v1/models is unavailable")
        models = data.get("data") or data.get("models") or []
        return [item for item in models if isinstance(item, dict)]

    async def delete_key(self, key_hash: str) -> None:
        """Revoke one LiteLLM virtual key by its stored SHA-256 hash."""
        await self._post("/key/delete", {"keys": [key_hash]})


def _primary_model(snapshot: dict[str, Any]) -> dict[str, Any]:
    models = snapshot.get("models")
    if isinstance(models, list) and models and isinstance(models[0], dict):
        return models[0]
    return {}
