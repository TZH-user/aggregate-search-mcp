"""智谱 web_search — 国内直连,GLM 生态(约 0.01 元/次)。

POST https://open.bigmodel.cn/api/paas/v4/web_search
"""

from __future__ import annotations

import os

from ..models import SearchResult
from .base import SearchEngine


class ZhipuEngine(SearchEngine):
    name = "zhipu"
    API = "https://open.bigmodel.cn/api/paas/v4/web_search"

    # 官方时间筛选:oneDay/oneWeek/oneMonth/oneYear/noLimit
    FRESHNESS_MAP = {
        "day": "oneDay", "week": "oneWeek",
        "month": "oneMonth", "year": "oneYear", "noLimit": "noLimit",
    }

    async def search(self, query: str, count: int, freshness=None):
        # 官方可选引擎:search_std(基础)/ search_pro(高阶)/ search_pro_sogou / search_pro_quark
        engine_code = os.getenv("ZHIPU_SEARCH_ENGINE", "search_std")
        body: dict = {
            "search_engine": engine_code,
            "search_query": query,
            "count": count,
        }
        if freshness and freshness in self.FRESHNESS_MAP:
            body["search_recency_filter"] = self.FRESHNESS_MAP[freshness]
        resp = await self.client.post(
            self.API,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = []
        for r in data.get("search_result", []):
            rows.append({
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "snippet": r.get("content", ""),
                "content": "",
                "published_date": r.get("publish_date"),
                "source_name": r.get("media"),
            })
        return self._mk(self.client, rows)
