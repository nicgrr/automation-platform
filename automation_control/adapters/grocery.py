from typing import Any

import httpx


class GroceryReadAdapter:
    """Read-only access to existing grocery HTTP endpoints."""

    _PATHS = {
        "status": "/api/status",
        "items": "/api/staples",
        "comparison": "/api/compare",
        "alerts": "/api/alerts",
    }

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=10.0)

    async def get(self, resource: str) -> Any:
        if resource not in self._PATHS:
            raise ValueError("unsupported grocery resource")
        response = await self.client.get(f"{self.base_url}{self._PATHS[resource]}")
        response.raise_for_status()
        return response.json()

