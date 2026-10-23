"""SearXNG — 自托管聚合兜底(免 key,零成本)。

GET {base}/search?q=...&format=json
"""

from __future__ import annotations

import httpx

from ..models import SearchResult
from .base import SearchEngine

FRESHNESS_MAP = {"day": "day", "week": "week", "month": "month", "year": "year"}


class SearXNGEngine(SearchEngine):
    name = "searxng"

    async def search(self, query: str, count: int, freshness=None):
        params: dict = {"q": query, "format": "json"}
        if freshness and freshness in FRESHNESS_MAP:
            params["time_range"] = FRESHNESS_MAP[freshness]
        resp = await self.client.get(f"{self.base_url}/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        rows = []
        for r in data.get("results", [])[:count]:
            rows.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
                "published_date": r.get("publishedDate"),
            })
        return self._mk(self.client, rows)
