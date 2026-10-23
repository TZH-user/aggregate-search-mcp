"""结果融合:URL 规范化去重 + 加权 RRF + 跨源命中加分。

RRF 公式: score(d) = Σ_e w_e / (k + rank_e(d))   (k 默认 60, rank 从 1 起)
只用名次不用原始分数,天然规避各引擎分数量纲不一致的问题;
w_e 按 query 语言取该引擎在 config 中的 zh/en 权重。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import SearchResult

RRF_K = 60

# 常见跟踪参数:命中即剥除
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_cid", "utm_reader", "utm_social",
    "spm", "from", "vd", "vd_source", "share_token", "share_source",
    "share_medium", "tt_from", "ref", "referrer", "source", "fbclid",
    "gclid", "igshid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "yclid",
}

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def detect_language(query: str) -> str:
    """粗粒度语言判断:含 CJK 字符即视为中文查询。"""
    if not query:
        return "en"
    cjk = len(_CJK_RE.findall(query))
    return "zh" if cjk / len(query) >= 0.1 else "en"


def canonical_url(url: str) -> str:
    """URL 规范化:小写 scheme/host、去 fragment、剥跟踪参数、去尾部斜杠。"""
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip()
    if not p.netloc:
        return url.strip()

    kept = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS]
    query = urlencode(kept)

    path = p.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", query, ""))


def fuse(
    batches: list[list[SearchResult]],
    lang: str,
    weights: dict[str, dict[str, float]],
    bonus_factor: float = 0.25,
) -> list[SearchResult]:
    """把各引擎结果列表融合为单一排序。

    batches: 每个引擎的有序结果列表(rank 需已填好,1 起)。
    weights: {engine_name: {"zh": w, "en": w}}。
    bonus_factor: 每多命中一个引擎,总分再乘 (1 + bonus_factor)。
    """
    by_url: dict[str, SearchResult] = {}

    for batch in batches:
        for item in batch:
            key = canonical_url(item.url) or item.url
            w = weights.get(item.engine, {}).get(lang, 1.0)
            gain = w / (RRF_K + item.rank)
            if key in by_url:
                acc = by_url[key]
                if item.engine not in acc.engines_hit:
                    acc.engines_hit.append(item.engine)
                acc.score += gain
                # 同链接保留信息量最大的摘要/正文
                if len(item.content) > len(acc.content):
                    acc.content = item.content
                if len(item.snippet) > len(acc.snippet):
                    acc.snippet = item.snippet
                if item.published_date and not acc.published_date:
                    acc.published_date = item.published_date
            else:
                item.url = key
                item.engines_hit = [item.engine]
                item.score = gain
                by_url[key] = item

    fused = sorted(by_url.values(), key=lambda r: r.score, reverse=True)
    for r in fused:
        r.score *= 1 + bonus_factor * (len(r.engines_hit) - 1)
    fused.sort(key=lambda r: r.score, reverse=True)
    return fused
