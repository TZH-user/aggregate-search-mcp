"""融合层测试:URL 规范化 / 去重 / 加权 RRF / 语言检测。"""

from aggregate_search.fusion import canonical_url, detect_language, fuse
from aggregate_search.models import SearchResult

W = {"a": {"zh": 1.0, "en": 1.0}, "b": {"zh": 1.0, "en": 1.0}}


def _mk(engine, urls, title="t"):
    return [
        SearchResult(title=title, url=u, engine=engine, rank=i)
        for i, u in enumerate(urls, start=1)
    ]


def test_canonical_url_strips_tracking():
    assert canonical_url("https://www.Example.com/a/?utm_source=x&id=2#frag") \
        == "https://www.example.com/a?id=2"


def test_canonical_url_keeps_meaningful_params():
    assert canonical_url("https://example.com/search?q=abc") \
        == "https://example.com/search?q=abc"


def test_canonical_url_trailing_slash():
    assert canonical_url("https://example.com/a/b/") == "https://example.com/a/b"
    assert canonical_url("https://example.com/") == "https://example.com/"


def test_detect_language():
    assert detect_language("python 异步编程") == "zh"
    assert detect_language("python async tutorial") == "en"
    assert detect_language("") == "en"


def test_fuse_ranks_cross_engine_hits_first():
    batch_a = _mk("a", ["https://x.com/1", "https://x.com/2", "https://x.com/3"])
    batch_b = _mk("b", ["https://x.com/2", "https://x.com/1", "https://x.com/4"])
    fused = fuse([batch_a, batch_b], lang="en", weights=W)

    assert fused[0].url == "https://x.com/1"  # a#1 + b#2
    assert fused[1].url == "https://x.com/2"  # a#2 + b#1,分数几乎并列
    assert set(fused[0].engines_hit) == {"a", "b"}


def test_fuse_dedups_after_canonicalization():
    batch_a = _mk("a", ["https://x.com/a?utm_source=s"])
    batch_b = _mk("b", ["https://X.com/a/"])
    fused = fuse([batch_a, batch_b], lang="en", weights=W)
    assert len(fused) == 1
    assert set(fused[0].engines_hit) == {"a", "b"}


def test_fuse_merges_longest_content():
    a = SearchResult(title="t", url="https://x.com/1", content="短", engine="a", rank=1)
    b = SearchResult(title="t", url="https://x.com/1", content="这里是一段长得多的正文", engine="b", rank=2)
    fused = fuse([[a], [b]], lang="en", weights=W)
    assert fused[0].content == "这里是一段长得多的正文"


def test_fuse_language_weight_flips_order():
    # 同名次下,中文 query 时 zh 权重高的引擎应胜出
    weights = {"cn": {"zh": 1.5, "en": 0.5}, "en_e": {"zh": 0.5, "en": 1.5}}
    a = SearchResult(title="t", url="https://x.com/cn", engine="cn", rank=1)
    b = SearchResult(title="t", url="https://x.com/en", engine="en_e", rank=1)
    fused_zh = fuse([[a], [b]], lang="zh", weights=weights)
    fused_en = fuse([[SearchResult(**{**a.__dict__})], [SearchResult(**{**b.__dict__})]], lang="en", weights=weights)
    assert fused_zh[0].url == "https://x.com/cn"
    assert fused_en[0].url == "https://x.com/en"


def test_fuse_bonus_multiplies_cross_hits():
    single = _mk("a", ["https://x.com/solo"])
    double = _mk("a", ["https://y.com/pair"]) + _mk("b", ["https://y.com/pair"])
    fused = fuse([single, double], lang="en", weights=W)
    # 单源 rank1 得 1/61;双源各 rank1 得 2/61 再乘 1.25,必然更高
    assert fused[0].url == "https://y.com/pair"
