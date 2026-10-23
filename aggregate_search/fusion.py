"""结果融合:URL 规范化去重 + 近重复合并 + 加权 RRF + 跨源命中加分。

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
    elif path == "/":
        path = ""  # 根路径 "/" 与空路径必须归一到同一 key,否则同一首页会因引擎是否带尾斜杠而重复占席

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


def merge_near_duplicates(results: list[SearchResult], threshold: float = 0.9) -> list[SearchResult]:
    """近重复合并:同站(www 归一)+ 同路径 + 标题规范相似度 >= threshold 的结果,
    视为同一页面的参数变体(如行情页 ?date= 不同、跳转参数残差),合并为一条——
    保留高分者,并入另一条的 engines_hit,信息字段取更丰富的那个。

    三条件缺一不可:跨站同名标题、同站不同路径(如 /1 与 /3)、同路径不同标题
    都是正常的不同页面,不合并。threshold <= 0 时原样返回。
    """
    if threshold <= 0 or len(results) < 2:
        return results
    import difflib
    import re as _re

    def norm_title(t: str) -> str:
        return "".join(_re.findall(r"[\w]+", (t or "").lower()))

    kept: list[SearchResult] = []
    kept_keys: list[tuple[str, str, str]] = []  # (host, path, norm_title)
    for r in sorted(results, key=lambda x: x.score, reverse=True):
        try:
            parsed = urlparse(r.url or "")
            host = parsed.netloc.lower().removeprefix("www.")
        except ValueError:
            host, parsed = "", None
        path = (parsed.path or "") if parsed else ""
        nt = norm_title(r.title)
        merged = False
        if host and nt:
            for i, (h2, p2, nt2) in enumerate(kept_keys):
                if h2 == host and p2 == path \
                        and difflib.SequenceMatcher(None, nt, nt2).ratio() >= threshold:
                    dup = kept[i]
                    for eng in r.engines_hit:
                        if eng not in dup.engines_hit:
                            dup.engines_hit.append(eng)
                    if len(r.snippet) > len(dup.snippet):
                        dup.snippet = r.snippet
                    if len(r.content) > len(dup.content):
                        dup.content = r.content
                    if r.published_date and not dup.published_date:
                        dup.published_date = r.published_date
                    merged = True
                    break
        if not merged:
            kept.append(r)
            kept_keys.append((host, path, nt))
    return kept


def diversify(results: list[SearchResult], max_per_host: int = 2) -> list[SearchResult]:
    """单站限条(借鉴 SearXNG 分组多样性):同一 host 超过 max_per_host 条的结果
    顺延到列表尾部(不清零不删除),避免"官网条款页/隐私页连占三席"这类单站霸屏。

    max_per_host <= 0 时原样返回。URL 无法解析 host 的结果不受限。
    """
    if max_per_host <= 0:
        return results
    kept: list[SearchResult] = []
    overflow: list[SearchResult] = []
    counts: dict[str, int] = {}
    for r in results:
        try:
            host = urlparse(r.url or "").netloc.lower().removeprefix("www.")
        except ValueError:
            host = ""
        if not host:
            kept.append(r)
            continue
        c = counts.get(host, 0)
        if c < max_per_host:
            counts[host] = c + 1
            kept.append(r)
        else:
            overflow.append(r)
    return kept + overflow
