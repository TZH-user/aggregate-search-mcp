"""Tavily — agent 优化搜索(每月 1000 次免费)。

POST https://api.tavily.com/search
"""

from __future__ import annotations

import httpx

from ..models import SearchResult
from .base import SearchEngine

FRESHNESS_MAP = {"day": "day", "week": "week", "month": "month", "year": "year"}


class TavilyEngine(SearchEngine):
    name = "tavily"
    API = "https://api.tavily.com/search"

    async def search(self, query: str, count: int, freshness=None):
        body: dict = {"query": query, "max_results": count, "search_depth": "basic"}
        if freshness and freshness in FRESHNESS_MAP:
            body["time_range"] = FRESHNESS_MAP[freshness]
        resp = await self.client.post(
            self.API,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = []
        for r in data.get("results", []):
            rows.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": "",
                "content": r.get("content", ""),
                "published_date": r.get("published_date"),
            })
        return self._mk(self.client, rows)
