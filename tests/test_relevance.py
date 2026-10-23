"""相关性过滤层测试:词提取 / 覆盖率 / 降权剔除 / 权威域助推 / 开关与保底。"""

from aggregate_search.models import SearchResult
from aggregate_search.relevance import apply_relevance, has_auth_signal, query_terms


def _mk(title, url, score=1.0, snippet="", content=""):
    return SearchResult(title=title, url=url, snippet=snippet, content=content, score=score)


def test_query_terms_mixed_language():
    latin, cjk = query_terms("Model Context Protocol news October 2026")
    assert latin == ["model", "context", "protocol", "news", "october", "2026"]
    assert cjk == []
    latin, cjk = query_terms("Rust 编程语言 官网")
    assert latin == ["rust"]
    assert cjk == ["编程语言"]  # 官网是权威信号词,不进覆盖率分母


def test_query_terms_auth_signal_detection():
    assert has_auth_signal("FastAPI official documentation")
    assert has_auth_signal("博查搜索 官网")
    assert not has_auth_signal("Apple fruit nutrition facts")


def test_word_boundary_rustdesk_not_rust():
    results = [
        _mk("rustdesk Windows版本 rustdesk评测", "https://blog.51cto.com/u_1/138", score=2.0),
        _mk("Rust Programming Language", "https://www.rust-lang.org", score=1.0),
    ]
    out, stats = apply_relevance("Rust 编程语言 官网", results)
    urls = [r.url for r in out]
    assert "https://blog.51cto.com/u_1/138" not in urls  # rustdesk 不含词边界上的 rust
    assert stats["dropped"] == 1


def test_stem_plural_matches():
    results = [
        _mk("Models.com - The Faces of Fashion", "https://models.com", score=2.0),
        _mk("The 2026-07-28 Specification", "https://blog.modelcontextprotocol.io/x",
            snippet="The 2026-07-28 Model Context Protocol specification is out", score=1.0),
    ]
    out, _ = apply_relevance("Model Context Protocol news October 2026", results)
    # models.com 覆盖率 1/6(model 复数命中)被重罚,官方博客全词命中保持原分
    assert out[0].url.endswith("/x")


def test_cjk_bigram_partial_match():
    results = [_mk("智谱AI开放平台 API 服务", "https://docs.bigmodel.cn/cn/api")]
    out, stats = apply_relevance("智谱开放平台 API 文档", results)
    assert len(out) == 1
    assert stats["dropped"] == 0  # 智谱 2-gram 命中,不整串也计命中


def test_auth_boost_lifts_official_domain():
    results = [
        _mk("Introduction to FastAPI", "https://www.geeksforgeeks.org/python/introduction-to-fastapi",
            snippet="FastAPI is a modern Python web framework", score=2.0),
        _mk("FastAPI - FastAPI", "https://fastapi.tiangolo.com",
            snippet="FastAPI is a modern, fast web framework for building APIs", score=1.8),
    ]
    out, stats = apply_relevance("FastAPI official documentation", results)
    assert out[0].url == "https://fastapi.tiangolo.com"  # 官方域被助推到第一
    assert stats["boosted"] == 1


def test_no_boost_without_auth_signal():
    # Apple fruit 查询无权威信号:即使域名含 apple 也不助推(避免把苹果公司顶到水果营养前)
    results = [
        _mk("Apple", "https://www.apple.com", score=1.0),
        _mk("Apples 101: Nutrition Facts", "https://www.healthline.com/nutrition/foods/apples",
            snippet="apple fruit nutrition facts calories fiber", score=0.9),
    ]
    out, stats = apply_relevance("Apple fruit nutrition facts", results)
    assert stats["boosted"] == 0


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("AGG_RELEVANCE", "0")
    results = [_mk("totally unrelated", "https://example.com/x", score=1.0)]
    out, stats = apply_relevance("rust language guide", results)
    assert len(out) == 1 and stats["enabled"] is False


def test_zero_coverage_all_kept_as_safety_net():
    # 全部结果覆盖率为 0(如引擎只回标题且标题撞不上)→ 不剔除,保底返回
    results = [
        _mk("foo", "https://a.com/1", score=1.0),
        _mk("bar", "https://b.com/2", score=0.9),
    ]
    out, stats = apply_relevance("zzz qqq unrelated", results)
    assert len(out) == 2
    assert stats["dropped"] == 0


def test_single_term_query_skips_filter():
    results = [_mk("anything", "https://a.com", score=1.0)]
    out, stats = apply_relevance("rustdesk", results)
    assert len(out) == 1 and stats["terms"] < 2


def test_downweight_not_drop_partial_coverage():
    # 覆盖率 0 < cov < 阈值:降权但保留(沉尾不清零)
    results = [
        _mk("MODEL Definition & Meaning", "https://www.merriam-webster.com/dictionary/model",
            snippet="recent examples from 2026", score=2.0),
        _mk("MCP guide", "https://modelcontextprotocol.io/spec",
            snippet="model context protocol news october 2026", score=1.0),
    ]
    out, stats = apply_relevance("Model Context Protocol news October 2026", results)
    assert len(out) == 2
    assert stats["downweighted"] == 1
    assert out[0].url == "https://modelcontextprotocol.io/spec"
