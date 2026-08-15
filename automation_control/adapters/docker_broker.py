from typing import Any

import httpx


class DockerBrokerReadAdapter:
    """Control-plane client for the isolated, read-only Docker broker."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=10.0)

    async def allowed_containers(self) -> Any:
        return await self._get("/containers")

    async def status(self, name: str) -> Any:
        return await self._get(f"/containers/{name}/status")

    async def health(self, name: str) -> Any:
        return await self._get(f"/containers/{name}/health")

    async def logs(self, name: str, *, tail: int = 100) -> Any:
        return await self._get(f"/containers/{name}/logs", params={"tail": min(max(tail, 1), 500)})

    async def _get(self, path: str, params: dict | None = None) -> Any:
        response = await self.client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

