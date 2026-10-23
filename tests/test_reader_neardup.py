"""reader(读全文)与近重复合并测试。"""

import httpx
import pytest

from aggregate_search import reader
from aggregate_search.fusion import merge_near_duplicates
from aggregate_search.models import SearchResult


# ---------------------------------------------------------------- read_url
_SAMPLE_HTML = """
<html><head><title>测试页面标题</title></head><body>
<nav>导航 导航 导航</nav>
<article><h1>Rust 安装指南</h1>
<p>第一步是安装 rustup 工具链,它是官方推荐的安装方式,支持 Windows、macOS 与 Linux。</p>
<p>安装完成后,运行 rustc --version 确认编译器已就绪,然后就可以开始编写第一个程序了。</p>
</article>
<script>var tracking = "should be removed";</script>
<footer>版权所有</footer></body></html>
"""


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_read_url_extracts_main_content(monkeypatch):
    captured = {}

    async def fake_fetch(url):
        captured["url"] = url
        return _SAMPLE_HTML

    monkeypatch.setattr(reader, "_fetch", fake_fetch)
    out = await reader.read_url("https://example.com/rust-install")
    assert out["url"] == "https://example.com/rust-install"
    assert "Rust" in out["title"]  # trafilatura 取文章标题(h1),比 <title> 更准
    assert "rustup" in out["content"]
    assert "tracking" not in out["content"]  # script 内容被剥除
    assert out["truncated"] is False


async def test_read_url_rejects_non_http():
    out = await reader.read_url("ftp://example.com/x")
    assert "error" in out


async def test_read_url_http_error(monkeypatch):
    async def fake_fetch(url):
        raise httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", url),
            response=httpx.Response(404, request=httpx.Request("GET", url)))

    monkeypatch.setattr(reader, "_fetch", fake_fetch)
    out = await reader.read_url("https://example.com/missing")
    assert out["error"] == "HTTP 404"


async def test_read_url_timeout_error(monkeypatch):
    async def fake_fetch(url):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(reader, "_fetch", fake_fetch)
    out = await reader.read_url("https://slow.example.com")
    assert "超时" in out["error"]


async def test_read_url_truncates(monkeypatch):
    async def fake_fetch(url):
        return "<html><body><p>" + "很长的一段正文。" * 2000 + "</p></body></html>"

    monkeypatch.setattr(reader, "_fetch", fake_fetch)
    monkeypatch.setenv("AGG_READ_MAX_CHARS", "1500")
    out = await reader.read_url("https://example.com/long")
    assert out["truncated"] is True
    assert out["content_chars"] <= 1500


# ---------------------------------------------------------------- 近重复合并
def _mk(title, url, score):
    return SearchResult(title=title, url=url, score=score)


def test_merge_same_host_same_title():
    rs = [
        _mk("苹果(AAPL)最新价格_行情_走势图-东方财富网",
            "https://quote.eastmoney.com/us/AAPL.html?date=2026-10-20", 9.0),
        _mk("苹果(AAPL)最新价格_行情_走势图-东方财富网",
            "http://quote.eastmoney.com/us/AAPL.html?jump_to_web=1", 7.0),
        _mk("其他不同标题", "https://quote.eastmoney.com/us/MSFT.html", 5.0),
    ]
    out = merge_near_duplicates(rs)
    assert len(out) == 2
    assert out[0].score == 9.0  # 保留高分者
    assert out[0].engines_hit == []  # 无引擎信息时不新增


def test_merge_keeps_richer_snippet_and_unions_engines():
    a = _mk("Rust Programming Language", "https://rust-lang.org/?utm=x", 9.0)
    b = _mk("Rust Programming Language", "https://rust-lang.org/?fr=nav", 6.0)
    a.engines_hit, b.engines_hit = ["tavily"], ["serper"]
    b.snippet = "更丰富的摘要内容"
    out = merge_near_duplicates([a, b])
    assert len(out) == 1
    assert sorted(out[0].engines_hit) == ["serper", "tavily"]
    assert out[0].snippet == "更丰富的摘要内容"


def test_no_merge_same_host_different_paths_even_same_title():
    # 同站同标题但路径不同(/1 与 /3)是正常的不同页面,不合并
    rs = [
        _mk("首页", "https://a.com/1", 9.0),
        _mk("首页", "https://a.com/3", 5.0),
    ]
    assert len(merge_near_duplicates(rs)) == 2


def test_no_merge_across_hosts():
    rs = [
        _mk("Same Title Here", "https://a.com/page", 9.0),
        _mk("Same Title Here", "https://b.com/page", 5.0),
    ]
    assert len(merge_near_duplicates(rs)) == 2


def test_no_merge_different_titles_same_host():
    rs = [
        _mk("Rust 安装指南 windows 版", "https://a.com/1", 9.0),
        _mk("Go 语言入门教程", "https://a.com/2", 5.0),
    ]
    assert len(merge_near_duplicates(rs)) == 2


def test_merge_disabled_with_zero_threshold():
    rs = [_mk("Same", "https://a.com/1", 9.0), _mk("Same", "https://a.com/2", 5.0)]
    assert len(merge_near_duplicates(rs, threshold=0)) == 2
