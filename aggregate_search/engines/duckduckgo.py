"""DuckDuckGo HTML — 免 key 兜底引擎(默认关闭,需 AGG_ENABLE_DUCKDUCKGO=1 显式开启)。

GET https://html.duckduckgo.com/html/?q=... 然后解析静态 HTML:
- 标题/链接:`<a class="result__a" href="...">`
- 摘要:`<a class="result__snippet" ...>`
链接多为跳转壳 `//duckduckgo.com/l/?uddg=<urlencoded>&rut=...`,必须解出 uddg 还原真实 URL。

借鉴自 open-webSearch 的"免 key 爬虫引擎"思路,但只作兜底位:
配额耗尽/其他引擎全挂时还有一路结果;解析器随页面改版可能失效,
失败会走调度层的健康黑名单,不影响其他引擎。
"""

from __future__ import annotations

import html as html_mod
import re
from urllib.parse import parse_qs, unquote, urlparse

from ..models import SearchResult
from .base import SearchEngine

DEFAULT_BASE = "https://html.duckduckgo.com/html"

_LINK_RE = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(_TAG_RE.sub("", s))).strip()


def _unwrap(href: str) -> str:
    """`//duckduckgo.com/l/?uddg=<enc>&rut=...` → 真实 URL;非跳转壳原样返回。"""
    href = html_mod.unescape(href)
    if "uddg=" in href:
        try:
            qs = parse_qs(urlparse(href).query).get("uddg", [])
            if qs:
                return unquote(qs[0])
        except ValueError:
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


class DuckDuckGoEngine(SearchEngine):
    name = "duckduckgo"

    async def search(self, query: str, count: int, freshness=None):
        # html 端点不支持时间过滤,freshness 静默忽略(调度层语义:不支持的引擎自动忽略)
        base = self.base_url or DEFAULT_BASE
        resp = await self.client.get(base, params={"q": query})
        resp.raise_for_status()
        snippets = [g or "" for g in _SNIPPET_RE.findall(resp.text)]
        rows = []
        for i, (href, raw_title) in enumerate(_LINK_RE.findall(resp.text)[:count]):
            rows.append({
                "title": _clean(raw_title),
                "url": _unwrap(href),
                "snippet": _clean(snippets[i]) if i < len(snippets) else "",
            })
        if not rows:
            raise RuntimeError("duckduckgo html parse: 0 results (页面改版或被风控?)")
        return self._mk(self.client, rows)
