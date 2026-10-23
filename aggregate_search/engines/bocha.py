"""博查 BochaAI — 国内直连,中文强(个人免费计划/约 0.018-0.036 元/次)。

POST https://api.bochaai.com/v1/web-search
响应结构兼容 Bing Web Search v7 风格。
"""

from __future__ import annotations

from ..models import SearchResult
from .base import SearchEngine

FRESHNESS_MAP = {
    "day": "oneDay", "week": "oneWeek",
    "month": "oneMonth", "year": "oneYear", "noLimit": "noLimit",
}


class BochaEngine(SearchEngine):
    name = "bocha"
    API = "https://api.bochaai.com/v1/web-search"

    async def search(self, query: str, count: int, freshness=None):
        body: dict = {"query": query, "count": count, "summary": True}
        if freshness and freshness in FRESHNESS_MAP:
            body["freshness"] = FRESHNESS_MAP[freshness]
        resp = await self.client.post(
            self.API,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        pages = (data.get("data") or {}).get("webPages") or {}
        rows = []
        for r in pages.get("value", []):
            summary = r.get("summary") or ""
            snippet = r.get("snippet") or ""
            rows.append({
                "title": r.get("name", ""),
                "url": r.get("url", ""),
                "snippet": summary or snippet,
                "content": summary,
                "published_date": r.get("datePublished"),
                "source_name": r.get("siteName"),
            })
        return self._mk(self.client, rows)
