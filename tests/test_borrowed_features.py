"""借鉴功能测试:DuckDuckGo HTML 解析 / 每主机限条 / 域名偏好规则 / opt-in 开关。"""

import httpx
import pytest

from aggregate_search import config
from aggregate_search.engines import build_engine
from aggregate_search.fusion import diversify
from aggregate_search.models import SearchResult
from aggregate_search.relevance import apply_domain_rules


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- DuckDuckGo
_DDGC_HTML = """
<div class="result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&amp;rut=abc">
     Example <b>Docs</b> &amp; Guides</a>
  <a class="result__snippet" href="x">The official <b>documentation</b> page</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://plain.org/page">Plain Page</a>
  <a class="result__snippet">No tags here</a>
</div>
"""


async def test_duckduckgo_parses_and_unwraps_uddg():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "html.duckduckgo.com"
        assert request.url.params["q"] == "rust install"
        return httpx.Response(200, text=_DDGC_HTML)

    e = build_engine("duckduckgo", make_client(handler))
    rs = await e.search("rust install", 5)
    assert rs[0].url == "https://example.com/docs"  # uddg 解包
    assert rs[0].title == "Example Docs & Guides"  # 去 tag + 反转义
    assert rs[0].snippet == "The official documentation page"
    assert rs[1].url == "https://plain.org/page"  # 普通链接原样
    assert rs[1].rank == 2


async def test_duckduckgo_raises_on_zero_results():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>captcha or layout change</html>")

    e = build_engine("duckduckgo", make_client(handler))
    with pytest.raises(RuntimeError, match="0 results"):
        await e.search("q", 5)


def test_duckduckgo_opt_in_off_by_default(monkeypatch):
    monkeypatch.delenv("AGG_ENABLE_DUCKDUCKGO", raising=False)
    names = [s.name for s in config.available_specs()]
    assert "duckduckgo" not in names
    monkeypatch.setenv("AGG_ENABLE_DUCKDUCKGO", "1")
    names = [s.name for s in config.available_specs()]
    assert "duckduckgo" in names


# ---------------------------------------------------------------- 每主机限条
def _mk(url, score):
    return SearchResult(title="t", url=url, score=score)


def test_diversify_caps_per_host():
    rs = [
        _mk("https://a.com/1", 9), _mk("https://a.com/2", 8), _mk("https://a.com/3", 7),
        _mk("https://b.com/1", 6), _mk("https://c.com/1", 5),
    ]
    out = diversify(rs, max_per_host=2)
    urls = [r.url for r in out]
    assert urls[:4] == ["https://a.com/1", "https://a.com/2", "https://b.com/1", "https://c.com/1"]
    assert urls[4] == "https://a.com/3"  # 溢出条目沉到尾部,不删除


def test_diversify_www_and_subdomain_distinct():
    rs = [
        _mk("https://www.a.com/1", 9), _mk("https://a.com/2", 8),  # 同站(www 归一)
        _mk("https://docs.a.com/3", 7),  # 子域算不同站
    ]
    out = diversify(rs, max_per_host=2)
    assert [r.url for r in out] == ["https://www.a.com/1", "https://a.com/2", "https://docs.a.com/3"]


def test_diversify_disabled_with_zero():
    rs = [_mk("https://a.com/1", 9), _mk("https://a.com/2", 8), _mk("https://a.com/3", 7)]
    assert diversify(rs, max_per_host=0) == rs


# ---------------------------------------------------------------- 域名偏好规则
def test_domain_rules_block_and_boost():
    rs = [
        _mk("https://spam.example/1", 9),
        _mk("https://docs.python.org/3/", 6),
        _mk("https://news.site/x", 5),
    ]
    out, stats = apply_domain_rules(
        rs, block={"example"}, boost={"python.org"}, boost_factor=2.0)
    urls = [r.url for r in out]
    assert "https://spam.example/1" not in urls  # 父域匹配子域
    assert urls[0] == "https://docs.python.org/3/"  # 6*2 > 5,反超
    assert stats["blocked"] == 1 and stats["boosted"] == 1
    assert stats["blocked_items"][0]["url"] == "https://spam.example/1"


def test_domain_rules_noop_without_config():
    rs = [_mk("https://a.com/1", 1.0)]
    out, stats = apply_domain_rules(rs, boost=set(), block=set())
    assert len(out) == 1 and stats["boosted"] == 0 and stats["blocked"] == 0


def test_domain_rules_read_env(monkeypatch):
    monkeypatch.setenv("AGG_DOMAIN_BOOST", "GitHub.com, stackoverflow.com")
    monkeypatch.setenv("AGG_DOMAIN_BLOCK", "")
    rs = [_mk("https://github.com/rust-lang", 1.4), _mk("https://other.com/x", 2.0)]
    out, stats = apply_domain_rules(rs)  # 大写/空白归一;1.4*1.5=2.1 > 2.0 反超
    assert stats["boosted"] == 1
    assert out[0].url == "https://github.com/rust-lang"
