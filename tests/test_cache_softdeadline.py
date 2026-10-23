"""v5 特性测试:查询缓存(TTL/参数键/失败不缓存/开关)与软截断(慢引擎跳过/保底等待/不拉黑)。"""

import asyncio
import time

import pytest

from aggregate_search import orchestrator
from aggregate_search.engines.base import SearchEngine
from aggregate_search.models import SearchResult


class FakeEngine(SearchEngine):
    def __init__(self, name, rows, delay=0.0, fail=False):
        super().__init__(client=None)
        self.name = name
        self._rows = rows
        self._delay = delay
        self._fail = fail
        self.calls = 0

    async def search(self, query, count, freshness=None):
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail:
            raise RuntimeError("boom")
        return [
            SearchResult(title=f"t{i}", url=u, engine=self.name, rank=i)
            for i, u in enumerate(self._rows[:count], start=1)
        ]


@pytest.fixture(autouse=True)
def clean_state():
    orchestrator._unhealthy.clear()
    orchestrator._cache.clear()
    yield
    orchestrator._unhealthy.clear()
    orchestrator._cache.clear()


def patch_specs(monkeypatch, engines):
    monkeypatch.setattr(orchestrator, "_select_engines", lambda only=None: engines)


# ---------------------------------------------------------------- 查询缓存
async def test_cache_hit_skips_engines(monkeypatch):
    a = FakeEngine("a", ["https://x.com/1"])
    patch_specs(monkeypatch, [a])
    first = await orchestrator.aggregate_search("cache me", max_results=5)
    second = await orchestrator.aggregate_search("cache me", max_results=5)
    assert a.calls == 1
    assert "cached" not in first
    assert second["cached"] is True
    assert second["results"] == first["results"]


async def test_cache_key_respects_params(monkeypatch):
    a = FakeEngine("a", ["https://x.com/1", "https://x.com/2", "https://x.com/3"])
    patch_specs(monkeypatch, [a])
    await orchestrator.aggregate_search("cache me", max_results=5)
    await orchestrator.aggregate_search("cache me", max_results=3)  # 不同参数 → 不同键
    assert a.calls == 2


async def test_cache_ttl_expiry(monkeypatch):
    monkeypatch.setenv("AGG_CACHE_TTL_S", "0.05")
    a = FakeEngine("a", ["https://x.com/1"])
    patch_specs(monkeypatch, [a])
    await orchestrator.aggregate_search("ttl me")
    await asyncio.sleep(0.07)
    out = await orchestrator.aggregate_search("ttl me")
    assert a.calls == 2 and "cached" not in out


async def test_cache_disabled(monkeypatch):
    monkeypatch.setenv("AGG_CACHE_TTL_S", "0")
    a = FakeEngine("a", ["https://x.com/1"])
    patch_specs(monkeypatch, [a])
    await orchestrator.aggregate_search("no cache")
    await orchestrator.aggregate_search("no cache")
    assert a.calls == 2


async def test_failed_response_not_cached(monkeypatch):
    a = FakeEngine("a", ["https://x.com/1"], fail=True)
    patch_specs(monkeypatch, [a])
    first = await orchestrator.aggregate_search("failing q")
    second = await orchestrator.aggregate_search("failing q")
    assert "error" in first and "error" in second
    assert a.calls == 2  # 失败不缓存,下次照常重试


async def test_cache_lru_eviction(monkeypatch):
    a = FakeEngine("a", ["https://x.com/1"])
    patch_specs(monkeypatch, [a])
    orchestrator._cache["old"] = (time.monotonic() + 999, {"results": []})
    for i in range(orchestrator.CACHE_MAX + 1):
        await orchestrator.aggregate_search(f"evict {i}")
    assert "old" not in orchestrator._cache
    assert len(orchestrator._cache) <= orchestrator.CACHE_MAX


# ---------------------------------------------------------------- 软截断
async def test_soft_deadline_skips_slow_engine(monkeypatch):
    monkeypatch.setenv("AGG_SOFT_DEADLINE_S", "0.3")
    fast = FakeEngine("fast", ["https://f.com/1"])
    slow = FakeEngine("slow", ["https://s.com/1"], delay=30)
    patch_specs(monkeypatch, [fast, slow])
    t0 = time.perf_counter()
    out = await orchestrator.aggregate_search("deadline q", max_results=5)
    dt = time.perf_counter() - t0
    assert out["engines_used"] == ["fast"]
    assert out["soft_timed_out"] == ["slow"]
    assert dt < 5  # 不等 30s 的慢引擎
    assert out["results"]


async def test_soft_deadline_all_slow_falls_back_to_full_wait(monkeypatch):
    monkeypatch.setenv("AGG_SOFT_DEADLINE_S", "0.2")
    s1 = FakeEngine("s1", ["https://x.com/1"], delay=1.0)
    s2 = FakeEngine("s2", ["https://x.com/2"], delay=1.0)
    patch_specs(monkeypatch, [s1, s2])
    out = await orchestrator.aggregate_search("all slow", max_results=5)
    # 全部引擎未完成 → 退回完整等待,绝不返回空
    assert sorted(out["engines_used"]) == ["s1", "s2"]
    assert out["soft_timed_out"] == []
    assert out["results"]


async def test_soft_deadline_disabled(monkeypatch):
    monkeypatch.setenv("AGG_SOFT_DEADLINE_S", "0")
    slow = FakeEngine("slow", ["https://s.com/1"], delay=0.5)
    patch_specs(monkeypatch, [slow])
    out = await orchestrator.aggregate_search("no deadline", max_results=5)
    assert out["engines_used"] == ["slow"]
    assert out["soft_timed_out"] == []


async def test_soft_timed_out_engine_not_blacklisted(monkeypatch):
    monkeypatch.setenv("AGG_SOFT_DEADLINE_S", "0.2")
    slow = FakeEngine("slow", ["https://s.com/1"], delay=30)
    fast = FakeEngine("fast", ["https://f.com/1"])
    patch_specs(monkeypatch, [fast, slow])
    await orchestrator.aggregate_search("not unhealthy", max_results=5)
    assert orchestrator._healthy("slow") is True  # 慢≠坏,不进健康黑名单


async def test_combined_cache_and_soft_deadline(monkeypatch):
    """软截断的结果也进缓存:第二次调用直接拿缓存,不再等任何引擎。"""
    monkeypatch.setenv("AGG_SOFT_DEADLINE_S", "0.3")
    fast = FakeEngine("fast", ["https://f.com/1"])
    slow = FakeEngine("slow", ["https://s.com/1"], delay=30)
    patch_specs(monkeypatch, [fast, slow])
    await orchestrator.aggregate_search("combined q", max_results=5)
    out = await orchestrator.aggregate_search("combined q", max_results=5)
    assert out["cached"] is True
    assert fast.calls == 1 and slow.calls == 1
