"""调度器端到端测试(mock 引擎,不出网)。"""

import pytest

from aggregate_search import orchestrator
from aggregate_search.engines.base import SearchEngine
from aggregate_search.models import SearchResult


class FakeEngine(SearchEngine):
    def __init__(self, name, rows, fail=False):
        super().__init__(client=None)
        self.name = name
        self._rows = rows
        self._fail = fail

    async def search(self, query, count, freshness=None):
        if self._fail:
            raise RuntimeError("boom")
        return [
            SearchResult(title=f"t{i}", url=u, engine=self.name, rank=i)
            for i, u in enumerate(self._rows[:count], start=1)
        ]


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setenv("AGG_CACHE_TTL_S", "0")  # 隔离缓存,防跨测试污染
    orchestrator._unhealthy.clear()
    orchestrator._cache.clear()
    yield
    orchestrator._unhealthy.clear()
    orchestrator._cache.clear()


def patch_specs(monkeypatch, engines):
    monkeypatch.setattr(orchestrator, "_select_engines", lambda only=None: engines)


async def test_aggregates_and_fuses(monkeypatch):
    a = FakeEngine("a", ["https://x.com/1", "https://x.com/2"])
    b = FakeEngine("b", ["https://x.com/2", "https://x.com/3"])
    patch_specs(monkeypatch, [a, b])

    out = await orchestrator.aggregate_search("hello world", max_results=5)
    assert sorted(out["engines_used"]) == ["a", "b"]
    assert out["engines_failed_or_empty"] == []
    assert out["results"][0]["url"] in ("https://x.com/1", "https://x.com/2")
    assert len(out["results"]) == 3


async def test_engine_failure_isolated(monkeypatch):
    a = FakeEngine("a", ["https://x.com/1"], fail=True)
    b = FakeEngine("b", ["https://y.com/1"])
    patch_specs(monkeypatch, [a, b])

    out = await orchestrator.aggregate_search("hello")
    assert out["engines_used"] == ["b"]
    assert out["engines_failed_or_empty"] == ["a"]
    assert orchestrator._healthy("a") is False


async def test_unhealthy_engine_skipped(monkeypatch):
    a = FakeEngine("a", ["https://x.com/1"], fail=True)
    b = FakeEngine("b", ["https://y.com/1"])
    patch_specs(monkeypatch, [a, b])

    await orchestrator.aggregate_search("hello")

    calls = []
    class Spy(FakeEngine):
        async def search(self, query, count, freshness=None):
            calls.append(self.name)
            return await super().search(query, count, freshness)

    b2 = Spy("b", ["https://y.com/1"])
    patch_specs(monkeypatch, [a, b2])
    out = await orchestrator.aggregate_search("hello")
    assert calls == ["b"]  # a 已被健康黑名单跳过
    assert out["engines_used"] == ["b"]


async def test_no_engines_returns_error(monkeypatch):
    patch_specs(monkeypatch, [])
    out = await orchestrator.aggregate_search("hello")
    assert "error" in out


async def test_empty_query(monkeypatch):
    patch_specs(monkeypatch, [FakeEngine("a", ["https://x.com/1"])])
    out = await orchestrator.aggregate_search("   ")
    assert "error" in out


async def test_max_results_truncates(monkeypatch):
    a = FakeEngine("a", [f"https://x.com/{i}" for i in range(10)])
    patch_specs(monkeypatch, [a])
    out = await orchestrator.aggregate_search("hello", max_results=3)
    assert len(out["results"]) == 3
