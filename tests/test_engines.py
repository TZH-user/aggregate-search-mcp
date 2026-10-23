"""引擎适配器解析测试:用 httpx.MockTransport 模拟各引擎真实响应格式。"""

import json

import httpx
import pytest

from aggregate_search.engines import build_engine


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_tavily_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.tavily.com"
        assert request.headers["Authorization"] == "Bearer tk"
        assert json.loads(request.content)["time_range"] == "week"
        return httpx.Response(200, json={"results": [
            {"title": "T1", "url": "https://a.com/1", "content": "body", "published_date": "2026-01-01"},
        ]})

    e = build_engine("tavily", make_client(handler), api_key="tk")
    rs = await e.search("q", 5, freshness="week")
    assert rs[0].engine == ""  # 调度层负责填 engine
    assert rs[0].rank == 1
    assert rs[0].content == "body"
    assert rs[0].published_date == "2026-01-01"


async def test_serper_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "google.serper.dev"
        assert request.headers["X-API-KEY"] == "sk"
        return httpx.Response(200, json={"organic": [
            {"title": "S1", "link": "https://b.com/1", "snippet": "snip", "source": "B", "date": "3 days ago"},
        ]})

    e = build_engine("serper", make_client(handler), api_key="sk")
    rs = await e.search("q", 5)
    assert rs[0].url == "https://b.com/1"
    assert rs[0].snippet == "snip"
    assert rs[0].source_name == "B"


async def test_bocha_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.bochaai.com"
        assert json.loads(request.content)["freshness"] == "oneDay"
        return httpx.Response(200, json={"code": 200, "data": {"webPages": {"value": [
            {"name": "B1", "url": "https://c.com/1", "snippet": "s", "summary": "sum",
             "siteName": "站", "datePublished": "2026-10-01"},
        ]}}})

    e = build_engine("bocha", make_client(handler), api_key="bk")
    rs = await e.search("q", 5, freshness="day")
    assert rs[0].content == "sum"
    assert rs[0].source_name == "站"


async def test_bocha_tolerates_missing_pages():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "data": {}})

    e = build_engine("bocha", make_client(handler), api_key="bk")
    assert await e.search("q", 5) == []


async def test_zhipu_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "open.bigmodel.cn"
        body = json.loads(request.content)
        assert body["search_engine"] == "search_std"
        return httpx.Response(200, json={"search_result": [
            {"title": "Z1", "link": "https://d.com/1", "content": "c", "media": "媒", "publish_date": "2026-09-30"},
        ]})

    e = build_engine("zhipu", make_client(handler), api_key="zk")
    rs = await e.search("q", 5)
    assert rs[0].url == "https://d.com/1"
    assert rs[0].source_name == "媒"


async def test_searxng_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://sx.local/search?q=hello&format=json"
        return httpx.Response(200, json={"results": [
            {"title": "X1", "url": "https://e.com/1", "content": "c"},
            {"title": "X2", "url": "https://e.com/2", "content": "c2"},
            {"title": "X3", "url": "https://e.com/3", "content": "c3"},
        ]})

    e = build_engine("searxng", make_client(handler), base_url="http://sx.local")
    rs = await e.search("hello", 2)
    assert len(rs) == 2  # 截断到 count
    assert rs[0].rank == 1


async def test_http_error_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    e = build_engine("tavily", make_client(handler), api_key="bad")
    with pytest.raises(httpx.HTTPStatusError):
        await e.search("q", 5)
