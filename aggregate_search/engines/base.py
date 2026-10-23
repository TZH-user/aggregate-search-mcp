"""引擎适配器基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from ..models import SearchResult

# 统一的时间范围词汇,各引擎自行映射;不支持者忽略
Freshness = str  # "day" | "week" | "month" | "year"


class SearchEngine(ABC):
    name = "base"

    def __init__(self, client: httpx.AsyncClient, api_key: str = "", base_url: str = ""):
        self.client = client
        self.api_key = api_key
        self.base_url = base_url.rstrip("/") if base_url else ""

    @abstractmethod
    async def search(self, query: str, count: int, freshness: Freshness | None = None) -> list[SearchResult]:
        """执行搜索;任何网络/协议错误都应抛异常,由调度层统一捕获。"""

    @staticmethod
    def _mk(client: httpx.AsyncClient, results: list[dict]) -> list[SearchResult]:
        out = []
        for i, r in enumerate(results, start=1):
            out.append(SearchResult(
                title=str(r.get("title", "")).strip(),
                url=str(r.get("url", "")).strip(),
                snippet=str(r.get("snippet", "")).strip(),
                content=str(r.get("content", "")).strip(),
                published_date=r.get("published_date") or None,
                source_name=r.get("source_name") or None,
                engine="",
                rank=i,
            ))
        return out
