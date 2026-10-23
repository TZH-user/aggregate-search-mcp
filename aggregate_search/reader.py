"""read_url — 抓取并抽取网页正文(搜索→选中的下一步是阅读)。

抽取用 trafilatura(正文抽取的事实标准);网络策略:
先直连(本机实测海外直连可用,国内站直连必需),失败再经出网代理重试一次。
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("aggregate-search")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) aggregate-search-mcp/0.2"


def _max_chars() -> int:
    try:
        return max(500, int(os.getenv("AGG_READ_MAX_CHARS", "8000")))
    except ValueError:
        return 8000


async def _fetch(url: str) -> str:
    """直连优先,失败经出网代理重试一次;返回 HTML 文本。"""
    from .orchestrator import get_client

    headers = {"User-Agent": UA}
    try:
        resp = await get_client(domestic=False).get(url, headers=headers, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except httpx.ProxyError:
        raise
    except Exception as exc:  # noqa: BLE001 — 直连失败换出网客户端(含代理)再试
        if isinstance(exc, httpx.HTTPStatusError):
            raise  # 4xx/5xx 换通道也无意义
        log.info("read_url direct failed (%s), retrying via egress client", type(exc).__name__)
        resp = await get_client(domestic=False).get(url, headers=headers, follow_redirects=True)
        resp.raise_for_status()
        return resp.text


def extract(html: str, url: str) -> dict[str, str]:
    """HTML → {title, content};trafilatura 失败时退化为纯文本剥除。"""
    import trafilatura

    title = ""
    try:
        meta = trafilatura.extract_metadata(html, url)
        if meta:
            title = (meta.title or "").strip()
    except Exception:  # noqa: BLE001 — 元数据失败不影响正文
        pass

    content = trafilatura.extract(
        html, url=url, include_comments=False, include_tables=True,
        favor_recall=True, output_format="markdown",
    )
    if not content:
        # 兜底:剥脚本样式与全部标签,保留可读文本(质量差但不至于空手而归)
        import re

        text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        content = re.sub(r"\s{2,}", "\n", text).strip()
    return {"title": title, "content": (content or "").strip()}


async def read_url(url: str) -> dict[str, object]:
    """抓取并抽取正文。失败返回 {"error": ...},不抛出。"""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return {"error": "url 必须以 http:// 或 https:// 开头"}

    limit = _max_chars()
    try:
        html = await _fetch(url)
    except httpx.TimeoutException:
        return {"error": f"抓取超时: {url}", "url": url}
    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}", "url": url}
    except Exception as exc:  # noqa: BLE001 — 对外只给可读错误
        return {"error": f"抓取失败: {type(exc).__name__}: {exc}"[:160], "url": url}

    page = extract(html, url)
    content = page["content"][:limit]
    return {
        "url": url,
        "title": page["title"],
        "content": content,
        "truncated": len(page["content"]) > limit,
        "content_chars": len(content),
    }
