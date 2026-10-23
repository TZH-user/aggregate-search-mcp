"""失败分类与出网代理探测测试(mock,不出网)。

背景:此前所有引擎失败对外都只是一个引擎名,403 配额耗尽 / 401 key 无效 /
网络不可达长得一模一样,导致"配好了却用不了"时无法自查。
"""

import httpx
import pytest

from aggregate_search import orchestrator


def http_error(status: int, body: dict | str) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://engine.example/search")
    resp = httpx.Response(status, json=body, request=req) if isinstance(body, dict) \
        else httpx.Response(status, text=body, request=req)
    return httpx.HTTPStatusError(str(status), request=req, response=resp)


def test_quota_exhausted():
    exc = http_error(403, {"code": "403", "message": "You do not have enough money or package quota"})
    info = orchestrator.classify_failure(exc)
    assert info["kind"] == "quota_exhausted"
    assert info["status"] == 403
    assert "quota" in info["detail"].lower()
    assert "资源包" in info["hint"]


def test_auth_failed():
    info = orchestrator.classify_failure(http_error(401, {"error": "invalid api key"}))
    assert info["kind"] == "auth_failed"
    assert "key" in info["hint"]


def test_rate_limited():
    assert orchestrator.classify_failure(http_error(429, {"error": "too many"}))["kind"] == "rate_limited"


def test_server_error():
    assert orchestrator.classify_failure(http_error(503, {"error": "down"}))["kind"] == "server_error"


def test_timeout():
    info = orchestrator.classify_failure(httpx.ConnectTimeout("timed out"))
    assert info["kind"] == "timeout"
    assert "AGG_TIMEOUT_S" in info["hint"]


def test_network_unreachable():
    info = orchestrator.classify_failure(httpx.ConnectError("connect failed"))
    assert info["kind"] == "network_unreachable"
    assert "AGG_PROXY" in info["hint"]


def test_unknown_exception():
    info = orchestrator.classify_failure(RuntimeError("boom"))
    assert info["kind"] == "unknown"
    assert info["status"] is None


def test_all_failed_hint_lists_every_engine():
    errors = {
        "bocha": {"kind": "quota_exhausted", "status": 403, "detail": "...", "hint": "配额/余额耗尽"},
        "tavily": {"kind": "network_unreachable", "status": None, "detail": "...", "hint": "网络不可达"},
    }
    hint = orchestrator.all_failed_hint(errors)
    assert "bocha" in hint and "tavily" in hint
    assert "quota_exhausted" in hint


def test_detect_egress_proxy_prefers_explicit(monkeypatch):
    monkeypatch.setattr(orchestrator, "_egress_proxy", None)
    monkeypatch.setattr(orchestrator, "_proxy_usable", lambda p: True)
    monkeypatch.setenv("AGG_PROXY", "http://127.0.0.1:9999")
    assert orchestrator.detect_egress_proxy() == "http://127.0.0.1:9999"


def test_detect_egress_proxy_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(orchestrator, "_egress_proxy", None)
    monkeypatch.setattr(orchestrator, "_proxy_usable", lambda p: True)
    monkeypatch.delenv("AGG_PROXY", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    assert orchestrator.detect_egress_proxy() == "http://127.0.0.1:8888"


def test_detect_egress_proxy_rejects_broken_proxy(monkeypatch):
    """探测到但 TLS 握手失败的代理必须被丢弃,回退直连。

    实测场景:本机 7897 接受 CONNECT 却在 TLS 阶段掐断,而海外源直连可用。
    """
    monkeypatch.setattr(orchestrator, "_egress_proxy", None)
    monkeypatch.setattr(orchestrator, "_proxy_usable", lambda p: False)
    monkeypatch.setenv("AGG_PROXY", "http://127.0.0.1:7897")
    assert orchestrator.detect_egress_proxy() is None


def test_detect_egress_proxy_never_raises(monkeypatch):
    """探测结果只允许是字符串或 None,且必须可重复调用(结果缓存)。"""
    monkeypatch.setattr(orchestrator, "_egress_proxy", None)
    monkeypatch.delenv("AGG_PROXY", raising=False)
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(var, raising=False)
    first = orchestrator.detect_egress_proxy()
    assert first is None or isinstance(first, str)
    assert orchestrator.detect_egress_proxy() == first


async def test_engine_errors_surface_in_result(monkeypatch):
    """全部引擎失败时,返回值里必须带可自查的 error 文案。"""
    from aggregate_search.engines.base import SearchEngine

    class Dead(SearchEngine):
        name = "dead"

        async def search(self, query, count, freshness=None):
            raise httpx.ConnectError("no route")

    orchestrator._unhealthy.clear()
    monkeypatch.setattr(orchestrator, "_select_engines", lambda only=None: [Dead(client=None)])
    out = await orchestrator.aggregate_search("hello")
    assert out["engines_used"] == []
    assert "error" in out
    assert out["engine_errors"]["dead"]["kind"] == "network_unreachable"
