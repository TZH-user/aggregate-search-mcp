"""Serper.dev — Google SERP($1/千次起)。

POST https://google.serper.dev/search
"""

from __future__ import annotations

from ..models import SearchResult
from .base import SearchEngine


class SerperEngine(SearchEngine):
    name = "serper"
    API = "https://google.serper.dev/search"

    async def search(self, query: str, count: int, freshness=None):
        resp = await self.client.post(
            self.API,
            headers={"X-API-KEY": self.api_key},
            json={"q": query, "num": count},
        )
        resp.raise_for_status()
        data = resp.json()
        rows = []
        for r in data.get("organic", []):
            rows.append({
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "snippet": r.get("snippet", ""),
                "published_date": r.get("date"),
                "source_name": r.get("source"),
            })
        return self._mk(self.client, rows)
