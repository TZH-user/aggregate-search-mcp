"""融合后相关性过滤:按 query 与结果文本的词覆盖率降权/剔除离题结果,权威域小助推。

动机(2026-10-23 评测,见 eval/):加权 RRF 只看引擎名次、无相关性信号,
对"专有名词+日期"类查询,字面撞词的跑题页(模特网/词典)会混进 top-K(D1 垃圾 3/5);
"X 官网 / official docs"类查询中真正的官方域名排位偏深(B2 官方文档第 5、C3 官网未进 top-5)。

规则:
- 覆盖率 cov = query 词在 title+snippet+content(截断)+url 的词集中命中的比例
  (拉丁词按词边界+轻量复数匹配,因此 rustdesk 不匹配 rust;CJK 短语整串或任意 2-gram 命中)
- 权威信号词(官网/官方/official/文档/documentation/规范/specification…)只用作
  助推触发条件,不计入覆盖率分母——官方页标题常常不含 "official documentation" 字样
- cov = 0 → 剔除(词数 >= 2 才启用;全部会被剔除时放弃剔除,保底返回)
- 0 < cov < MIN_COV → 分数 × PENALTY(默认 0.3,沉到尾部但不清零)
- query 含权威信号词且域名包含任一拉丁查询词(长度>=4)→ 分数 × AUTH_BOOST(默认 1.3)
- AGG_RELEVANCE=0 可整体关闭;阈值均可经 AGG_REL_MIN_COV / AGG_REL_PENALTY / AGG_AUTH_BOOST 调
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from .models import SearchResult

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_SEG_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")

_LATIN_STOP = {
    "the", "a", "an", "of", "for", "in", "on", "to", "is", "are", "and", "or",
    "how", "what", "which", "does", "do", "vs", "best", "top",
}
_CJK_STOP = {"的", "是", "吗", "什么", "怎么", "如何", "与", "和"}

# 权威信号:触发域名助推,但不参与覆盖率(元词,不区分内容)
_LATIN_AUTH = {"official", "documentation", "specification", "homepage", "docs"}
_CJK_AUTH = {"官网", "官方", "文档", "规范"}
_DOMAIN_TERM_MIN_LEN = 4  # 域名助推要求的词长,排除 ai/api/18 这类泛词


def query_terms(query: str) -> tuple[list[str], list[str]]:
    """query → (拉丁词列表, CJK 短语列表),小写、去停用词与权威信号词、去重保序。"""
    latin: list[str] = []
    for w in _WORD_RE.findall(query.lower()):
        if w in _LATIN_STOP or w in _LATIN_AUTH or w in latin:
            continue
        latin.append(w)
    cjk: list[str] = []
    for seg in _CJK_SEG_RE.findall(query):
        if seg in _CJK_STOP or seg in _CJK_AUTH or seg in cjk:
            continue
        cjk.append(seg)
    return latin, cjk


def has_auth_signal(query: str) -> bool:
    low = query.lower()
    return any(s in low for s in (*_LATIN_AUTH, *_CJK_AUTH))


def _stem_forms(term: str) -> set[str]:
    forms = {term}
    if len(term) > 2:
        forms.add(term + "s")
        forms.add(term + "es")
    return forms


def _coverage(latin: list[str], cjk: list[str], tokens: set[str], low_text: str) -> float:
    if not latin and not cjk:
        return 1.0
    hit = total = 0
    for t in latin:
        total += 1
        if tokens & _stem_forms(t):
            hit += 1
    for seg in cjk:
        total += 1
        if seg in low_text:
            hit += 1
        elif len(seg) >= 4 and any(seg[i:i + 2] in low_text for i in range(len(seg) - 1)):
            hit += 1
    return hit / total


def _domain_has_term(latin: list[str], url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    host = host.removeprefix("www.")
    return any(len(t) >= _DOMAIN_TERM_MIN_LEN and t in host for t in latin)


def apply_relevance(
    query: str,
    results: list[SearchResult],
    *,
    min_cov: float | None = None,
    penalty: float | None = None,
    auth_boost: float | None = None,
    enabled: bool | None = None,
) -> tuple[list[SearchResult], dict[str, object]]:
    """对融合后的结果按覆盖率降权/剔除并重排。返回 (新列表, 统计)。"""
    if enabled is None:
        enabled = os.getenv("AGG_RELEVANCE", "1") != "0"
    stats: dict[str, object] = {
        "enabled": enabled, "terms": 0, "dropped": 0, "downweighted": 0, "boosted": 0,
        # 审计留痕:被剔除/降权的结果(标题+URL,截断),供事后复核过滤器没有误杀
        "dropped_items": [], "downweighted_items": [],
    }
    if not enabled or not results:
        return results, stats

    latin, cjk = query_terms(query)
    n_terms = len(latin) + len(cjk)
    stats["terms"] = n_terms
    if n_terms == 0:
        return results, stats

    min_cov = float(os.getenv("AGG_REL_MIN_COV", "0.4")) if min_cov is None else min_cov
    penalty = float(os.getenv("AGG_REL_PENALTY", "0.3")) if penalty is None else penalty
    auth_boost = float(os.getenv("AGG_AUTH_BOOST", "1.3")) if auth_boost is None else auth_boost
    auth_query = has_auth_signal(query)
    # 覆盖率过滤只在词数 >= 2 时有意义(单词查询无法区分跑题);助推不受此限
    use_coverage = n_terms >= 2

    scored: list[tuple[SearchResult, float]] = []
    for r in results:
        text = f"{r.title} {r.snippet} {r.content[:2000]}".lower()
        tokens = set(_WORD_RE.findall(text)) | set(_WORD_RE.findall((r.url or "").lower()))
        scored.append((r, _coverage(latin, cjk, tokens, text)))

    kept = [pair for pair in scored if pair[1] > 0.0]
    # 保底:全覆盖率为 0 时放弃剔除(往往是引擎 snippet 全空的极端情况)
    if not kept or not use_coverage:
        stats["dropped"] = 0
        kept = scored
    else:
        stats["dropped"] = len(scored) - len(kept)
        stats["dropped_items"] = [
            {"title": (r.title or "")[:60], "url": r.url} for r, c in scored if c == 0.0
        ][:10]

    out: list[SearchResult] = []
    for r, cov in kept:
        if use_coverage and cov < min_cov:
            r.score *= penalty
            stats["downweighted"] += 1
            if len(stats["downweighted_items"]) < 10:
                stats["downweighted_items"].append({"title": (r.title or "")[:60], "url": r.url})
        if auth_query and auth_boost > 1.0 and _domain_has_term(latin, r.url):
            r.score *= auth_boost
            stats["boosted"] += 1
        out.append(r)

    out.sort(key=lambda r: r.score, reverse=True)
    return out, stats


# --------------------------------------------------------------------------
# 用户可配的域名偏好规则(借鉴 SearXNG hostnames 插件 / Kagi 域名偏好)
# AGG_DOMAIN_BOOST="python.org,github.com"  命中域名的结果分数 × AGG_DOMAIN_BOOST_FACTOR(默认1.5)
# AGG_DOMAIN_BLOCK="pinterest.*" 风格不支持,只支持精确域名/父域(子域自动匹配)
# --------------------------------------------------------------------------
def _domain_set(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def _host_of(url: str) -> str:
    try:
        return urlparse(url or "").netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def _match_domain(host: str, domains: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def apply_domain_rules(
    results: list[SearchResult],
    *,
    boost: set[str] | None = None,
    block: set[str] | None = None,
    boost_factor: float | None = None,
) -> tuple[list[SearchResult], dict[str, object]]:
    """按用户配置的域名偏好 踢黑/助推。与覆盖率过滤独立:这是显式用户偏好,不是启发式。"""
    boost = _domain_set(os.getenv("AGG_DOMAIN_BOOST")) if boost is None else boost
    block = _domain_set(os.getenv("AGG_DOMAIN_BLOCK")) if block is None else block
    stats: dict[str, object] = {"blocked": 0, "boosted": 0, "blocked_items": [], "boosted_items": []}
    if not boost and not block or not results:
        return results, stats
    boost_factor = float(os.getenv("AGG_DOMAIN_BOOST_FACTOR", "1.5")) if boost_factor is None else boost_factor

    kept: list[SearchResult] = []
    for r in results:
        host = _host_of(r.url)
        if _match_domain(host, block):
            stats["blocked"] += 1
            if len(stats["blocked_items"]) < 10:
                stats["blocked_items"].append({"title": (r.title or "")[:60], "url": r.url})
            continue
        if boost and _match_domain(host, boost):
            r.score *= boost_factor
            stats["boosted"] += 1
            if len(stats["boosted_items"]) < 10:
                stats["boosted_items"].append({"title": (r.title or "")[:60], "url": r.url})
        kept.append(r)
    kept.sort(key=lambda r: r.score, reverse=True)
    return kept, stats
